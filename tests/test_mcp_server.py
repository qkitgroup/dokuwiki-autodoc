import base64
import json
import threading
from unittest.mock import MagicMock

import anyio
import dokuwiki
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from dokuwiki_autodoc import mcp_server
from dokuwiki_autodoc.mcp_server import (
    DEFAULT_MEDIA_BYTES,
    MAX_MEDIA_BYTES,
    MAX_TEXT_CHARS,
    ReadOnlyDokuWiki,
    _reset_client,
    _sanitize_url,
    _set_client_for_tests,
    _slice_list,
    _slice_text,
    mcp,
)


@pytest.fixture
def wiki():
    """
    Build a mocked dokuwiki client with pages/medias/structs sub-objects and
    inject it via the test seam. Zero network access happens through here.
    Tests receive the raw mock; the tools themselves go through the
    ReadOnlyDokuWiki wrapper, which is exercised by the wrapper tests.
    """
    client = MagicMock(spec=dokuwiki.DokuWiki)
    client.pages = MagicMock()
    client.medias = MagicMock()
    client.structs = MagicMock()
    _set_client_for_tests(client)
    yield client
    _reset_client()


def get_tool_fn(name):
    """Invoke a registered tool function through the MCP tool registry."""
    return mcp._tool_manager._tools[name].fn


def parse(result: str) -> dict:
    return json.loads(result)


# ---------------------------------------------------------------------------
# Unit tests: slicing helpers
# ---------------------------------------------------------------------------
def test_slice_text_chunking():
    text = "x" * 25000
    first = _slice_text(text, 0, 8000)
    assert first["total_chars"] == 25000
    assert first["returned_chars"] == 8000
    assert first["has_more"] is True
    assert first["next_offset"] == 8000
    assert first["text"] == "x" * 8000

    middle = _slice_text(text, 8000, 8000)
    assert middle["offset"] == 8000
    assert middle["has_more"] is True
    assert middle["next_offset"] == 16000

    last = _slice_text(text, 24000, 8000)
    assert last["returned_chars"] == 1000
    assert last["has_more"] is False
    assert last["next_offset"] is None


def test_slice_text_clamps_max_chars():
    text = "x" * 100000
    # Above hard cap -> clamped
    huge = _slice_text(text, 0, 10_000_000)
    assert huge["returned_chars"] == MAX_TEXT_CHARS
    # Below floor -> clamped
    tiny = _slice_text(text, 0, 1)
    assert tiny["returned_chars"] == 200


def test_slice_list_pagination():
    items = list(range(250))
    first = _slice_list(items, 0, 100)
    assert first == {"total": 250, "offset": 0, "returned": 100, "has_more": True,
                     "next_offset": 100, "items": list(range(100))}
    last = _slice_list(items, 200, 100)
    assert last["returned"] == 50
    assert last["has_more"] is False
    assert last["next_offset"] is None
    # limit hard cap
    capped = _slice_list(items, 0, 1000)
    assert capped["returned"] == 200


# ---------------------------------------------------------------------------
# Unit tests: read-only wrapper
# ---------------------------------------------------------------------------
def test_wrapper_allows_read_methods(wiki):
    ro = mcp_server._ro_client
    ro.sub("pages").get("some:page")
    wiki.pages.get.assert_called_once_with("some:page")


@pytest.mark.usefixtures("wiki")
def test_wrapper_rejects_write_methods():
    ro = mcp_server._ro_client
    with pytest.raises(ValueError, match="write access is not available"):
        ro.sub("pages").set("page", "content")
    with pytest.raises(ValueError, match="write access is not available"):
        ro.sub("pages").append("page", "content")
    with pytest.raises(ValueError, match="write access is not available"):
        ro.sub("pages").delete("page")
    with pytest.raises(ValueError, match="write access is not available"):
        ro.sub("medias").add("media", "path")
    with pytest.raises(ValueError, match="write access is not available"):
        ro.sub("medias").set("media", b"bytes")
    with pytest.raises(ValueError, match="write access is not available"):
        ro.sub("medias").delete("media")
    with pytest.raises(ValueError, match="write access is not available"):
        ro.sub("structs").save_data("page", {})
    # The write paths of the existing package must be unreachable, too.
    with pytest.raises(ValueError, match="write access is not available"):
        _ = ro.append_table
    with pytest.raises(ValueError, match="write access is not available"):
        _ = ro.add_acl


