"""
Read-only MCP (Model Context Protocol) adapter for DokuWiki.

This module exposes the *read* surface of a DokuWiki instance to an LLM agent
via the Model Context Protocol. It is deliberately independent of
:class:`dokuwiki_autodoc.autodoc.AutoDocumentation`, which constitutes the
write side of this package (report generation, table appends, image uploads).

All tools return JSON strings. Every output is bounded (O(max_chars) or
O(limit)) so that a large wiki cannot flood an agent's context window:
text is chunked, lists are paginated and media bytes are hard-capped.

Read-only enforcement is layered:

1. Tools only ever reach the wiki through :class:`ReadOnlyDokuWiki`, which
   whitelists read callables and refuses everything else at call time.
2. No tool signature accepts write-shaped parameters (content, data, ...).
3. ``skipacl`` is never exposed and always ``False``; ACL state is *visible*
   through ``get_page_permission`` instead of bypassable.
4. A CI test asserts the exact set of registered tool names.

Configuration (environment variables, set in the MCP client config):

* ``DOKUWIKI_URL`` (required): e.g. ``https://your.wiki/``
* ``DOKUWIKI_USER`` / ``DOKUWIKI_PASSWORD``: empty for open wikis
* ``DOKUWIKI_ALLOW_INVALID_CERTS``: ``"1"``/``"true"`` disables TLS
  verification (dangerous, logs a warning; never a tool parameter)
* ``DOKUWIKI_USE_CERTIFI``: use certifi's trust store

Run with ``dokuwiki-mcp`` (console script, stdio transport).
"""

import base64
import json
import logging
import os
import re
import ssl
import threading
import unittest.mock
from typing import Any

import dokuwiki
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

logger = logging.getLogger("dokuwiki_autodoc.mcp")

# ---------------------------------------------------------------------------
# Bounded-output constants. Every tool output is O(limit) or O(max_chars),
# never O(wiki).
# ---------------------------------------------------------------------------
DEFAULT_TEXT_CHARS = 8000       # default wikitext slice per call
DEFAULT_HTML_CHARS = 20000      # default HTML slice per call
MAX_TEXT_CHARS = 40000          # hard cap a caller may raise max_chars to
MIN_TEXT_CHARS = 200            # hard floor, prevents useless 1-char calls
DEFAULT_LIST_LIMIT = 100        # default page/media list slice
MAX_LIST_ITEMS = 200            # hard cap for limit
MAX_DEPTH = 10                  # hard cap for namespace recursion
MAX_SEARCH_SNIPPET = 500        # per-hit snippet cut (server caps at 15 hits)
MAX_MEDIA_BYTES = 1024 * 1024   # 1 MiB hard cap for media transfers
DEFAULT_MEDIA_BYTES = 65536     # 64 KiB default media transfer cap
STRUCT_RESULT_MAX = 100_000     # struct payload cap before truncation

READONLY_ANNOTATIONS = ToolAnnotations(read_only_hint=True)

# ---------------------------------------------------------------------------
# Read-only wrapper
# ---------------------------------------------------------------------------
# Read callables whitelisted on the root client. ``send`` is deliberately
# excluded although technically a read-capable dispatcher: it could invoke any
# XML-RPC command, including write commands, so it must not be reachable.
_ROOT_READ_METHODS = ("version", "title", "time")
# ...and on each sub-object. Anything else (pages.set/append/delete,
# medias.add/set/delete, structs.save_data, add_acl, del_acl, ...) is refused.
_SUBOBJECT_READ_METHODS = {
    "pages": ("list", "changes", "search", "versions", "info", "get", "html",
              "permission", "links", "backlinks"),
    "medias": ("list", "changes", "info", "get"),
    "structs": ("get_data", "get_schema", "get_aggregation_data"),
}


