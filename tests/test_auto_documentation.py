from dokuwiki_autodoc.autodoc import AutoDocumentation, QkitDocumentationBuilder
from time import time
import pytest
import os

TEMPLATE_TEXT_ONLY = r"""
====== {[ sample_name ]} ======

===== Information =====
Condition: {[ condition ]}

===== Results =====
Result: {[ result ]}
"""

TEMPLATE_WITH_IMAGE = TEMPLATE_TEXT_ONLY + """

Image:
{{ {[image_id]}?200 }}
"""


@pytest.fixture
def autodoc(mocker, monkeypatch) -> AutoDocumentation:
    """
    Construct the mocked auto documentation object.

    Assert the calls in the test cases, see https://docs.python.org/3/library/unittest.mock.html#patch-object
    """
    # Step 1: Mock DokuWiki API to prevent outbound calls
    mocker.patch("dokuwiki.DokuWiki.send")
    monkeypatch.setattr('builtins.input', lambda prompt: "username")
    monkeypatch.setattr('getpass.getpass', lambda prompt: "password")
    doc = AutoDocumentation("https://host").with_templates()

    # Step 2: Patch all calls.
    mocker.patch("dokuwiki.DokuWiki.send", side_effect=Exception('Uncaptured API call!'))
    mocker.patch.object(doc.wiki.pages, "get", return_value="")  # Pages do not exist
    mocker.patch.object(doc.wiki.pages, "set")  # But can be created, no problems.
    mocker.patch.object(doc.wiki.pages, "append")  # Appending works too.
    mocker.patch.object(doc.wiki.medias, "add")  # Medias can be created as well

    return doc


def test_certifi_path(mocker, monkeypatch):
    mocker.patch("dokuwiki.DokuWiki.send")
    monkeypatch.setattr('builtins.input', lambda prompt: "username")
    monkeypatch.setattr('getpass.getpass', lambda prompt: "password")
    doc = AutoDocumentation("https://host", use_certifi=True)


def test_report_generation(autodoc):
    data = {'sample_name': "Test Sample", 'condition': "good", 'result': "Fine"}
    autodoc.generate_report_from_template_string('sample:test:report', data, TEMPLATE_TEXT_ONLY)


def test_doku_filter(autodoc):
    data = {'all': {'sample_name': "Test Sample", 'condition': {'left': 'good', 'right': 'ok'}, 'result': "Fine"}}
    autodoc.generate_report_from_template_string('sample:test:report', data, "{[all | dict2doku]}")
    autodoc.wiki.pages.get.assert_called_once_with('sample:test:report')
    autodoc.wiki.pages.set.assert_called_once_with('sample:test:report',
                                                   '  * sample_name: Test Sample\n  * result: Fine\n\n==== condition ====\n  * left: good\n  * right: ok\n\n',
                                                   sum='Automatic Report Generation.')


def test_image_upload(autodoc):
    id = 'sample:test:images:unique_id.png'
    autodoc.upload_image(id, 'tests/example.png')
    autodoc.wiki.medias.add.assert_called_once()


def test_full_report(autodoc):
    id = 'sample:test:images:unique_id.png'
    autodoc.upload_image(id, 'tests/example.png')

    data = {'sample_name': "Test Sample", 'condition': "good", 'result': "Fine", 'image_id': id}
    autodoc.generate_report_from_template_string('sample:test:image_report', data, TEMPLATE_WITH_IMAGE)


def test_link_formating():
    link = "some:link"
    text = "some alt text"
    assert AutoDocumentation.format_link(link) == "[[some:link]]"
    assert AutoDocumentation.format_link(link, text) == "[[some:link|some alt text]]"


def test_table_append(autodoc):
    columns = ['name', 'date', 'link']
    now = time()
    data = ['Test', now, AutoDocumentation.format_link("https://example.com", "Example")]
    autodoc.append_table('sample:test:table', columns, data)
    autodoc.wiki.pages.get.assert_called_once_with('sample:test:table')
    autodoc.wiki.pages.append.assert_called_once_with('sample:test:table',
                                                      f'\n\n^ name ^ date ^ link ^\n| Test | {now} | [[https://example.com|Example]] |\n',
                                                      minor=True)


QKIT_TEMPLATE = """
{% extends "doc_base.txt.liquid" %}
{% block title %} My Title {% endblock %}
{% block content %}
More content
{% endblock%}
"""


@pytest.fixture
def qkit_fix(mocker):
    import qkit
    qkit.cfg['datadir'] = os.path.abspath("./tests/test_data/")
    qkit.cfg['logdir'] = os.path.abspath("./tests/qkit-log/")
    qkit.start()
    qkit.fid.update_file_db()