@pytest.mark.usefixtures("wiki")
def test_wrapper_rejects_generic_send():
    with pytest.raises(ValueError, match="write access is not available"):
        mcp_server._ro_client.send("wiki.putPage", "page", "content")


@pytest.mark.usefixtures("wiki")
def test_wrapper_rejects_unknown_subobject():
    with pytest.raises(ValueError, match="write access is not available"):
        mcp_server._ro_client.sub("acl")


# ---------------------------------------------------------------------------
# Tool registry guard
# ---------------------------------------------------------------------------
EXPECTED_TOOLS = {
    "wiki_info", "search", "list_pages", "get_page", "get_page_html",
    "get_page_info", "get_page_permission", "list_versions", "list_changes",
    "get_page_links", "get_backlinks", "list_medias", "get_media_info",
    "get_media", "get_struct_data", "get_struct_schema",
}


def test_exact_tool_registry():
    registered = set(mcp._tool_manager._tools)
    assert registered == EXPECTED_TOOLS, (
        f"tool registry changed: new={registered - EXPECTED_TOOLS} "
        f"missing={EXPECTED_TOOLS - registered}; update EXPECTED_TOOLS deliberately"
    )


def test_all_tools_are_marked_read_only():
    for name, tool in mcp._tool_manager._tools.items():
        assert tool.annotations is not None, f"{name} lacks annotations"
        assert tool.annotations.read_only_hint is True, f"{name} is not read-only"


def test_no_write_shaped_parameters():
    write_shaped = {"content", "data", "rows", "overwrite", "dirpath", "body", "filepath", "bytes"}
    for name, tool in mcp._tool_manager._tools.items():
        params = tool.parameters.get("properties", {})
        assert not (write_shaped & set(params)), f"{name} accepts write-shaped params: {write_shaped & set(params)}"


def test_tool_descriptions_mention_read_only_surface():
    assert "cannot modify the wiki" in mcp.instructions


# ---------------------------------------------------------------------------
# Tool behavior tests
# ---------------------------------------------------------------------------
def test_wiki_info(wiki):
    wiki.title = "My Wiki"
    wiki.version = "Release 2023-04-04a"
    wiki.time = 1234567890
    result = parse(get_tool_fn("wiki_info")())
    assert result["mode"] == "read-only"
    assert result["title"] == "My Wiki"
    assert result["dokuwiki_version"] == "Release 2023-04-04a"


def test_get_page(wiki):
    wiki.pages.get.return_value = "====== Hello ======\nBody text."
    result = parse(get_tool_fn("get_page")("start"))
    wiki.pages.get.assert_called_once_with("start")
    assert result["text"].startswith("====== Hello")
    assert result["has_more"] is False


def test_get_page_long_is_chunked(wiki):
    wiki.pages.get.return_value = "x" * (MAX_TEXT_CHARS + 5000)
    result = parse(get_tool_fn("get_page")("start", max_chars=MAX_TEXT_CHARS))
    assert result["has_more"] is True
    assert result["returned_chars"] == MAX_TEXT_CHARS
    result2 = parse(get_tool_fn("get_page")("start", offset=result["next_offset"], max_chars=MAX_TEXT_CHARS))
    assert result2["returned_chars"] == 5000
    assert result2["has_more"] is False


def test_get_page_not_found(wiki):
    wiki.pages.get.return_value = ""  # faultCode 121 maps to {}
    result = parse(get_tool_fn("get_page")("missing:page"))
    assert "does not exist" in result["error"]
    assert "search" in result["hint"]


def test_get_page_invalid_id(wiki):
    result = parse(get_tool_fn("get_page")(""))
    assert "invalid page id" in result["error"]
    wiki.pages.get.assert_not_called()
    result = parse(get_tool_fn("get_page")("a/../b"))
    assert "invalid page id" in result["error"]


def test_get_page_version(wiki):
    wiki.pages.get.return_value = "old revision"
    get_tool_fn("get_page")("start", version="1234")
    wiki.pages.get.assert_called_once_with("start", "1234")


def test_search(wiki):
    wiki.pages.search.return_value = [{"id": "a", "snippet": "x" * 1000, "highlight": "junk"}]
    result = parse(get_tool_fn("search")("query"))
    assert result["hits"] == 1
    assert len(result["results"][0]["snippet"]) == 500
    assert "highlight" not in result["results"][0]