class ReadOnlyDokuWiki:
    """
    Trust-boundary wrapper around a :class:`dokuwiki.DokuWiki` client.

    ALL attribute access — including dunder and private names — goes through
    the whitelist via ``__getattribute__``, so there is no path that hands out
    the raw, write-capable client. Any non-whitelisted attribute, in
    particular every write method, raises ``ValueError`` at call time.
    """

    _ALLOWED: dict = {
        "": frozenset(_ROOT_READ_METHODS),
        **{obj: frozenset(methods) for obj, methods in _SUBOBJECT_READ_METHODS.items()},
    }

    def __init__(self, client: dokuwiki.DokuWiki):
        allowed = object.__getattribute__(self, "_ALLOWED")
        object.__setattr__(self, "_client", client)
        object.__setattr__(
            self, "_subobjects",
            {name: getattr(client, name) for name in allowed if name})

    def __getattribute__(self, name: str) -> Any:
        if name == "sub":
            return object.__getattribute__(self, name)
        allowed = object.__getattribute__(self, "_ALLOWED")
        if name in allowed[""]:
            client = object.__getattribute__(self, "_client")
            return getattr(client, name)
        if name in allowed:
            message = (f"'{name}' is a namespace object; access its read methods "
                       "via the tool implementations only")
            raise ValueError(message)
        raise ValueError(_not_writeable_message(name))

    def sub(self, name: str) -> Any:
        """
        Return the whitelisted read-only view of the ``pages``/``medias``/
        ``structs`` sub-object. Attribute access on the returned proxy is
        restricted to the same whitelist.
        """
        allowed = object.__getattribute__(self, "_ALLOWED")
        if name not in allowed or not name:
            raise ValueError(_not_writeable_message(name))
        subobjects = object.__getattribute__(self, "_subobjects")
        target = subobjects[name]
        allowed = allowed[name]

        class _SubView:
            def __getattribute__(self, attr: str) -> Any:
                if attr.startswith("__") and attr != "__class__":
                    # Python machinery (repr, weakrefs, ...) must keep working.
                    return object.__getattribute__(self, attr)
                if attr in allowed:
                    return getattr(target, attr)
                raise ValueError(_not_writeable_message(f"{name}.{attr}"))

        return _SubView()


def _not_writeable_message(name: str) -> str:
    return (f"'{name}' is not available: write access is not available in the "
            "read-only MCP adapter")


# ---------------------------------------------------------------------------
# Credential sanitization
# ---------------------------------------------------------------------------
# The dokuwiki lib embeds userinfo credentials into the XML-RPC URL, and
# xmlrpc.client exceptions carry that URL in their text (str/repr). Sanitize
# every URL and exception string before it reaches a log record or an
# agent-visible envelope.
_URLINFO_RE = re.compile(r"(\w+://)([^/\s@]+)@")


def sanitize_text(text: str) -> str:
    """Redact userinfo ('user:password@') from any URL appearing in *text*."""
    return _URLINFO_RE.sub(r"\1<redacted>@", text)


def _sanitize_url(url: str) -> str:
    """Redact userinfo from a configured URL before logging it. Works even on
    malformed URLs (wrong number of slashes etc.), redacting everything
    between 'scheme://' and the first '/' or the end of the authority part."""
    match = re.match(r"(\w+://)(.*)", url)
    if not match:
        return "<unparseable-url>"
    scheme, rest = match.groups()
    authority = rest.split("/", 1)[0]
    if "@" in authority:
        authority = authority.split("@", 1)[1]
    remainder = "/" + rest.split("/", 1)[1] if "/" in rest else ""
    return f"{scheme}{authority}{remainder}"


def sanitize_error(err: BaseException) -> str:
    """Exception text, safe for logs and agent-visible envelopes."""
    return sanitize_text(f"{type(err).__name__}: {err}")


