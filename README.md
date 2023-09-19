# Dokuwiki-AutoDoc

[![PyPI - Version](https://img.shields.io/pypi/v/dokuwiki-autodoc.svg)](https://pypi.org/project/dokuwiki-autodoc)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/dokuwiki-autodoc.svg)](https://pypi.org/project/dokuwiki-autodoc)

Automatically generate documentation of your experiments in DokuWiki! This way, you won't loose track or overview of your progress!

This package uses the [DokuWiki](https://pypi.org/project/dokuwiki/) python package to automatically access DokuWiki and write reports generated from [Liquid](https://pypi.org/project/python-liquid/) templates. Plots generated from your measurements are uploaded as well.

This package is intended to be run in a [Jupyter Lab](https://jupyter.org/) environment and uses interactive prompts to authenticate against the wiki.

## Usage
To establish a connection to your wiki, run
```python
doc = AutoDocumentation("https://your.wiki/")
```
This will prompt you for your username and password.

Next, to generate a report from a measurement in qkit, run the following directly after your measurement:
```python
with QkitDocumentationBuilder(doc, 'sample:yoursample') as builder:
        builder.upload_images()
        builder.generate_report(QKIT_TEMPLATE)
        builder.generate_table_entry(["Type", "Comment"], lambda data: [
            data['measurement']['measurement_type'], 
            input("Comment: ")
        ])
```
This will
- Upload the plots generated from your measurement by qkit
- Build a report from a template
- Insert a reference to this measurement into an overview table (UUID, Date) with additional columns as defined (Type, Comment).

For safety reasons, the table page is only amended. This means, that only the last table of the page can be extended. Therefore, it may be warranted to include multiple sub-pages on a sample page. This can be done with the [Include Plugin](https://www.dokuwiki.org/plugin:include).

## Templates
Templates for your reports are defined using the Liquid templating language. As DokuWiki uses curly braces for images and square brackets for links, Liquid is configured to use '{[' and ']}' (respectively '{%' '%}') instead. An Example may look like
```
{% extends "doc_base.txt.liquid" %}
{% block title %} My Title {% endblock %}
{% block content %}
More content
{% endblock%}
```
which takes data from your `qkit` measurement and its settings to fill out the page, using the `doc_base.txt.liquid` template.
## Installation

```console
pip install dokuwiki-autodoc
```

Do note, that this project depends on qkit, but as qkit is not yet on PyPi, you will need to install it manually.

## Testing
Run a temporary dokuwiki instance on your local machine at `http://localhost`. Change the password and username in the mocked `sys.stdin`.

In order to test qkit interoperability, you will need to change the `SAMPLE` to one in your data directory.

## License
`dokuwiki-autodoc` is distributed under the terms of the [GPLv2](LICENSE.txt) license.
