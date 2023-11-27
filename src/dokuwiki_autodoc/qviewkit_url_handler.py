import os
import sys
import re
from pathlib import Path
from queue import PriorityQueue
from typing import Optional, Tuple

import qkit
from parsita.util import constant
from qkit.gui.qviewkit.main import main
import logging
from parsita import *
import time


class QviewkitURLParser(ParserContext):
    """
    Parses a qviewkit url into its components. It consists out of a prefix, and a UUID.

    The UUID may be optionally be followed by a `?`, and a `&` separated `key=value` list.
    """

    HINT_ARGUMENT = "hint"

    _qviewkit_prefix = lit('qviewkit://')
    _argument_name = reg(r'[a-zA-Z\-_]+')
    _argument_value = reg(r'[^&]+')
    _kv = _argument_name << lit('=') & _argument_value
    _arguments = (lit('?') >> rep1sep(_kv, lit('&')) << eof > dict) | (eof > constant({}))
    _uuid = reg(r'[A-Z0-9]{6}') > str
    qviewkit_url = _qviewkit_prefix >> _uuid & _arguments

    @classmethod
    def parse(cls, data) -> Tuple[str, Optional[dict]]:
        """
        Parses a qviewkit URL into a UUID and a dict containing optional arguments.
        """
        result = cls.qviewkit_url.parse(data)
        assert isinstance(result, Success), f"Parsing unsuccessful! URL: {data}"
        unwrapped = result.unwrap()
        return unwrapped


def url_handler(args=sys.argv):
    """
    Launch a qviewkit window with the file specified by the url.

    Takes as its only argument the qviewkit-url of the form `qviewkit://ABCDEF`, where `ABCDEF` is the UUID.
    """
    assert len(args) == 2, "qviewkit-url only takes a single argument!"
    qviewkit_url = args[1]
    logging.info(f"Parsing URL {qviewkit_url}")
    uuid, kvargs = QviewkitURLParser.parse(qviewkit_url)

    qkit.start()
    file = qkit.fid.get(uuid)

    repo = qkit.cfg.get("repo_path", default=None)
    if file is None and QviewkitURLParser.HINT_ARGUMENT in kvargs and repo is not None:
        end_at = time.time() + qkit.cfg.get("search_timeout_seconds", default=10)
        try:
            file = directed_search(repo, kvargs[QviewkitURLParser.HINT_ARGUMENT].split(";"), uuid, end_at)
        except TimeoutError:
            logging.error(f"Search timed out.")
            file = None

    if file is not None:  # Success!
        logging.info(f"Opening file {file} derived from URL {qviewkit_url}")
        main(argv=[args[0], "-f", file])
    else:  # Failure...
        logging.error(f"Could not find file based on URL {qviewkit_url}")
        main(argv=[args[0]])  # Opening empty window to signal error.


def directed_search(directory: str, hints: list[str], target_uuid: str, end_time: float) -> Optional[str]:
    """
    Search repository putting priority on paths containing strings in `hints`.

    This may take a very long time. The search will be aborted at `end_time` by raising an `TimeoutError`.
    """
    queue = PriorityQueue()
    queue.put_nowait((0, directory))

    while not queue.empty():  # We still have items to search and did not return early.
        base_prio, path = queue.get_nowait()
        logging.info(f"Visiting {path}")
        for entry in os.scandir(path):
            if entry.is_file():
                # A file is a terminal node
                if entry.name.startswith(target_uuid) and entry.name.endswith(".h5"):
                    # We found the file. Return early
                    return entry.path
            elif entry.is_dir():
                # We have a directory, we need to prioritize it according to its name.
                # If the name contains some pattern, it is added to the queue with an increased priority
                if entry.name.startswith(target_uuid):
                    # This directory will most likely contain the file we are looking for, go there directly.
                    queue.put_nowait((base_prio - len(hints) - 1, entry.path))
                    continue
                score = base_prio - sum([1 if hint in entry.name.lower() else 0 for hint in hints])
                logging.debug(f"RP: {score}\t{entry.path}")
                queue.put_nowait((score, entry.path))
        if time.time() > end_time:
            raise TimeoutError("Search timed out!")
    return None