# ---------------------------------------------------------------------------
# TLS transport
# ---------------------------------------------------------------------------
def _unverified_cookies_transport(proto: str):
    """
    Build a cookie-retaining transport that skips TLS certificate validation.

    The dokuwiki lib's own ``CookiesTransport`` cannot carry an SSL context,
    so we reproduce it here with one, scoped to this client only — unlike
    monkeypatching ``ssl.create_default_context``, which mutates the whole
    process and (on current Pythons) is not even honored by xmlrpc's transport.
    """
    from xmlrpc.client import SafeTransport, Transport

    base = Transport if proto == "http" else SafeTransport

    class _UnverifiedCookiesTransport(base):
        def __init__(self):
            super().__init__(
                context=None if proto == "http" else ssl._create_unverified_context())
            self._cookies: dict = {}

        def send_headers(self, connection, headers):
            if self._cookies:
                cookies = (f"{k}={v}" for k, v in self._cookies.items())
                connection.putheader("Cookie", "; ".join(cookies))
            base.send_headers(self, connection, headers)

        def parse_response(self, response):
            try:
                for header in response.msg.get_all("Set-Cookie"):
                    cookie = header.split(";", 1)[0]
                    cookie_key, cookie_value = cookie.split("=", 1)
                    self._cookies[cookie_key] = cookie_value
            except (TypeError, AttributeError):
                pass
            return base.parse_response(self, response)

    return _UnverifiedCookiesTransport()


# ---------------------------------------------------------------------------
# Client management (lazy connect)
# ---------------------------------------------------------------------------
_client: dokuwiki.DokuWiki | None = None
_ro_client: ReadOnlyDokuWiki | None = None
_client_lock = threading.Lock()


def _build_client() -> dokuwiki.DokuWiki:
    """
    Construct the raw dokuwiki client from environment configuration.
    Raises ``RuntimeError`` naming the missing variables (never their values).
    """
    missing = [var for var in ("DOKUWIKI_URL",) if not os.environ.get(var)]
    if missing:
        raise RuntimeError(_missing_env_message(missing))

    url = os.environ["DOKUWIKI_URL"]
    user = os.environ.get("DOKUWIKI_USER", "")
    password = os.environ.get("DOKUWIKI_PASSWORD", "")

    kwargs: dict = {}
    if _env_flag("DOKUWIKI_ALLOW_INVALID_CERTS"):
        logger.warning(
            "TLS certificate validation is DISABLED (DOKUWIKI_ALLOW_INVALID_CERTS "
            "is set). Connections are vulnerable to man-in-the-middle attacks. "
            "DO NOT USE IN PRODUCTION."
        )
        # Pass a transport that carries an unverified SSL context, scoped to
        # this client only. cookieAuth cannot be combined with a custom
        # transport in the dokuwiki lib (it overwrites kwargs['transport']),
        # so we run the login ourselves.
        proto = _url_proto(url)
        kwargs["transport"] = _unverified_cookies_transport(proto)
        kwargs["cookieAuth"] = False
    else:
        kwargs["cookieAuth"] = True
        if _env_flag("DOKUWIKI_USE_CERTIFI"):
            try:
                import certifi
                os.environ["SSL_CERT_FILE"] = certifi.where()
            except ImportError:
                logger.warning(
                    "DOKUWIKI_USE_CERTIFI is set but certifi is not installed; ignoring."
                )

    logger.info("Connecting to wiki at %s", _sanitize_url(url))
    client = dokuwiki.DokuWiki(url, user, password, **kwargs)
    if kwargs.get("cookieAuth") is False:
        if not client.login(user, password):
            message = "invalid login or password!"
            raise dokuwiki.DokuWikiError(message)
    return client


def _url_proto(url: str) -> str:
    match = re.match(r"(\w+)://", url)
    return match.group(1) if match else "https"


