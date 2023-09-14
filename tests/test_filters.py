from dokuwiki_autodoc.liquid_filters import dict2doku
import pytest
from dokuwiki_autodoc.autodoc import AutoDocumentation

@pytest.fixture
def liquid_env():
    return AutoDocumentation.build_default_liquid()

expectation = """
  * primitive_1: 123
  * primitive_5: 456

==== complex_1 ====
  * p1: value
  * p2: other value

==== complex_2 ====

=== c3 ===
  * p4: 0

""".strip()

def test_dict_format(liquid_env):
    test_object = {
        'primitive_1': 123,
        'complex_1': {
            'p1': 'value',
            'p2': 'other value'
        },
        'complex_2': {
            'c3': {'p4': 0 }
        },
        'primitive_5': 456
    }
    result = liquid_env.from_string("[[test_object | dict2doku]]").render(test_object=test_object).strip()
    assert result == expectation


def test_number_format(liquid_env):
    assert liquid_env.from_string("[[ '7.6e9' | decimal]]").render() == "7,600,000,000"
    assert liquid_env.from_string("[[ '7.6e9' | unit: 'hertz']]").render() == "7,600,000,000 Hz"

def test_templating(liquid_env):
    assert liquid_env.from_string("[[ '7.6e9' | decimal]]").render() == "7,600,000,000"
    assert liquid_env.from_string("[[ '7.6e9' | unit: 'hertz']]").render() == "7,600,000,000 Hz"
    assert liquid_env.from_string("[[ '1' | unit: 'ampere']]").render() == "1 A"

@pytest.fixture
def liquid_with_templates(liquid_env):
    AutoDocumentation.load_templates(liquid_env)
    return liquid_env

TEST_TEMPLATE = """
[% extends "doc_base.txt.liquid" %]
[% block content %]
This is a test!
[% endblock%]
"""

EXPECTATION = r"""
======   Measurement UUID ======
None with DNE by Me in run The Last One

QKIT-Version: Not relevant


This is a test!



===== Analysis =====
  * result: ok-ish






===== Measurement =====
  * uuid: UUID
  * measurement_type: None
  * measurement_func: DNE
  * user: Me
  * run_id: The Last One
  * git_commit_id: Not relevant




-----------
This documentation has been autogenerated using {{https://github.com/qkitgroup/dokuwiki-autodoc|dokuwiki-autodoc}}.
"""

def test_templates(liquid_with_templates):
    result = liquid_with_templates.from_string(TEST_TEMPLATE).render(measurement={
            'uuid': 'UUID', 
            'measurement_type': 'None',
            'measurement_func': 'DNE',
            'user': 'Me',
            'run_id': 'The Last One',
            'git_commit_id': 'Not relevant'
        }, analysis = {
            'result': 'ok-ish'
        })
    print(result)
    assert result.strip() == EXPECTATION.strip()