from .test_auto_documentation import autodoc

TEMPLATE = """
{% extends "sample.txt.liquid" %}
{% block description %}
The Sample Description.
{% endblock%}
"""

EXPECTED_RESULT = """
====== Sample Test Run 1 ======

The Sample Description.




===== Measurements =====

==== VNA Scan ====
{{page>test_page:vna_scan}}

==== VNA Current Sweep ====
{{page>test_page:vna_current_sweep}}

"""


def test_sample_template(autodoc):
    data = {
        'sample_name': "Test Run 1",
        'document_path': "test_page",
        'measurements': {
            'VNA Scan': 'vna_scan',
            'VNA Current Sweep': 'vna_current_sweep'
        }
    }
    autodoc.generate_report_from_template_string(data['document_path'], data=data, template_str=TEMPLATE)
    autodoc.wiki.pages.set.assert_called_once_with("test_page", EXPECTED_RESULT, sum="Automatic Report Generation.")