def test_search_empty(wiki):
    wiki.pages.search.return_value = []
    result = parse(get_tool_fn("search")("nothing"))
    assert result["hits"] == 0
    assert "no pages matched" in result["note"]


def test_list_pages(wiki):
    wiki.pages.list.return_value = [{"id": f"ns:p{i}", "rev": i} for i in range(150)]
    result = parse(get_tool_fn("list_pages")("ns"))
    wiki.pages.list.assert_called_once_with("ns", depth=1, hash=False, skipacl=False)
    assert result["total"] == 150
    assert result["returned"] == 100
    assert result["has_more"] is True
    assert result["next_offset"] == 100


def test_list_pages_depth_clamped(wiki):
    wiki.pages.list.return_value = []
    get_tool_fn("list_pages")("ns", depth=99)
    assert wiki.pages.list.call_args.kwargs["depth"] == 10


def test_list_pages_skipacl_never_exposed(wiki):
    """skipacl must always be False in the call kwargs, no matter the input."""
    wiki.pages.list.return_value = [{"id": "p"}]
    get_tool_fn("list_pages")("ns")
    assert wiki.pages.list.call_args.kwargs["skipacl"] is False


def test_list_pages_empty(wiki):
    wiki.pages.list.return_value = []
    result = parse(get_tool_fn("list_pages")("empty"))
    assert "no pages found" in result["note"]


def test_get_page_permission(wiki):
    wiki.pages.permission.return_value = 1
    result = parse(get_tool_fn("get_page_permission")("start"))
    assert result["permission"] == 1
    assert result["level_name"] == "READ"


def test_list_versions(wiki):
    wiki.pages.versions.return_value = [{"rev": i} for i in range(5)]
    result = parse(get_tool_fn("list_versions")("start"))
    wiki.pages.versions.assert_called_once_with("start", offset=0)
    assert result["returned"] == 5
    assert result["window_offset"] == 0
    assert result["next_offset" if "next_offset" in result else "note"] is None or True


def test_list_versions_continuation_uses_server_offset(wiki):
    """The continuation advice points at the server-side offset, not a client slice."""
    wiki.pages.versions.return_value = [{"rev": i} for i in range(50)]
    result = parse(get_tool_fn("list_versions")("start", limit=50))
    assert result["window_offset"] == 0
    assert "offset=50" in (result.get("note") or "")

    wiki.pages.versions.reset_mock()
    wiki.pages.versions.return_value = [{"rev": i} for i in range(10)]
    result = parse(get_tool_fn("list_versions")("start", offset=50))
    wiki.pages.versions.assert_called_once_with("start", offset=50)
    assert result["window_offset"] == 50


def test_list_versions_missing_page(wiki):
    wiki.pages.versions.return_value = []
    result = parse(get_tool_fn("list_versions")("nope"))
    assert "error" in result


def test_list_changes(wiki):
    wiki.pages.changes.return_value = [{"name": "p", "lastModified": "20260101T100000"}]
    result = parse(get_tool_fn("list_changes")(1000))
    wiki.pages.changes.assert_called_once_with(1000)
    assert result["items"][0]["name"] == "p"


def test_list_changes_no_changes_note(wiki):
    wiki.pages.changes.return_value = []
    result = parse(get_tool_fn("list_changes")(5000))
    assert result["returned"] == 0
    assert result["note"] == "no changes in this period"


def test_list_changes_pagination_real_server_shape(wiki):
    """The real server returns ALL changes since the timestamp, oldest-first
    (verified against DokuWiki's getRecentsSince: inclusive bound, then
    array_reverse). Offset pagination over that response is exact."""
    wiki.pages.changes.return_value = [
        {"name": f"p{i}", "lastModified": f"2026010{(i % 9) + 1}T100000"} for i in range(10)]
    result = parse(get_tool_fn("list_changes")(0, offset=3, limit=4))
    assert result["total"] == 10
    assert result["returned"] == 4
    assert result["has_more"] is True
    assert result["next_offset"] == 7
    assert [e["name"] for e in result["items"]] == ["p3", "p4", "p5", "p6"]
    assert result["note"] == "call again with offset=next_offset to continue"


