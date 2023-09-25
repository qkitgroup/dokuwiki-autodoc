import sys
import re
import qkit
from qkit.gui.qviewkit.main import main
import logging

URL_REGEX = re.compile(r'qviewkit://([A-Z0-9]{6})')

def url_handler(args = sys.argv):
    """
    Launch a qviewkit window with the file specified by the url.

    Takes as its only argument the qviewkit-url of the form `qviewkit://ABCDEF`, where `ABCDEF` is the UUID.
    """
    assert len(args) == 2, "qviewkit-url only takes a single argument!"
        
    match = URL_REGEX.match(args[1])
    uuid = match.group(1)

    qkit.start()
    file = qkit.fid.get(uuid)

    logging.info(f"Opening file {file} derived from URL {args[1]}")
    main(argv = [args[0], "-f", file])