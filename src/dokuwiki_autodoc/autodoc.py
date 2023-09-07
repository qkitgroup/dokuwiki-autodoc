import dokuwiki
import getpass
import logging
from typing import Any, Callable, Dict, Iterable, List, Union
from os import PathLike, path, listdir
import liquid
import copy

class AutoDocumentation():
    """
    This class provides an interface to a dokuwiki instance.
    It is a very limited adaptation to Jupyter Notebooks to allow APPEND ONLY documentation.
    This is to prevent data loss.
    """

    def __init__(self, server):
        """
        Create a connection to the wiki on the server.
        Interactively querries the username and password.
        """
        username = input("Wiki user: ")
        password = getpass.getpass("Wiki password: ")
        self.wiki = dokuwiki.DokuWiki(server, username, password, cookieAuth=True)
        logging.info(f"Connected to Wiki: {self.wiki.title}")

    def append_table(self, page: str, columns: List[str], data: List):
        """
        Appends data to the last table in the specified `page`.
        If the page doesn't exist, it will be created.
        If it does not end with a table compatible with the header, a new table will be created.
        Then, a row of data will be appended.

        Note: This may not work, if the table head contains links. To determine compatibility,
        the amount of column seperators ('|' and '^') are counted. The problem is, that links may contain '|'
        to indicate the display text, which breaks this system. Other rows may contain links, however.
        """
        assert len(columns) == len(data), f"The table must have the same amount of columns for its head ({len(columns)}) and data ({len(data)})"
        
        # Create table lines
        data = list(map(lambda entry: str(entry), data)) # Make all the data strings
        table_head = '^ ' + ' ^ '.join(columns) + ' ^\n'
        data_row = '| ' + ' | '.join(data) + ' |\n'
        
        # Step 1: Get page content or initialize it empty
        page_content = "" # This is done, so that the logic can remain the same without edge cases.
        try:
            page_content = self.wiki.pages.get(page)
        except(dokuwiki.DokuWikiError):
            # Page probably doesn't exist, so initialize it empty
            self.wiki.pages.set(page, "")
        
        to_be_appended = ""
        if not page_content.endswith(("\n", "\r\n")): # Ensure we start in a new line
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
                break # No longer in the table, the previous line was the table head

        if head_candidate:
            # It is a table line, count columns
            column_count = head_candidate.count('|') + head_candidate.count('^') - 1

            if column_count == len(columns): # TODO: We have identified the head_candidate, should we enforce an exact match?
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

    def generate_report(self, page: str, data: dict, template_str: str):
        """
        Generate a report based on `template`, fill it with `content` and upload it under the name `page`.

        Uses the liquid templating language, but instead of curly braces, it uses square brackets, because dokuwiki
        already uses curly braces.
        """
        template = liquid.Template(template_str, tag_start_string="[%", tag_end_string="%]", 
                                   statement_start_string="[[", statement_end_string="]]")
        report = template.render(**data)
        assert not self.wiki.pages.get(page), "Page already exists!"
        self.wiki.pages.set(page, report, sum="Automatic Report Generation.")


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

class _Object(object):
    def __getitem__(self, key: str) -> Any:
        return self.__dict__[key]

class QkitDocumentationBuilder():

    

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
        self._data = _Object() # Store data available to the Liquid template
        self._data.overview_page = overview_page 
        self._report_id = AutoDocumentation.join_path([self.overview_page, self.UUID])
        

    def __enter__(self):
        try:
            import qkit
            from qkit.storage.store import Data
            import json
        except ImportError as err:
            raise ImportError(err, "Tried to import qkit, is it installed?", name="qkit")
        if not self.UUID:
            self.UUID = qkit.fid.get_last()

        self._h5path = qkit.fid.get(self.UUID)
        self._h5data = Data(self._h5path)
        self._data.measurement = json.loads(self._h5data.data.measurement[:][0])
        self._data.settings = json.loads(self._h5data.data.settings[:][0])

        return self

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
        self._data.images = self._image_ids
    
    def generate_report(self, template, **context):
        all_data = copy.copy(self._data)
        all_data.__dict__.update(context)
        self.autodoc.generate_report(self._report_id, context, template)


    def generate_table_entry(self, columns: List[str], generator: Callable[[any], List], **context):
        """
        Generates a table with the columns UUID, date, and additional columns based on the users choice.
        """
        all_data = copy.copy(self._data)
        all_data.__dict__.update(context)
        import qkit
        full_columns = ['UUID', 'Date'] + columns
        full_row = [AutoDocumentation.format_link(self._report_id, self.UUID), qkit.fid.get_date(self.UUID)] + list(map(lambda it: str(it), generator(all_data)))
        self.autodoc.append_table(self.overview_page, full_columns, full_row)

    def __exit__(self, *args):
        self._h5data.close()