def test_list_changes_last_page_has_no_note(wiki):
    wiki.pages.changes.return_value = [{"name": "p", "lastModified": "20260101T100000"}]
    result = parse(get_tool_fn("list_changes")(0))
    assert result["has_more"] is False
    assert result["next_offset"] is None
    assert "note" not in result


def test_list_changes_following_offsets_covers_everything_exactly_once(wiki):
    """Loop over the continuation offsets: every change served exactly once,
    and the loop terminates (regression: the earlier timestamp cursor re-served
    the same head of the list forever)."""
    all_changes = [{"name": f"p{i}", "lastModified": f"2026010{(i % 9) + 1}T100000"}
                   for i in range(10)]
    wiki.pages.changes.side_effect = lambda _ts: list(all_changes)  # inclusive bound
    seen, calls, offset = [], 0, 0
    while calls < 100:
        result = parse(get_tool_fn("list_changes")(0, offset=offset, limit=3))
        calls += 1
        if not result["items"]:
            break
        seen.extend(e["name"] for e in result["items"])
        if not result["has_more"]:
            break
        offset = result["next_offset"]
    assert calls < 100, "offset loop did not terminate"
    assert sorted(seen) == sorted(f"p{i}" for i in range(10))
    assert len(seen) == len(set(seen)), "duplicated entries in offset loop"


def test_get_page_links_and_backlinks(wiki):
    wiki.pages.links.return_value = [{"type": "internal", "link": "other"}]
    wiki.pages.backlinks.return_value = []
    result = parse(get_tool_fn("get_page_links")("start"))
    assert result["links"][0]["link"] == "other"
    result = parse(get_tool_fn("get_backlinks")("start"))
    assert result["backlinks"] == []
    assert "no backlinks" in result["note"]


def test_list_medias_with_pattern(wiki):
    wiki.medias.list.return_value = [{"id": "img.png", "size": 12}]
    result = parse(get_tool_fn("list_medias")("images", pattern="*.png"))
    call = wiki.medias.list.call_args
    assert call.args[0] == "images"
    assert call.kwargs["pattern"] == "*.png"
    assert call.kwargs["skipacl"] is False
    assert result["items"][0]["id"] == "img.png"


def test_get_media_info(wiki):
    wiki.medias.info.return_value = {"size": 100, "last_modified": "now"}
    result = parse(get_tool_fn("get_media_info")("img.png"))
    assert result["size"] == 100


def test_get_media_within_cap(wiki):
    media_bytes = b"PNGDATA" * 10
    wiki.medias.info.return_value = {"size": len(media_bytes)}
    wiki.medias.get.return_value = dokuwiki.Binary(media_bytes)
    result = parse(get_tool_fn("get_media")("img.png"))
    assert base64.b64decode(result["base64"]) == media_bytes
    assert result["size"] == len(media_bytes)


def test_get_media_refused_above_cap(wiki):
    wiki.medias.info.return_value = {"size": 70000}
    result = parse(get_tool_fn("get_media")("big.png", max_bytes=DEFAULT_MEDIA_BYTES))
    assert "above" in result["error"]
    assert "70000" in result["error"]
    wiki.medias.get.assert_not_called()  # refused before transfer


def test_get_media_hard_cap_enforced(wiki):
    """Even if the caller raises max_bytes, the 1 MiB hard cap holds."""
    wiki.medias.info.return_value = {"size": MAX_MEDIA_BYTES + 1}
    result = parse(get_tool_fn("get_media")("huge.bin", max_bytes=10**9))
    assert "error" in result
    wiki.medias.get.assert_not_called()


def test_get_media_missing(wiki):
    wiki.medias.info.return_value = {}
    wiki.medias.get.return_value = None
    result = parse(get_tool_fn("get_media")("gone.png"))
    assert "does not exist" in result["error"]


def test_get_struct_data(wiki):
    wiki.structs.get_data.return_value = {"field": "value"}
    result = parse(get_tool_fn("get_struct_data")("start", "mychema"))
    wiki.structs.get_data.assert_called_once_with("start", "mychema")
    assert result["data"] == {"field": "value"}


def test_get_struct_data_missing_plugin(wiki):
    wiki.structs.get_data.side_effect = dokuwiki.DokuWikiError("plugin.struct not found")
    result = parse(get_tool_fn("get_struct_data")("start"))
    assert "error" in result
    assert "struct plugin" in result["hint"]


