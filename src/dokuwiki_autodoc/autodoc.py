import pathlib
import re

import dokuwiki
import getpass
import logging
from typing import Any, Callable, Dict, Iterable, List, Union
from os import PathLike, path, listdir
import liquid
import copy
import inspect
from dokuwiki_autodoc.liquid_filters import dict2doku
from liquid_babel.filters import Unit, Number
from liquid.extra import add_inheritance_tags
from liquid.loaders import DictLoader, CachingFileSystemLoader
from importlib_resources import files  # TODO: Migrate to importlib.resources if python_required >= 3.9
import numpy as np
from .liquid_filters import BABEL_NUMBER_OPTIONS


class AutoDocumentation:
    """
    This class provides an interface to a dokuwiki instance.
    It is a very limited adaptation to Jupyter Notebooks to allow APPEND-ONLY documentation.
    This is to prevent data loss.
    """

    def __init__(self, server, use_certifi=False, allow_invalid_certs=False):
        """
        Create a connection to the wiki on the server.
        Interactively queries the username and password.
        If required, certifi can be used as a trust store.
        """
        username = input("Wiki user: ")
        password = getpass.getpass("Wiki password: ")

        if allow_invalid_certs:
            logging.warning("INVALID CERTIFICATES ARE ALLOWED! DO NOT USE IN PRODUCTION!")
            import ssl
            ssl.create_default_context = ssl._create_unverified_context
        elif use_certifi:
            import certifi
            import os
            os.environ['SSL_CERT_FILE'] = certifi.where()

        self.wiki = dokuwiki.DokuWiki(server, username, password, cookieAuth=True)
        logging.info(f"Connected to Wiki: {self.wiki.title}")

        self._liquid_environment = AutoDocumentation.build_default_liquid()

    def append_table(self, page: str, columns: List[str], data: List):
        """
        Appends data to the last table in the specified `page`.
        If the page doesn't exist, it will be created.
        If it does not end with a table compatible with the header, a new table will be created.
        Then, a row of data will be appended.

        Note: This may not work, if the table head contains links. To determine compatibility,
        the amount of column separators ('|' and '^') are counted. The problem is, that links may contain '|'
        to indicate the display text, which breaks this system. Other rows may contain links, however.
        """
        assert len(columns) == len(
            data), f"The table must have the same amount of columns for its head ({len(columns)}) and data ({len(data)})"

        # Create table lines
        data = list(map(lambda entry: str(entry), data))  # Make all the data strings
        table_head = '^ ' + ' ^ '.join(columns) + ' ^\n'
        data_row = '| ' + ' | '.join(data) + ' |\n'

        # Step 1: Get page content or initialize it empty
        page_content = ""  # This is done, so that the logic can remain the same without edge cases.
        try:
            page_content = self.wiki.pages.get(page)
        except dokuwiki.DokuWikiError:
            # Page probably doesn't exist, so initialize it empty
            self.wiki.pages.set(page, "")

        to_be_appended = ""
        if not page_content.endswith(("\n", "\r\n")):  # Ensure we start in a new line
            to_be_appended += "\n"

        # Step 2: Check if page content ends with a table
        lines = list(map(lambda it: it.strip(), page_content.strip().splitlines()))
        # Find the last table heading. Walk backwards from the last line.
        head_candidate = None
        for line in reversed(lines):
            # Note: If we run out of lines, (start of document), then the table ends as well.
            if line.startswith(('|', '^')) and line.endswith(('|', '^')):
                # Still in the table
                head_candidate = line
            else:
                break  # No longer in the table, the previous line was the table head

        if head_candidate:
            # It is a table line, count columns
            column_count = head_candidate.count('|') + head_candidate.count('^') - 1

            if column_count == len(columns):
                # TODO: We have identified the head_candidate, should we enforce an exact match?
                # There is a table matching our column count, append data
                to_be_appended += data_row
            else:
                # We need a new table, the old one doesn't match our column count
                to_be_appended += "\n" + table_head + data_row
        else:
            # There is no table, create a new table, same as above
            to_be_appended += "\n" + table_head + data_row

        # Lastly, we append the data
        self.wiki.pages.append(page, to_be_appended, minor=True)

    def upload_image(self, id: str, content: Union[str, bytes, PathLike]):
        """
        Uploads an image to the wiki. Does not overwrite. Please ensure unique file names yourself.

        In order to reference the uploaded pictures, please refer to https://www.dokuwiki.org/de:images
        """
        self.wiki.medias.add(id, content, overwrite=False)

    def generate_report_from_template(self, page: str, data: dict, template_name: str):
        """
        Generate a report based on a template with the name `template`, fill it with `content` and upload it under the name 
        `page`.

        Uses the liquid templating language, but instead of curly braces, it uses square brackets, because dokuwiki
        already uses curly braces.
        """
        template = self._liquid_environment.get_template(template_name)
        self._generate_and_upload_template(page, data, template)

    def generate_report_from_template_string(self, page: str, data: dict, template_str: str):
        """
        Generate a report based on `template_str`, fill it with `content` and upload it under the name `page`.

        Uses the liquid templating language, but instead of curly braces, it uses square brackets, because dokuwiki
        already uses curly braces.
        """
        template = self._liquid_environment.from_string(template_str)
        self._generate_and_upload_template(page, data, template)

    def _generate_and_upload_template(self, page: str, data: dict, template: liquid.BoundTemplate):
        report = template.render(**data)
        assert not self.wiki.pages.get(page), "Page already exists!"
        self.wiki.pages.set(page, report, sum="Automatic Report Generation.")

    def with_templates(self, template_directory=None):
        AutoDocumentation.load_templates(self._liquid_environment, template_directory)
        return self

    @staticmethod
    def format_link(target: str, text: str = None) -> str:
        """
        Formats a string which is interpreted as a link in dokuwiki, which points to `target`, while the user
        sees `text`, if set.
        """
        if not text:
            return f"[[{target}]]"
        else:
            return f"[[{target}|{text}]]"

    @staticmethod
    def join_path(iterable: Iterable) -> str:
        return ":".join(iterable)

    @staticmethod
    def build_default_liquid() -> liquid.Environment:
        env = liquid.Environment(tag_start_string="{%", tag_end_string="%}",
                                 statement_start_string="{[", statement_end_string="]}")
        env.add_filter("dict2doku", dict2doku)
        env.add_filter("decimal", Number(**BABEL_NUMBER_OPTIONS))
        env.add_filter("unit", Unit(default_length='short'))
        add_inheritance_tags(env)
        return env

    @staticmethod
    def load_templates(environment, path=None):
        """
        Load liquid templates. If no path is given, package templates are loaded.
        The package templates serve as a baseline and try to simplify interaction with qkit.
        """
        if path == None:
            package = files("dokuwiki_autodoc.templates")
            templates = {resource.name: package.joinpath(resource.name).read_text()
                         for resource in package.iterdir() if resource.is_file() and resource.name.endswith(".liquid")}
            environment.loader = DictLoader(templates)
        else:
            environment.loader = CachingFileSystemLoader(path)