def _missing_env_message(missing: list) -> str:
    return (f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set DOKUWIKI_URL (and optionally DOKUWIKI_USER/DOKUWIKI_PASSWORD) "
            "in the MCP client configuration.")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _get_ro_client() -> ReadOnlyDokuWiki:
    """
    Lazily connect on first tool call. Thread-safe (the SDK runs tools in
    worker threads). If a call against the cached client fails with a
    connection error, the cache is dropped so a later retry can re-establish
    the session.
    """
    global _client, _ro_client
    with _client_lock:
        if _ro_client is None:
            client = _build_client()
            _ro_client = ReadOnlyDokuWiki(client)
            _client = client
            try:
                logger.info("Connected to wiki '%s'", _ro_client.title)
            except Exception as err:
                logger.warning("Could not read wiki title: %s", sanitize_error(err))
    return _ro_client


def _invalidate_client() -> None:
    """
    Drop the cached client so the next tool call reconnects. Called when a
    tool fails in a way that suggests the connection (session) went bad.
    """
    global _client, _ro_client
    with _client_lock:
        _client = None
        _ro_client = None


# Exceptions that indicate a dead connection/session rather than a per-request
# answer (a missing page, a bad id). On these, reconnect on the next call.
_CONNECTION_ERROR_TYPES = (ConnectionError, dokuwiki.DokuWikiError)


def _tool_error(tool: str, err: Exception, hint: str = "", page: str = "") -> str:
    """
    Shared error path: log + build the agent-visible envelope, and invalidate
    the cached client if the failure looks like a dead connection/session.
    """
    target = f" (page={page})" if page else ""
    logger.warning("%s failed: %s", tool, sanitize_error(err))
    if isinstance(err, _CONNECTION_ERROR_TYPES) and not isinstance(err, RuntimeError):
        _invalidate_client()
    return _err(f"{tool} failed{target}: {sanitize_error(err)}", hint=hint)


def _set_client_for_tests(client: Any) -> ReadOnlyDokuWiki:
    """Test seam: inject a mock client."""
    global _client, _ro_client
    _client = client
    _ro_client = ReadOnlyDokuWiki(client)
    return _ro_client


def _reset_client() -> None:
    global _client, _ro_client
    _client = None
    _ro_client = None


# ---------------------------------------------------------------------------
# Response shaping helpers
# ---------------------------------------------------------------------------
def _slice_text(text: str, offset: int, max_chars: int) -> dict:
    """
    Chunk *text* into a bounded, machine-followable envelope with
    continuation info, so a large page can be read in successive calls.
    """
    offset = max(0, int(offset))
    max_chars = min(max(int(max_chars), MIN_TEXT_CHARS), MAX_TEXT_CHARS)
    total = len(text)
    chunk = text[offset:offset + max_chars]
    next_offset = offset + max_chars
    has_more = next_offset < total
    return {
        "total_chars": total,
        "offset": offset,
        "returned_chars": len(chunk),
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "text": chunk,
    }


def _slice_list(items: list, offset: int, limit: int) -> dict:
    """
    Paginate *items* into a bounded envelope with continuation info.
    """
    offset = max(0, int(offset))
    limit = min(max(int(limit), 1), MAX_LIST_ITEMS)
    total = len(items)
    page = items[offset:offset + limit]
    next_offset = offset + limit
    has_more = next_offset < total
    return {
        "total": total,
        "offset": offset,
        "returned": len(page),
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "items": page,
    }