def test_qkit_measurement_documentation(autodoc, qkit_fix):
    with QkitDocumentationBuilder(autodoc, 'sample:test:qkit', 'RZDWVZ') as builder:
        builder.upload_images()
        builder.generate_report(QKIT_TEMPLATE)
        with builder.table_builder() as tb:
            tb.add_column("Type", lambda data: data.measurement['measurement_type'])
            tb.add_column("Comment", lambda _: "Look! A comment!")

    autodoc.wiki.pages.set.assert_called_once()  # This function must have been called exactly once
    call_args = autodoc.wiki.pages.set.call_args.args
    call_kwargs = autodoc.wiki.pages.set.call_args.kwargs
    assert len(call_args) == 2, "Invalid argument count!"
    assert call_args[0] == 'sample:test:qkit:RZDWVZ'
    assert call_args[1] == open("./tests/expected-report-RZDWVZ.txt").read(), f"Found:\n {call_args[1]}"
    assert call_kwargs['sum'] == 'Automatic Report Generation.'


def test_qkit_measurement_without_uuid(autodoc, qkit_fix):
    with QkitDocumentationBuilder(autodoc, 'sample:test:qkit') as builder:
        builder.upload_images()
        builder.generate_report(QKIT_TEMPLATE)
        with builder.table_builder() as tb:
            tb.add_column("Type", lambda data: data.measurement['measurement_type'])
            tb.add_column("Comment", lambda _: "Look! A comment!")

    autodoc.wiki.pages.set.assert_called_once()  # This function must have been called exactly once
    call_args = autodoc.wiki.pages.set.call_args.args
    call_kwargs = autodoc.wiki.pages.set.call_args.kwargs
    assert len(call_args) == 2, "Invalid argument count!"
    assert call_args[0] == 'sample:test:qkit:S0Z69N'
    assert call_args[1] == open("./tests/expected-report-S0Z69N.txt").read(), f"Found:\n {call_args[1]}"
    assert call_kwargs['sum'] == 'Automatic Report Generation.'


def test_qkit_properties(autodoc, qkit_fix):
    with QkitDocumentationBuilder(autodoc, 'sample:test:qkit', UUID='S0Z69N') as builder:
        builder.upload_images()
        builder.generate_report(QKIT_TEMPLATE)
        with builder.table_builder() as tb:
            tb.add_column("Type", lambda data: data.measurement['measurement_type'])
            tb.add_column("Comment", lambda _: "Look! A comment!")

    autodoc.wiki.pages.set.assert_called_once()  # This function must have been called exactly once
    call_args = autodoc.wiki.pages.set.call_args.args
    call_kwargs = autodoc.wiki.pages.set.call_args.kwargs
    assert len(call_args) == 2, "Invalid argument count!"
    assert call_args[0] == 'sample:test:qkit:S0Z69N'
    assert call_args[1] == open("./tests/expected-report-S0Z69N.txt").read(), f"Found:\n {call_args[1]}"
    assert call_kwargs['sum'] == 'Automatic Report Generation.'


CAPTURE_TEMPLATE = "{[locals.capture_this]}"


def test_local_capture(autodoc, qkit_fix):
    capture_this = "the_captured"
    with QkitDocumentationBuilder(autodoc, 'sample:test:qkit', UUID='S0Z69N') as builder:
        builder.update_context_with_locals()
        builder.generate_report(CAPTURE_TEMPLATE)

    autodoc.wiki.pages.set.assert_called_once()  # This function must have been called exactly once
    call_args = autodoc.wiki.pages.set.call_args.args
    call_kwargs = autodoc.wiki.pages.set.call_args.kwargs
    assert len(call_args) == 2, "Invalid argument count!"
    assert call_args[0] == 'sample:test:qkit:S0Z69N'
    assert call_args[1] == "the_captured", f"Found:\n {call_args[1]}"
    assert call_kwargs['sum'] == 'Automatic Report Generation.'


FORCED_CAPTURE_TEMPLATE = "{[capture_this]}"
def test_forced_capture(autodoc, qkit_fix):
    with QkitDocumentationBuilder(autodoc, 'sample:test:qkit', UUID='S0Z69N') as builder:
        builder.update_context(capture_this="the_captured")
        builder.generate_report(FORCED_CAPTURE_TEMPLATE)

    autodoc.wiki.pages.set.assert_called_once()  # This function must have been called exactly once
    call_args = autodoc.wiki.pages.set.call_args.args
    call_kwargs = autodoc.wiki.pages.set.call_args.kwargs
    assert len(call_args) == 2, "Invalid argument count!"
    assert call_args[0] == 'sample:test:qkit:S0Z69N'
    assert call_args[1] == "the_captured", f"Found:\n {call_args[1]}"
    assert call_kwargs['sum'] == 'Automatic Report Generation.'