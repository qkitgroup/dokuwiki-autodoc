from parsita import Success

from dokuwiki_autodoc.qviewkit_url_handler import QviewkitURLParser


def test_basic_url():
    result = QviewkitURLParser.qviewkit_url.parse("qviewkit://ABCDEF")
    assert result == Success(['ABCDEF', {}])


def test_url_with_repo():
    result = QviewkitURLParser.qviewkit_url.parse("qviewkit://ABCDEF?repo=https://measurements.phi.kit.edu")
    assert result == Success(['ABCDEF', {'repo': 'https://measurements.phi.kit.edu'}])


def test_primitive_extraction():
    uuid, kvs = QviewkitURLParser.parse("qviewkit://ABCDEF?repo=https://measurements.phi.kit.edu")
    assert uuid == 'ABCDEF'
    assert kvs['repo'] == 'https://measurements.phi.kit.edu'


def test_extensibility():
    uuid, kvs = QviewkitURLParser.parse("qviewkit://ABCDEF?repo=https://measurements.phi.kit.edu&orig=10.0.0.1")
    assert uuid == 'ABCDEF'
    assert kvs['repo'] == 'https://measurements.phi.kit.edu'
    assert kvs['orig'] == '10.0.0.1'