def _ok(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _err(message: str, hint: str = "") -> str:
    """
    Recoverable-error envelope. Returned as a normal (non-error) tool result
    so the agent can read and react to it.
    """
    payload = {"error": message}
    if hint:
        payload["hint"] = hint
    return json.dumps(payload, ensure_ascii=False)


def _validate_page_id(page: str, kind: str = "page") -> str | None:
    """Cheap id sanity check before any network call. Returns an error or None."""
    if not page or not page.strip():
        return f"invalid {kind} id: must be a non-empty string"
    if "\x00" in page or ".." in page:
        return f"invalid {kind} id: must not contain NUL or '..'"
    return None


def _not_found_envelope(kind: str, page: str) -> str:
    hint = ("use search or list_pages to find the correct page id"
            if kind == "page"
            else "use list_medias to find the correct media id")
    return _err(f"{kind} '{page}' does not exist", hint=hint)


# ---------------------------------------------------------------------------
# MCP server and tools
# ---------------------------------------------------------------------------
_INSTRUCTIONS = (
    "Read-only access to a DokuWiki. Use search to find pages, get_page for "
    "wikitext (prefer it over get_page_html — HTML is tag noise), and the "
    "list_* tools with offset/limit to explore namespaces. Large responses "
    "are chunked: follow next_offset in the envelope to continue reading. "
    "This server cannot modify the wiki."
)

# Deferred construction: MCPServer.__init__ calls configure_logging, which
# mutates the root logger (basicConfig) — unacceptable at import time for a
# library module. Build with that side effect suppressed; main() configures
# logging itself.
def _build_server() -> MCPServer:
    """Create the MCPServer without touching the root logger."""
    with unittest.mock.patch("mcp.server.mcpserver.server.configure_logging"):
        return MCPServer("dokuwiki", instructions=_INSTRUCTIONS)


mcp: MCPServer = _build_server()


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def wiki_info() -> str:
    """Identify the connected wiki: title, DokuWiki version and server time.
    Also acts as the health check that establishes the connection."""
    try:
        ro = _get_ro_client()
        return _ok({
            "mode": "read-only",
            "title": ro.title,
            "dokuwiki_version": ro.version,
            "server_time": ro.time,
        })
    except Exception as err:
        return _tool_error(
            "wiki_info", err,
            hint=("check DOKUWIKI_URL, DOKUWIKI_USER and DOKUWIKI_PASSWORD in "
                  "the MCP client configuration"))


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def search(query: str) -> str:
    """Full-text search across the wiki (the entry point for any content
    question). Returns at most 15 hits with a snippet each."""
    try:
        results = _get_ro_client().sub("pages").search(query)
    except Exception as err:
        return _tool_error("search", err)
    if not results:
        return _ok({"query": query, "hits": 0, "results": [],
                    "note": "no pages matched the query"})
    trimmed = []
    for raw_hit in results:
        hit = dict(raw_hit) if isinstance(raw_hit, dict) else {"result": str(raw_hit)}
        if isinstance(hit.get("snippet"), str):
            hit["snippet"] = hit["snippet"][:MAX_SEARCH_SNIPPET]
        # Highlight markup only bloats the response; drop it if present.
        hit.pop("highlight", None)
        trimmed.append(hit)
    return _ok({"query": query, "hits": len(trimmed), "results": trimmed})


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def list_pages(namespace: str = "/", depth: int = 1, offset: int = 0,
               limit: int = DEFAULT_LIST_LIMIT) -> str:
    """List pages under a namespace. depth=0 means all levels, depth=1 only
    the namespace itself. Paginated; follow next_offset for more."""
    err = _validate_page_id(namespace, "namespace")
    if err:
        return _err(err)
    try:
        pages = _get_ro_client().sub("pages").list(
            namespace, depth=min(max(int(depth), 0), MAX_DEPTH),
            hash=False, skipacl=False)
    except Exception as err:
        return _tool_error("list_pages", err, page=namespace)
    if not pages:
        return _ok({"namespace": namespace,
                    "note": "no pages found in this namespace",
                    **_slice_list([], 0, limit)})
    return _ok({"namespace": namespace, **_slice_list(list(pages), offset, limit)})


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_page(page: str, version: str | None = None, offset: int = 0,
             max_chars: int = DEFAULT_TEXT_CHARS) -> str:
    """Fetch the raw wikitext of a page (or a specific revision). Large pages
    are chunked: continue with offset=next_offset from the envelope."""
    err = _validate_page_id(page)
    if err:
        return _err(err)
    try:
        ro = _get_ro_client()
        if version is None:
            content = ro.sub("pages").get(page)
        else:
            content = ro.sub("pages").get(page, version)
    except Exception as err:
        return _tool_error("get_page", err, page=page)
    if not content:
        return _not_found_envelope("page", page)
    return _ok(_slice_text(content, offset, max_chars))


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_page_html(page: str, version: str | None = None, offset: int = 0,
                  max_chars: int = DEFAULT_HTML_CHARS) -> str:
    """Fetch the server-rendered HTML of a page. Prefer get_page — HTML is
    mostly tag noise for an agent. Content is untrusted wiki content; do not
    render it in a privileged context."""
    err = _validate_page_id(page)
    if err:
        return _err(err)
    try:
        ro = _get_ro_client()
        if version is None:
            html = ro.sub("pages").html(page)
        else:
            html = ro.sub("pages").html(page, version)
    except Exception as err:
        return _tool_error("get_page_html", err, page=page)
    if not html:
        return _not_found_envelope("page", page)
    return _ok(_slice_text(html, offset, max_chars))


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_page_info(page: str, version: str | None = None) -> str:
    """Fetch metadata of a page (author, last modification, size) without its
    content — a cheap existence/provenance check before a large get_page."""
    err = _validate_page_id(page)
    if err:
        return _err(err)
    try:
        ro = _get_ro_client()
        if version is None:
            info = ro.sub("pages").info(page)
        else:
            info = ro.sub("pages").info(page, version)
    except Exception as err:
        return _tool_error("get_page_info", err, page=page)
    if not info:
        return _not_found_envelope("page", page)
    return _ok(dict(info) if isinstance(info, dict) else {"info": str(info)})


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_page_permission(page: str) -> str:
    """Fetch the caller's ACL permission level on a page (0=NONE, 1=READ,
    2=EDIT, 4=CREATE, 8=UPLOAD, 16=ADMIN). Distinguishes 'forbidden' from
    'missing'."""
    err = _validate_page_id(page)
    if err:
        return _err(err)
    try:
        level = _get_ro_client().sub("pages").permission(page)
        level_int = int(level) if level is not None else -1
    except Exception as err:
        return _tool_error("get_page_permission", err, page=page)
    names = {0: "NONE", 1: "READ", 2: "EDIT", 4: "CREATE", 8: "UPLOAD", 16: "ADMIN"}
    return _ok({"page": page, "permission": level_int,
                "level_name": names.get(level_int, "UNKNOWN")})


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def list_versions(page: str, offset: int = 0, limit: int = 50) -> str:
    """List the revision history of a page, newest first. Paginated; the
    server-side offset walks deep history."""
    err = _validate_page_id(page)
    if err:
        return _err(err)
    try:
        versions = _get_ro_client().sub("pages").versions(page, offset=max(0, int(offset)))
    except Exception as err:
        return _tool_error("list_versions", err, page=page)
    if not versions:
        return _err(f"page '{page}' does not exist or has no revisions "
                    f"(at offset {max(0, int(offset))})",
                    hint="use search or list_pages to find the correct page id")
    # The server returns one bounded window starting at *offset*. To read
    # deeper history, call again with offset += returned. The returned window
    # size (the lib does not accept a count) is reported honestly.
    window = list(versions)
    window_limit = min(max(int(limit), 1), len(window))
    shown = window[:window_limit]
    return _ok({
        "page": page,
        "window_offset": max(0, int(offset)),
        "returned": len(shown),
        "items": shown,
        "note": ("server history window; to see older revisions call again "
                 f"with offset={max(0, int(offset)) + len(shown)}"
                 if len(shown) == len(window) else None),
    })


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def list_changes(since_timestamp: int = 0, offset: int = 0, limit: int = DEFAULT_LIST_LIMIT) -> str:
    """List recent page changes since a unix timestamp (0 = most recent feed).
    Use this to notice what changed on the wiki between sessions. The server
    returns ALL changes in the period oldest-first; if more exist than were
    returned, continue with offset=next_offset."""
    try:
        changes = _get_ro_client().sub("pages").changes(max(0, int(since_timestamp)))
    except Exception as err:
        return _tool_error("list_changes", err)
    if not changes:
        return _ok({"since_timestamp": max(0, int(since_timestamp)),
                    "note": "no changes in this period", "returned": 0, "items": []})
    # wiki.getRecentChanges has no offset/count parameters, but it returns the
    # complete list of changes since the given timestamp, oldest-first, with
    # an INCLUSIVE lower bound (verified against DokuWiki's getRecentsSince,
    # which iterates newest→oldest and array_reverses). A timestamp cursor
    # would therefore re-serve the same entries forever; plain offset
    # pagination over the full response is exact: no duplicates, no data loss.
    # The real server shapes entries as {name, lastModified (ISO8601 dateTime),
    # author, version, perms, size}; 'rev' covers mock/alternate shapes.
    shown = _slice_list(changes, offset, limit)
    if shown["has_more"]:
        shown["note"] = "call again with offset=next_offset to continue"
    return _ok(shown)


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_page_links(page: str) -> str:
    """List all outgoing links of a page (internal and external) — the
    forward-crawl primitive."""
    err = _validate_page_id(page)
    if err:
        return _err(err)
    try:
        links = _get_ro_client().sub("pages").links(page)
    except Exception as err:
        return _tool_error("get_page_links", err, page=page)
    if not links:
        return _ok({"page": page, "links": [], "note": "no links found on this page"})
    return _ok({"page": page, "links": list(links)})


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_backlinks(page: str) -> str:
    """List pages that link TO this page — the reverse-crawl primitive."""
    err = _validate_page_id(page)
    if err:
        return _err(err)
    try:
        backlinks = _get_ro_client().sub("pages").backlinks(page)
    except Exception as err:
        return _tool_error("get_backlinks", err, page=page)
    if not backlinks:
        return _ok({"page": page, "backlinks": [],
                    "note": "no backlinks found for this page"})
    return _ok({"page": page, "backlinks": list(backlinks)})


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def list_medias(namespace: str = "/", depth: int = 1, pattern: str = "",
                offset: int = 0, limit: int = DEFAULT_LIST_LIMIT) -> str:
    """List media (attachment) files under a namespace, metadata only.
    Optional glob pattern filter. Paginated; follow next_offset for more."""
    err = _validate_page_id(namespace, "namespace")
    if err:
        return _err(err)
    try:
        options = {"depth": min(max(int(depth), 0), MAX_DEPTH), "skipacl": False}
        if pattern:
            options["pattern"] = pattern
        medias = _get_ro_client().sub("medias").list(namespace, **options)
    except Exception as err:
        return _tool_error("list_medias", err, page=namespace)
    if not medias:
        return _ok({"namespace": namespace,
                    "note": "no media files found in this namespace",
                    **_slice_list([], 0, limit)})
    return _ok({"namespace": namespace, **_slice_list(list(medias), offset, limit)})


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_media_info(media: str) -> str:
    """Fetch metadata of a media file (size, mtime) without transferring
    bytes — the cheap route for large files."""
    err = _validate_page_id(media, "media")
    if err:
        return _err(err)
    try:
        info = _get_ro_client().sub("medias").info(media)
    except Exception as err:
        return _tool_error("get_media_info", err, page=media)
    if not info:
        return _err(f"media '{media}' does not exist",
                    hint="use list_medias to find the correct media id")
    return _ok(dict(info) if isinstance(info, dict) else {"info": str(info)})


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_media(media: str, max_bytes: int = DEFAULT_MEDIA_BYTES) -> str:
    """Fetch the content of a media file as base64. Refused above max_bytes
    (capped at 1 MiB); check the size with get_media_info first for large
    files."""
    err = _validate_page_id(media, "media")
    if err:
        return _err(err)
    effective_cap = min(max(int(max_bytes), 0), MAX_MEDIA_BYTES)
    try:
        ro = _get_ro_client()
        info = ro.sub("medias").info(media) or {}
        size = info.get("size") if isinstance(info, dict) else None
        if size is not None and int(size) > effective_cap:
            return _media_too_large(media, size, effective_cap)
        raw = ro.sub("medias").get(media)
    except Exception as err:
        return _tool_error("get_media", err, page=media)
    # The dokuwiki lib maps a missing media (fault 121) to {} — no .data
    # attribute. Treat any payload without bytes as not-found.
    data = raw.data if hasattr(raw, "data") else None
    if data is None or len(data) == 0:
        return _err(f"media '{media}' does not exist",
                    hint="use list_medias to find the correct media id")
    if len(data) > effective_cap:
        return _media_too_large(media, len(data), effective_cap)
    encoded = base64.b64encode(data).decode("ascii")
    return _ok({"media": media, "size": len(data), "base64": encoded})


def _media_too_large(media: str, size: int, cap: int) -> str:
    return _err(
        f"media '{media}' is {size} bytes, above the {cap}-byte transfer cap",
        hint=("raise max_bytes (up to 1 MiB) or inspect metadata with "
              "get_media_info"),
    )


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_struct_data(page: str, schema: str = "",
                    timestamp: int | None = None) -> str:
    """Fetch structured data rows (struct plugin) of a page. schema='' returns
    all schemas. Requires the struct plugin on the wiki."""
    err = _validate_page_id(page)
    if err:
        return _err(err)
    try:
        ro = _get_ro_client()
        if timestamp is None:
            data = ro.sub("structs").get_data(page, schema)
        else:
            data = ro.sub("structs").get_data(page, schema, int(timestamp))
    except Exception as err:
        return _struct_error("get_struct_data", page, err)
    if not data:
        return _ok({"page": page, "note": "no struct data found for this page",
                    "data": {}})
    # Truncate structurally: cut the data itself and re-serialize, so the
    # envelope always contains valid JSON (never a sliced-through string).
    payload = json.dumps(data, ensure_ascii=False, default=str)
    if len(payload) > STRUCT_RESULT_MAX:
        if isinstance(data, dict):
            truncated: Any = {}
            for key, value in data.items():
                truncated[key] = value
                if len(json.dumps(truncated, ensure_ascii=False, default=str)) > STRUCT_RESULT_MAX:
                    # This key pushed us over: undo and stop.
                    del truncated[key]
                    break
        elif isinstance(data, list):
            truncated = []
            for item in data:
                truncated.append(item)
                if len(json.dumps(truncated, ensure_ascii=False, default=str)) > STRUCT_RESULT_MAX:
                    del truncated[-1]
                    break
        else:
            truncated = data
        note = (f"struct data exceeds {STRUCT_RESULT_MAX} chars and was "
                "truncated to the first keys/items")
        return _ok({"page": page, "truncated": True, "note": note,
                    "data": truncated})
    return _ok({"page": page, "data": data})


@mcp.tool(annotations=READONLY_ANNOTATIONS, structured_output=False)
def get_struct_schema(schema: str = "") -> str:
    """Fetch field definitions of a struct schema; empty name lists ALL
    schemas on the wiki. Requires the struct plugin."""
    try:
        result = _get_ro_client().sub("structs").get_schema(schema)
    except Exception as err:
        return _struct_error("get_struct_schema", None, err)
    if not result:
        return _ok({"note": "no struct schemas found", "schemas": {}})
    return _ok({"schemas": result})


def _struct_error(tool: str, page: str | None, err: Exception) -> str:
    return _tool_error(tool, err, page=page,
                       hint=("the struct plugin may not be installed on this "
                             "wiki; use get_page for raw wikitext instead"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Console-script entry point: run the read-only DokuWiki MCP server on
    stdio."""
    # Configure logging here, not at import time: as a library module we must
    # not mutate the root logger of an importing process. Output goes to
    # stderr, never stdout (which carries the MCP protocol).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    mcp.run()


if __name__ == "__main__":
    main()