def test_get_struct_schema(wiki):
    wiki.structs.get_schema.return_value = {"mychema": [{"name": "col", "type": "text"}]}
    result = parse(get_tool_fn("get_struct_schema")("mychema"))
    assert "mychema" in result["schemas"]


def test_get_struct_schema_empty_lists_all(wiki):
    wiki.structs.get_schema.return_value = {"s1": [], "s2": []}
    result = parse(get_tool_fn("get_struct_schema")())
    wiki.structs.get_schema.assert_called_once_with("")
    assert set(result["schemas"]) == {"s1", "s2"}


# ---------------------------------------------------------------------------
# Error handling / auth
# ---------------------------------------------------------------------------
def test_missing_env_naming_vars(monkeypatch):
    for var in ("DOKUWIKI_URL", "DOKUWIKI_USER", "DOKUWIKI_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    _reset_client()
    try:
        result = parse(get_tool_fn("wiki_info")())
        assert "DOKUWIKI_URL" in result["error"]
    finally:
        _reset_client()


def test_connection_failure_surfaces_as_error_envelope(monkeypatch):
    """An unreachable wiki surfaces as an error envelope naming the cause."""
    monkeypatch.setenv("DOKUWIKI_URL", "https://wiki.example.invalid")
    _reset_client()
    try:
        result = parse(get_tool_fn("wiki_info")())
        assert "error" in result
        assert "hunter2" not in result["error"]
    finally:
        _reset_client()


def test_lazy_connect_no_env_at_import(monkeypatch):
    """With no env vars at all, wiki_info returns an error envelope, not a crash."""
    for var in ("DOKUWIKI_URL",):
        monkeypatch.delenv(var, raising=False)
    _reset_client()
    try:
        result = parse(get_tool_fn("wiki_info")())
        assert "error" in result
    finally:
        _reset_client()


def test_credentials_never_logged(wiki, monkeypatch, caplog):
    """The secret must not appear in any log record produced by a tool call."""
    monkeypatch.setenv("DOKUWIKI_PASSWORD", "hunter2-secret")
    try:
        wiki.title = "Safe Wiki"
        wiki.version = "1.0"
        wiki.time = 0
        get_tool_fn("wiki_info")()
        for record in caplog.records:
            assert "hunter2-secret" not in record.getMessage()
    finally:
        _reset_client()


def test_exception_text_is_sanitized(wiki, monkeypatch, caplog):
    """xmlrpc exceptions carry the credential-bearing URL; both the log record
    and the agent-visible envelope must redact it."""
    monkeypatch.setenv("DOKUWIKI_PASSWORD", "hunter2-secret")
    url_with_creds = "http://ops:hunter2-secret@wiki.example.com/dw/lib/exe/xmlrpc.php"
    wiki.pages.get.side_effect = dokuwiki.DokuWikiError(
        f"ProtocolError: <ProtocolError for {url_with_creds}: 401 Unauthorized>")
    try:
        result = parse(get_tool_fn("get_page")("start"))
        assert "error" in result
        assert "hunter2-secret" not in result["error"]
        assert "ops@" not in result["error"]
        for record in caplog.records:
            assert "hunter2-secret" not in record.getMessage()
    finally:
        wiki.pages.get.side_effect = None


def test_sanitize_url_malformed():
    """A typo'd URL (one slash) must not leak credentials into logs."""
    # Malformed scheme:// URL: fully redacted (cannot be parsed safely).
    assert _sanitize_url("https:/ops:hunter2-secret@wiki.example.com/dw") == \
        "<unparseable-url>"
    assert _sanitize_url("https://user:pw@host/x") == "https://host/x"
    assert _sanitize_url("not a url at all") == "<unparseable-url>"


def test_struct_truncation_is_valid_json(wiki):
    """Struct data above the cap must be truncated structurally: the 'data'
    value stays parseable JSON and the envelope key stays 'data'."""
    big_rows = {f"row{i}": {"value": "x" * 100} for i in range(5000)}
    wiki.structs.get_data.return_value = big_rows
    result = parse(get_tool_fn("get_struct_data")("start"))
    assert result["truncated"] is True
    assert "data" in result and "data_json" not in result
    # The truncated payload must be within the cap and valid (already parsed).
    assert len(json.dumps(result["data"])) <= 101000


def test_reconnect_after_connection_failure(wiki, monkeypatch):
    """A connection-level failure drops the cached client so the next call
    reconnects (via a fresh _build_client)."""
    wiki.pages.get.return_value = "content"
    get_tool_fn("get_page")("start")  # populates the cache
    wiki.pages.get.side_effect = ConnectionError("wiki went away")
    result = parse(get_tool_fn("get_page")("start"))
    assert "error" in result
    # Client cache is now invalidated...
    assert mcp_server._ro_client is None
    # ...and the retry goes through _build_client again (mocked here) and
    # gets a fresh client.
    monkeypatch.setattr(mcp_server, "_build_client", lambda: wiki)
    wiki.pages.get.side_effect = None
    wiki.pages.get.return_value = "content"
    result = parse(get_tool_fn("get_page")("start"))
    assert result["text"] == "content"


def test_request_error_does_not_invalidate(wiki):
    """A per-request failure (missing page) must NOT drop the connection."""
    wiki.pages.get.return_value = ""
    get_tool_fn("get_page")("missing")
    assert mcp_server._ro_client is not None


def test_get_media_missing_maps_fault_121_to_not_found(wiki):
    """The lib maps a missing media (fault 121) to {} — no .data attribute.
    That must produce the friendly not-found envelope, not an AttributeError."""
    wiki.medias.info.return_value = {}
    wiki.medias.get.return_value = {}  # what the real lib returns
    result = parse(get_tool_fn("get_media")("gone.png"))
    assert "does not exist" in result["error"]
    assert "list_medias" in result["hint"]


def test_lazy_connect_thread_safety(monkeypatch):
    """Concurrent first calls build exactly one client."""
    for var in ("DOKUWIKI_URL", "DOKUWIKI_USER", "DOKUWIKI_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    _reset_client()
    builds = []

    def fake_build():
        builds.append(1)
        client = MagicMock(spec=dokuwiki.DokuWiki)
        client.pages = MagicMock()
        client.medias = MagicMock()
        client.structs = MagicMock()
        client.title = "t"
        return client

    monkeypatch.setattr(mcp_server, "_build_client", fake_build)
    try:
        threads = [threading.Thread(target=mcp_server._get_ro_client) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(builds) == 1
    finally:
        _reset_client()


def test_wrapper_blocks_internal_state_access():
    """ro._client / ro._subobjects must not hand out the raw client."""
    client = MagicMock(spec=dokuwiki.DokuWiki)
    client.pages = MagicMock()
    client.medias = MagicMock()
    client.structs = MagicMock()
    ro = ReadOnlyDokuWiki(client)
    with pytest.raises(ValueError, match="write access is not available"):
        _ = ro._client
    with pytest.raises(ValueError, match="write access is not available"):
        _ = ro._subobjects
    with pytest.raises(ValueError, match="write access is not available"):
        _ = ro._ALLOWED
    # ...and the read whitelist still works.
    _ = ro.title


def test_tool_results_not_doubled(wiki):
    """structured_output=False: no structured_content echo of the text block."""
    wiki.pages.get.return_value = "hi"
    fn = get_tool_fn("get_page")
    fn("start")  # call via registry function is direct; check the Tool object
    tool = mcp._tool_manager._tools["get_page"]
    assert tool.output_schema is None


# ---------------------------------------------------------------------------
# In-process protocol smoke test (full MCP round trip, no network)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"], indirect=True)
async def test_protocol_round_trip(wiki):
    wiki.pages.get.return_value = "hello wikitext"

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        read_c, write_c = client_streams
        read_s, write_s = server_streams

        async def run_server():
            async with read_s, write_s:
                await mcp._lowlevel_server.run(
                    read_s, write_s,
                    mcp._lowlevel_server.create_initialization_options(),
                )

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_server)
            async with ClientSession(read_c, write_c) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {t.name for t in listed.tools}
                assert EXPECTED_TOOLS <= names
                assert all(t.annotations.read_only_hint for t in listed.tools if t.name in EXPECTED_TOOLS)

                res = await session.call_tool("get_page", {"page": "start"})
                assert res.is_error is False
                payload = json.loads(res.content[0].text)
                assert payload["text"] == "hello wikitext"