class _Object(object):
    def __getitem__(self, key: str) -> Any:
        return self.__dict__[key]


class QkitDocumentationBuilder:

    def __init__(self, autodoc: AutoDocumentation, overview_page: str, UUID: str = None):
        """
        Generate a report from the last measurement performed in qkit. The report will be generated based on the measurement
        identified by `UUID` if set, otherwise the last measurement will be used.

        In this context `upload_images()` can be used to upload the auto-generated images,
        while `generate_report(template, additional_context)` will generate the report.

        To link to the report, use the generate_table_entry() function.
        """
        self.autodoc = autodoc
        self.UUID = UUID
        assert overview_page, "Overview page must be a non-empty string!"
        self.overview_page = overview_page
        self._image_ids = []
        self._data = _Object()  # Store data available to the Liquid template
        self._data.overview_page = overview_page
        self._user_context = {}
        self._local_context = None

    def __enter__(self):
        try:
            import qkit
            from qkit.storage.store import Data
            import json
        except ImportError as err:
            raise ImportError(err, "Tried to import qkit, is it installed?", name="qkit")
        if not self.UUID:
            self.UUID = qkit.fid.get_last()

        self._data_path = qkit.cfg['datadir']
        self._report_id = AutoDocumentation.join_path([self.overview_page, self.UUID])
        self._h5path = qkit.fid.get(self.UUID)
        self._h5data = Data(self._h5path)

        # Load proposed url into context
        containing_dir = pathlib.Path(self._h5path).parent
        relpath = path.relpath(containing_dir, start=self._data_path)
        split_pattern = re.compile(r"[/\\._\- ]")
        split = set(section.strip() for section in split_pattern.split(str(relpath)) if section.strip())
        split.add('data')  # Very common.
        split = list(dict.fromkeys(split))  # Make it an ordered list. Reproducibility.
        split.sort()
        hint = ";".join(split)
        url = f"qviewkit://{self.UUID}?hint={hint}"
        self._data.url = url
        # Load Metadata into context
        self._data.measurement = json.loads(self._h5data.data.measurement[:][0])
        self._data.settings = json.loads(self._h5data.data.settings[:][0])

        # Try loading analysis data
        analysis = self._gather_analysis()
        if analysis:
            self._data.analysis = analysis

        return self

    def _gather_analysis(self):
        analysis_context = {}
        analysis = self._h5data.analysis
        if not analysis:
            return None  # No analysis available
        for key in analysis.__dict__.keys():
            data = analysis.__dict__[key][:]
            if len(data) == 0:
                continue
            if np.size(data) == 1:  # Just a single value
                analysis_context[key] = np.asarray(data).flatten()[0]  # Get first item
        return analysis_context

    def upload_images(self):
        """
        Uploads the autogenerated images associated with the used UUID.
        """
        path_to_images = path.join(path.dirname(self._h5path), 'images')
        files_only = [f for f in listdir(path_to_images) if path.isfile(path.join(path_to_images, f))]
        for f in files_only:
            absolute_path = path.join(path_to_images, f)
            image_id = AutoDocumentation.join_path([self._report_id, f])
            self._image_ids.append(image_id)
            self.autodoc.upload_image(image_id, absolute_path)
        # First, sort the images into alphabetical order
        self._image_ids.sort()

        # Sort images into a useful order. Previous order is preserved for identical metrics, so that within
        # each group, alphabetization is preserved.
        sort_order = {'view': -3, 'data0': -2, 'analysis0': -1}

        def sort_metric(data: str) -> int:
            return min([value for (key, value) in sort_order.items() if key in data])

        self._image_ids.sort(key=lambda entry: sort_metric(entry.split(':')[-1]))
        self._data.images = self._image_ids

    def update_context(self, **context):
        """
        Provide user context defined from key-word arguments.
        """
        self._user_context.update(context)

    def update_context_with_locals(self):
        """
        Use inspection to get the callees local variables for context.
        """
        our_frame = inspect.currentframe()
        local_context = our_frame.f_back.f_locals.copy()  # Who called us and what are their locals?
        # Filter out private stuff
        self._local_context = {k: v for k, v in local_context.items() if not k.startswith('_')}
        del our_frame  # Prevent reference cycles. Garbage collection does not like that.

    def generate_report(self, template):
        self.autodoc.generate_report_from_template_string(self._report_id, self._collect_context(), template)

    def generate_report_from_template_file(self, template_name):
        self.autodoc.generate_report_from_template(self._report_id, self._collect_context(), template_name)

    def _collect_context(self) -> dict:
        all_data = copy.copy(self._data)  # Automatically collected. Lowest priority.
        if self._local_context is not None:  # May overwrite stuff, but not highest prio.
            if "locals" in all_data.__dict__:
                all_data.__dict__["locals"].__dict__.update(self._local_context)
            else:
                all_data.__dict__["locals"] = self._local_context

        all_data.__dict__.update(self._user_context)  # The user may overwrite everything.
        return all_data.__dict__

    class _TableBuilder:
        """
        The _Table Builder context to track columns and content, in a readable way.

        Enter with a `with` statement, and add columns using `add_column`. Once the exited, the table row
        will be added.
        """

        def __init__(self, autodoc, overview_page, context):
            self._columns = []
            self._content = []
            self._autodoc = autodoc
            self._context = context
            self._overview_page = overview_page

        def __enter__(self):
            return self

        def add_column(self, title: str, content: Callable):
            """
            Adds a column with `title` and creates the content from the `content` callable.

            This way, all variables available in content generation are also available for table creation.
            """
            self._columns.append(title)
            self._content.append(str(content(self._context)))

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None and exc_val is None and exc_tb is None:
                # Successfully executed -> Upload
                self._autodoc.append_table(self._overview_page, self._columns, self._content)
            else:
                raise exc_val

    def table_builder(self, **context) -> _TableBuilder:
        """
        Create a table_builder. Automatically adds the UUID and date of your measurements.

        Use the tb.add_column() function in _TableBuilder to add additional columns.
        """
        all_data = copy.copy(self._data)
        all_data.__dict__.update(context)

        import qkit
        builder = QkitDocumentationBuilder._TableBuilder(self.autodoc, self.overview_page, context=all_data)
        builder.add_column("UUID", lambda _: AutoDocumentation.format_link(self._report_id, self.UUID))
        builder.add_column("Date", lambda _: qkit.fid.get_date(self.UUID))
        return builder

    def __exit__(self, *args):
        self._h5data.close()
