from dokuwiki_autodoc.liquid_filters import dict2doku

expectation = """
primitive_1: 123
primitive_5: 456

==== complex_1 ====
p1: value
p2: other value

==== complex_2 ====

=== c3 ===
p4: 0

""".strip()

def test_dict_format():
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
    result = dict2doku(test_object).strip()
    assert result == expectation