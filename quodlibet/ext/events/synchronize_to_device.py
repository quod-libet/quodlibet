# -*- coding: utf-8 -*-
# Copyright 2018 Jan Korte
#           2020 Daniel Petrescu
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import shutil
import unicodedata
from pathlib import Path

from gi.repository import Gtk, Pango
from senf import fsn2text

from quodlibet import _
from quodlibet import app
from quodlibet import config
from quodlibet import get_data_dir
from quodlibet import ngettext
from quodlibet import qltk
from quodlibet import util
from quodlibet.pattern import FileFromPattern
from quodlibet.plugins import PM
from quodlibet.plugins import PluginConfigMixin
from quodlibet.plugins.events import EventPlugin
from quodlibet.qltk import Icons
from quodlibet.qltk.cbes import ComboBoxEntrySave
from quodlibet.qltk.ccb import ConfigCheckButton
from quodlibet.qltk.views import HintedTreeView
from quodlibet.query import Query
from quodlibet.util import print_d, print_e, print_exc
from quodlibet.util.enum import enum
from quodlibet.util.path import strip_win32_incompat_from_path

PLUGIN_CONFIG_SECTION = 'synchronize_to_device'


class Entry:
    """
    An entry in the tree of previewed export paths.
    """

    @enum
    class Tags(str):
        """
        Various tags that will be used in the output.
        """
        EMPTY = ''
        PENDING_COPY = _('Pending copy')
        PENDING_DELETE = _('Pending delete')
        DELETE = _('delete')
        SKIP = _('Skip')
        SKIP_DUPLICATE = _('DUPLICATE')
        IN_PROGRESS_SYNC = _('Synchronizing')
        IN_PROGRESS_DELETE = _('Deleting')
        RESULT_SUCCESS = _('Success')
        RESULT_FAILURE = _('FAILURE')
        RESULT_SKIP_EXISTING = _('Skipped existing file')

    def __init__(self, song, export_path=None):
        self._song = song
        self.export_path = export_path or ''
        self.tag = self.Tags.EMPTY
        self._filename = None

    @property
    def filename(self):
        if self._song is not None:
            return fsn2text(self._song('~filename'))
        else:
            return self._filename

    @filename.setter
    def filename(self, name):
        if self._song is None:
            self._filename = name
        else:
            raise ValueError(_('Cannot set the filename of a song.'))


class SyncToDevice(EventPlugin, PluginConfigMixin):
    PLUGIN_ICON = Icons.NETWORK_TRANSMIT
    PLUGIN_ID = PLUGIN_CONFIG_SECTION
    PLUGIN_NAME = _('Synchronize to Device')
    PLUGIN_DESC = _('Synchronizes all songs from the selected saved searches '
                    'with the specified folder.')

    CONFIG_SECTION = PLUGIN_CONFIG_SECTION
    CONFIG_QUERY_PREFIX = 'query_'
    CONFIG_PATH_KEY = '{}_{}'.format(PLUGIN_CONFIG_SECTION, 'path')
    CONFIG_PATTERN_KEY = '{}_{}'.format(PLUGIN_CONFIG_SECTION, 'pattern')

    path_query = os.path.join(get_data_dir(), 'lists', 'queries.saved')
    path_pattern = os.path.join(get_data_dir(), 'lists', 'renamepatterns')

    spacing_main = 20
    spacing_large = 6
    spacing_small = 3
    summary_sep = ' ' * 2
    summary_sep_list = ',' + summary_sep

    default_export_pattern = os.path.join('<artist>', '<album>', '<title>')

    model_cols = {'entry': (0, object),
                  'tag': (1, str),
                  'filename': (2, str),
                  'export': (3, str)}

    def PluginPreferences(self, parent):
        # Check if the queries file exists
        if not os.path.exists(self.path_query):
            return self._no_queries_frame()

        # Read saved searches from file
        self.queries = {}
        with open(self.path_query, 'r', encoding='utf-8') as query_file:
            for query_string in query_file:
                name = next(query_file).strip()
                self.queries[name] = Query(query_string.strip())
        if not self.queries:
            # query_file is empty
            return self._no_queries_frame()

        main_vbox = Gtk.VBox(spacing=self.spacing_main)
        self.main_vbox = main_vbox

        # Saved search selection frame
        saved_search_vbox = Gtk.VBox(spacing=self.spacing_large)
        self.saved_search_vbox = saved_search_vbox
        for query_name, query in self.queries.items():
            query_config = self.CONFIG_QUERY_PREFIX + query_name
            check_button = ConfigCheckButton(query_name, PM.CONFIG_SECTION,
                                             self._config_key(query_config))
            check_button.set_active(self.config_get_bool(query_config))
            saved_search_vbox.pack_start(check_button, False, False, 0)
        saved_search_scroll = self._expandable_scroll(min_h=0, max_h=300)
        saved_search_scroll.add(saved_search_vbox)
        frame = qltk.Frame(label=_('Synchronize the following saved searches:'),
                           child=saved_search_scroll)
        main_vbox.pack_start(frame, False, False, 0)

        # Destination path entry field
        destination_entry = Gtk.Entry(
            placeholder_text=_('The absolute path to your export location'),
            text=config.get(PM.CONFIG_SECTION, self.CONFIG_PATH_KEY, '')
        )
        destination_entry.connect('changed', self._destination_path_changed)
        self.destination_entry = destination_entry

        # Destination path selection button
        destination_button = qltk.Button(label='', icon_name=Icons.FOLDER_OPEN)
        destination_button.connect('clicked', self._select_destination_path)

        # Destination path hbox
        destination_path_hbox = Gtk.HBox(spacing=self.spacing_small)
        destination_path_hbox.pack_start(destination_entry, True, True, 0)
        destination_path_hbox.pack_start(destination_button, False, False, 0)

        # Destination path information
        destination_warn_label = self._label_with_icon(
            _("All pre-existing files in the destination folder that aren't in "
              "the saved searches will be deleted."),
            Icons.DIALOG_WARNING)
        destination_info_label = self._label_with_icon(
            _('For devices mounted with MTP, export to a local destination '
              'folder, then transfer it to your device with rsync. '
              'Or, when syncing many files to an Android Device, use adb-sync, '
              'which is much faster.'),
            Icons.DIALOG_INFORMATION)

        # Destination path frame
        destination_vbox = Gtk.VBox(spacing=self.spacing_large)
        destination_vbox.pack_start(destination_path_hbox, False, False, 0)
        destination_vbox.pack_start(destination_warn_label, False, False, 0)
        destination_vbox.pack_start(destination_info_label, False, False, 0)
        frame = qltk.Frame(label=_('Destination path:'), child=destination_vbox)
        main_vbox.pack_start(frame, False, False, 0)

        # Export pattern frame
        export_pattern_combo = ComboBoxEntrySave(
            self.path_pattern, [self.default_export_pattern],
            title=_('Path Patterns'), edit_title=_(u'Edit saved patterns…'))
        export_pattern_combo.enable_clear_button()
        export_pattern_combo.show_all()
        export_pattern_entry = export_pattern_combo.get_child()
        export_pattern_entry.set_placeholder_text(
            _('The structure of the exported filenames, based on their tags'))
        export_pattern_entry.set_text(config.get(PM.CONFIG_SECTION,
                                                 self.CONFIG_PATTERN_KEY,
                                                 self.default_export_pattern))
        export_pattern_entry.connect('changed', self._export_pattern_changed)
        self.export_pattern_entry = export_pattern_entry
        frame = qltk.Frame(label=_('Export pattern:'),
                           child=export_pattern_combo)
        main_vbox.pack_start(frame, False, False, 0)

        # Start preview button
        preview_start_button = qltk.Button(label=_('Preview'),
                                           icon_name=Icons.VIEW_REFRESH)
        preview_start_button.set_visible(True)
        preview_start_button.connect('clicked', self._start_preview)
        self.preview_start_button = preview_start_button

        # Stop preview button
        preview_stop_button = qltk.Button(label=_('Stop preview'),
                                          icon_name=Icons.PROCESS_STOP)
        preview_stop_button.set_visible(False)
        preview_stop_button.set_no_show_all(True)
        preview_stop_button.connect('clicked', self._stop_preview)
        self.preview_stop_button = preview_stop_button

        # Details view
        column_types = [column[1] for column in self.model_cols.values()]
        self.model = Gtk.ListStore(*column_types)
        self.details_tree = details_tree = HintedTreeView(model=self.model)
        details_scroll = self._expandable_scroll()
        details_scroll.set_shadow_type(Gtk.ShadowType.IN)
        details_scroll.add(details_tree)
        self.renders = {}

        # Preview column: status
        render = Gtk.CellRendererText()
        column = self._tree_view_column(render, self._cdf_status,
                                        title=_('Status'), expand=False,
                                        sort=self._model_col_id('tag'))
        details_tree.append_column(column)

        # Preview column: file
        render = Gtk.CellRendererText()
        column = self._tree_view_column(render, self._cdf_source_path,
                                        title=_('Source File'),
                                        sort=self._model_col_id('filename'))
        details_tree.append_column(column)

        # Preview column: export path
        render = Gtk.CellRendererText()
        render.set_property('editable', True)
        render.connect('edited', self._row_edited)
        column = self._tree_view_column(render, self._cdf_export_path,
                                        title=_('Export Path'),
                                        sort=self._model_col_id('export'))
        details_tree.append_column(column)

        # Status labels
        self.status_operation = Gtk.Label(xalign=0.0, yalign=0.5, wrap=True,
                                          visible=False, no_show_all=True)
        self.status_progress = Gtk.Label(xalign=0.0, yalign=0.5, wrap=True,
                                         visible=False, no_show_all=True)
        self.status_duplicates = self._label_with_icon(
            _('Duplicate export paths detected! The export paths above can be '
              'edited before starting the synchronization.'),
            Icons.DIALOG_WARNING, visible=False)
        self.status_deletions = self._label_with_icon(
            _('Existing files in the destination path will be deleted!'),
            Icons.DIALOG_WARNING, visible=False)

        # Section for previewing exported files
        preview_vbox = Gtk.VBox(spacing=self.spacing_large)
        preview_vbox.pack_start(preview_start_button, False, False, 0)
        preview_vbox.pack_start(preview_stop_button, False, False, 0)
        preview_vbox.pack_start(details_scroll, True, True, 0)
        preview_vbox.pack_start(self.status_operation, False, False, 0)
        preview_vbox.pack_start(self.status_progress, False, False, 0)
        preview_vbox.pack_start(self.status_duplicates, False, False, 0)
        preview_vbox.pack_start(self.status_deletions, False, False, 0)
        main_vbox.pack_start(preview_vbox, True, True, 0)

        # Start sync button
        sync_start_button = qltk.Button(label=_('Start synchronization'),
                                        icon_name=Icons.DOCUMENT_SAVE)
        sync_start_button.set_sensitive(False)
        sync_start_button.set_visible(True)
        sync_start_button.connect('clicked', self._start_sync)
        self.sync_start_button = sync_start_button

        # Stop sync button
        sync_stop_button = qltk.Button(label=_('Stop synchronization'),
                                       icon_name=Icons.PROCESS_STOP)
        sync_stop_button.set_visible(False)
        sync_stop_button.set_no_show_all(True)
        sync_stop_button.connect('clicked', self._stop_sync)
        self.sync_stop_button = sync_stop_button

        # Section for the sync buttons
        sync_vbox = Gtk.VBox(spacing=self.spacing_large)
        sync_vbox.pack_start(sync_start_button, False, False, 0)
        sync_vbox.pack_start(sync_stop_button, False, False, 0)
        main_vbox.pack_start(sync_vbox, False, False, 0)

        return main_vbox

    @staticmethod
    def _no_queries_frame():
        """
        Create a frame to use when there are no saved searches.

        :return: A new Frame.
        """
        return qltk.Frame(
            _('No saved searches yet, create some and come back!'))

    def _expandable_scroll(self, min_h=50, max_h=-1, expand=True):
        """
        Create a ScrolledWindow that expands as content is added.

        :param min_h: The minimum height of the window, in pixels.
        :param max_h: The maximum height of the window, in pixels. It will grow
                      up to this height before it starts scrolling the content.
        :param expand: Whether the window should expand.
        :return: A new ScrolledWindow.
        """
        return Gtk.ScrolledWindow(min_content_height=min_h,
                                  max_content_height=max_h,
                                  propagate_natural_height=expand)

    def _label_with_icon(self, text, icon_name, visible=True):
        """
        Create a new label with an icon to the left of the text.

        :param text:      The new text to set for the label.
        :param icon_name: An icon name or None.
        :return: A HBox containing an icon followed by a label.
        """
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
        label = Gtk.Label(label=text, xalign=0.0, yalign=0.5, wrap=True)

        hbox = Gtk.HBox(spacing=self.spacing_large)
        if not visible:
            hbox.set_visible(False)
            hbox.set_no_show_all(True)
        hbox.pack_start(image, False, False, 0)
        hbox.pack_start(label, True, True, 0)

        return hbox

    def _tree_view_column(self, render, cdf, title=None, sort=None,
                          expand=True, resize=True, reorder=True):
        """
        Create a new TreeViewColumn with the given properties.

        :param render:  The A Gtk.CellRenderer of this cell.
        :param cdf:     The Gtk.TreeCellDataFunc to use for updating content.
        :param title:   The column's title.
        :param sort:    The model column to use when sorting this column.
        :param expand:  Whether the column width should automatically expand.
        :param resize:  Whether the column can be resized.
        :param reorder: Whether the column can be reordered.
        :return: The new TreeViewColumn.
        """
        tvc = Gtk.TreeViewColumn()
        tvc.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
        tvc.set_expand(expand)
        tvc.set_resizable(resize)
        tvc.set_reorderable(reorder)
        if title:
            tvc.set_title(title)
        if resize:
            render.set_property('ellipsize', Pango.EllipsizeMode.END)
        if sort:
            tvc.set_sort_column_id(sort)
        tvc.set_cell_data_func(render, cdf)
        tvc.pack_start(render, True)
        self.renders[tvc] = render
        return tvc

    def _destination_path_changed(self, entry):
        """
        Save the destination path to the global config when the path changes.

        :param entry: The destination path entry field.
        """
        config.set(PM.CONFIG_SECTION, self.CONFIG_PATH_KEY, entry.get_text())

    def _select_destination_path(self, button):
        """
        Show a folder selection dialog to select the destination path
        from the file system.

        :param button: The destination path selection button.
        """
        dialog = Gtk.FileChooserDialog(
            title=_('Choose destination path'),
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            select_multiple=False, create_folders=True,
            local_only=False, show_hidden=True)
        dialog.add_buttons(
            _('_Cancel'), Gtk.ResponseType.CANCEL,
            _('_Save'), Gtk.ResponseType.OK
        )
        dialog.set_default_response(Gtk.ResponseType.OK)

        # If there is an existing path in the entry field,
        # make that path the default
        destination_entry_text = self.destination_entry.get_text()
        if destination_entry_text != '':
            dialog.set_current_folder(destination_entry_text)

        # Show the dialog and get the selected path
        response = dialog.run()
        response_path = dialog.get_filename()

        # Close the dialog and save the selected path
        dialog.destroy()
        if response == Gtk.ResponseType.OK \
                and response_path != destination_entry_text:
            self.destination_entry.set_text(response_path)

    def _export_pattern_changed(self, entry):
        """
        Save the export pattern to the global config when the pattern changes.

        :param entry: The export pattern entry field.
        """
        config.set(PM.CONFIG_SECTION, self.CONFIG_PATTERN_KEY, entry.get_text())

    def _cdf_status(self, column, cell, model, iter_, data):
        """
        Handle entering data into the "Status" column of the sync previews.
        """
        cell.set_property('text', model[iter_][self._model_col_id('tag')])

    def _cdf_source_path(self, column, cell, model, iter_, data):
        """
        Handle entering data into the "File" column of the sync previews.
        """
        cell.set_property('text', model[iter_][self._model_col_id('filename')])

    def _cdf_export_path(self, column, cell, model, iter_, data):
        """
        Handle entering data into the "Export" column of the sync previews.
        """
        cell.set_property('text', model[iter_][self._model_col_id('export')])

    def _row_edited(self, renderer, path, entered_path):
        """
        Handle a manual edit of a previewed export path.

        :param renderer:        The object which received the signal.
        :param path:            The path identifying the edited cell.
        :param entered_path:    The new path entered by the user.
        """

        def _update_warnings():
            """
            Toggle the visibility of the status warning labels based on the song
            counts.
            """
            if self.c_song_dupes == 0:
                self.status_duplicates.set_visible(False)
            else:
                self.status_duplicates.set_visible(True)

            if self.c_songs_delete == 0:
                self.status_deletions.set_visible(False)
            else:
                self.status_deletions.set_visible(True)

        def _make_duplicate(entry, old_unique):
            """ Mark the given entry as a duplicate. """
            print_d(entry.filename)
            entry.tag = Entry.Tags.SKIP_DUPLICATE
            self.c_song_dupes += 1
            if old_unique:
                self.c_songs_copy -= 1
            _update_warnings()

        def _make_unique(entry, old_duplicate):
            """ Mark the given entry as a unique file. """
            print_d(entry.filename)
            entry.tag = Entry.Tags.PENDING_COPY
            self.c_songs_copy += 1
            if old_duplicate:
                self.c_song_dupes -= 1
            _update_warnings()

        def _make_skip(entry, counter):
            """ Skip the given entry during synchronization. """
            print_d(entry.filename)
            entry.tag = Entry.Tags.SKIP
            entry.export_path = ''
            return counter - 1

        def _update_other_song(model, path, iter_, *data):
            """
            Update a previewed path based on the current change.
            This is a callback function passed to Gtk.TreeModel.foreach() to
            iterate over the rows in a tree model.

            :return: True to stop iterating, False to continue.
            """
            model_entry = model[path][self._model_col_id('entry')]
            if model_entry is entry \
                    or model_entry.tag == Entry.Tags.DELETE \
                    or model_entry.export_path == '':
                pass
            elif model_entry.export_path == entered_path \
                    and model_entry.tag == Entry.Tags.PENDING_COPY:
                _make_duplicate(model_entry, True)
                self._update_model_value(iter_, 'tag', model_entry.tag)
            elif model_entry.tag == Entry.Tags.SKIP_DUPLICATE \
                    and model_entry.export_path != entered_path \
                    and self._get_paths()[model_entry.export_path] == 1:
                _make_unique(model_entry, True)
                self._update_model_value(iter_, 'tag', model_entry.tag)
            return False

        path = Gtk.TreePath.new_from_string(path)
        entry = self.model[path][self._model_col_id('entry')]
        if entry.export_path != entered_path:
            old_path, new_path = {}, {}

            old_path['duplicate'] = entry.tag == Entry.Tags.SKIP_DUPLICATE
            old_path['delete'] = entry.tag == Entry.Tags.PENDING_DELETE
            old_path['empty'] = not entry.export_path and not old_path['delete']
            old_path['unique'] = not (old_path['duplicate']
                                      or old_path['delete']
                                      or old_path['empty'])
            old_path_inv = {}
            for key, value in old_path.items():
                old_path_inv.setdefault(value, []).append(key)

            previewed_paths = self._get_paths().keys()

            new_path['duplicate'] = entered_path in previewed_paths
            new_path['delete'] = entered_path.lower() == Entry.Tags.DELETE
            new_path['empty'] = not entered_path and not new_path['delete']
            new_path['unique'] = not (new_path['duplicate']
                                      or new_path['delete']
                                      or new_path['empty'])
            new_path_inv = {}
            for key, value in new_path.items():
                new_path_inv.setdefault(value, []).append(key)

            print_d(_('Export path changed from [{old_path}] to [{new_path}] '
                      'for file [{filename}]').format(
                          filename=entry.filename,
                          old_path=' '.join(old_path_inv[True]),
                          new_path=' '.join(new_path_inv[True])))

            # If the old path was empty...
            if old_path['empty'] and new_path['empty']:
                pass
            elif old_path['empty'] and new_path['delete']:
                try:
                    Path(entry.filename).relative_to(self.expanded_destination)
                    entry.tag = Entry.Tags.PENDING_DELETE
                    self.c_songs_delete += 1
                    _update_warnings()
                except ValueError:
                    pass
            elif old_path['empty'] and new_path['duplicate']:
                _make_duplicate(entry, False)
                entry.export_path = entered_path
            elif old_path['empty'] and new_path['unique']:
                _make_unique(entry, False)
                entry.export_path = entered_path

            # If the old path was a deletion...
            elif old_path['delete'] and new_path['empty']:
                pass
            elif old_path['delete'] and new_path['delete']:
                self.c_songs_delete = _make_skip(entry, self.c_songs_delete)
                _update_warnings()
            elif old_path['delete'] and new_path['duplicate']:
                pass
            elif old_path['delete'] and new_path['unique']:
                pass

            # If the old path was a duplicate...
            elif old_path['duplicate'] and new_path['empty']:
                self.c_song_dupes = _make_skip(entry, self.c_song_dupes)
                self.model.foreach(_update_other_song)
                _update_warnings()
            elif old_path['duplicate'] and new_path['delete']:
                self.c_song_dupes = _make_skip(entry, self.c_song_dupes)
                self.model.foreach(_update_other_song)
                _update_warnings()
            elif old_path['duplicate'] and new_path['duplicate']:
                entry.export_path = entered_path
            elif old_path['duplicate'] and new_path['unique']:
                _make_unique(entry, True)
                entry.export_path = entered_path
                self.model.foreach(_update_other_song)

            # If the old path was unique...
            elif old_path['unique'] and new_path['empty']:
                self.c_songs_copy = _make_skip(entry, self.c_songs_copy)
                self.model.foreach(_update_other_song)
                _update_warnings()
            elif old_path['unique'] and new_path['delete']:
                self.c_songs_copy = _make_skip(entry, self.c_songs_copy)
                self.model.foreach(_update_other_song)
                _update_warnings()
            elif old_path['unique'] and new_path['duplicate']:
                _make_duplicate(entry, True)
                entry.export_path = entered_path
            elif old_path['unique'] and new_path['unique']:
                entry.export_path = entered_path
                self.model.foreach(_update_other_song)

            # Update the model and the summary field
            self.model.set_row(self.model.get_iter(path),
                               self._make_model_row(entry))
            self._update_preview_summary()

    def _update_model_value(self, iter_, column, value):
        """
        Set the data in a since cell of the ListStore model.

        :param iter_:  A Gtk.TreeIter for the row being modified.
        :param column: The name of the column to modify.
        :param value:  The new value for the cell.
        """
        self.model.set_value(iter_, self._model_col_id(column), value)

    def _model_col_id(self, name):
        """
        Get the column ID from the given name.

        :param name: The column name to search for.
        :raises: KeyError if a column with the given name does not exist.
        """
        return self.model_cols[name][0]

    @staticmethod
    def _make_model_row(entry):
        """
        Create a new row to insert into the ListStore model.

        :param entry: The Entry to insert.
        """
        return [entry, entry.tag, entry.filename, entry.export_path]

    @staticmethod
    def _run_pending_events():
        """
        Prevent the application from becoming unresponsive.
        """
        while Gtk.events_pending():
            Gtk.main_iteration()

    def _start_preview(self, button):
        """
        Start the generation of export paths for all songs.

        :param button: The start preview button.
        """
        print_d(_('Starting synchronization preview'))
        self.running = True

        # Summary labels
        self.status_operation.set_label(
            _('Synchronization preview in progress.'))
        self.status_operation.set_visible(True)
        self.status_progress.set_visible(False)
        self.status_duplicates.set_visible(False)
        self.status_deletions.set_visible(False)

        # Change button visibility
        self.preview_start_button.set_visible(False)
        self.preview_stop_button.set_visible(True)

        self.c_songs_copy = self.c_song_dupes = self.c_songs_delete = 0
        if self._run_preview() is None:
            return

        self._stop_preview()
        self.sync_start_button.set_sensitive(True)
        print_d(_('Finished synchronization preview'))

    def _stop_preview(self, button=None):
        """
        Stop the generation of export paths for all songs.

        :param button: The stop preview button.
        """
        if button:
            print_d(_('Stopping synchronization preview'))
            self.status_operation.set_label(
                _('Synchronization preview was stopped.'))
        else:
            self.status_operation.set_label(
                _('Synchronization preview has finished.'))
        self.status_operation.set_visible(True)
        self.running = False

        # Change button visibility
        self.preview_start_button.set_visible(True)
        self.preview_stop_button.set_visible(False)

        self._update_preview_summary()

    def _run_preview(self):
        """
        Show the export paths for all songs to be synchronized.

        :return: Whether the generation of preview paths was successful.
        """
        destination_path, pattern = self._get_valid_inputs()
        if None in {destination_path, pattern}:
            return False
        self.expanded_destination = os.path.expanduser(destination_path)

        # Get a list containing all songs to export
        songs = self._get_songs_from_queries()
        if not songs:
            return False
        self.model.clear()
        export_paths = []

        for song in songs:
            if not self.running:
                print_d(_('Stopped synchronization preview'))
                return None
            self._run_pending_events()
            if not self.destination_entry.get_text():
                print_d(_('A different plugin was selected - stop preview'))
                return False

            export_path = self._get_export_path(song, destination_path, pattern)
            if not export_path:
                return False

            entry = Entry(song, export_path)

            expanded_path = os.path.expanduser(export_path)
            if expanded_path in export_paths:
                entry.tag = Entry.Tags.SKIP_DUPLICATE
                self.c_song_dupes += 1
            else:
                entry.tag = Entry.Tags.PENDING_COPY
                self.c_songs_copy += 1
                export_paths.append(expanded_path)

            self.model.append(row=self._make_model_row(entry))

        # List files to delete
        for root, __, files in os.walk(self.expanded_destination):
            for name in files:
                file_path = os.path.join(root, name)
                if file_path not in export_paths:
                    entry = Entry(None)
                    entry.filename = file_path
                    entry.tag = Entry.Tags.PENDING_DELETE
                    self.model.append(row=self._make_model_row(entry))
                    self.c_songs_delete += 1

        return True

    def _update_preview_summary(self):
        """
        Update the preview summary text field.
        """
        prefix = _('Synchronization will:') + self.summary_sep
        preview_progress = []

        if self.c_songs_copy > 0:
            counter = self.c_songs_copy
            preview_progress.append(
                ngettext('attempt to write {count} file',
                         'attempt to write {count} files',
                         counter).format(count=counter))

        if self.c_song_dupes > 0:
            counter = self.c_song_dupes
            preview_progress.append(
                ngettext('skip {count} duplicate file',
                         'skip {count} duplicate files',
                         counter).format(count=counter))
            for child in self.status_duplicates.get_children():
                child.set_visible(True)
            self.status_duplicates.set_visible(True)

        if self.c_songs_delete > 0:
            counter = self.c_songs_delete
            preview_progress.append(
                ngettext('delete {count} file',
                         'delete {count} files',
                         counter).format(count=counter))
            for child in self.status_deletions.get_children():
                child.set_visible(True)
            self.status_deletions.set_visible(True)

        preview_progress_text = self.summary_sep_list.join(preview_progress)
        if preview_progress_text:
            preview_progress_text = prefix + preview_progress_text
            self.status_progress.set_label(preview_progress_text)
            self.status_progress.set_visible(True)
            print_d(preview_progress_text)

    def _get_paths(self):
        """
        Build a list of all current export paths for the songs to be
        synchronized.
        """
        paths = {}
        for row in self.model:
            entry = row[self._model_col_id('entry')]
            if entry.tag != Entry.Tags.PENDING_DELETE and entry.export_path:
                if entry.export_path not in paths.keys():
                    paths[entry.export_path] = 1
                else:
                    paths[entry.export_path] += 1
        return paths

    def _show_sync_error(self, title, message):
        """
        Show an error message whenever a synchronization error occurs.

        :param title:   The title of the message popup.
        :param message: The error message.
        """
        qltk.ErrorMessage(self.main_vbox, title, message).run()
        print_e(title)

    def _get_valid_inputs(self):
        """
        Ensure that all user inputs have been given. Shows a popup error message
        if values are not as expected.

        :return: The entered destination path and an fsnative pattern,
                 or None if an error occurred.
        """
        # Get text from the destination path entry
        destination_path = self.destination_entry.get_text()
        if not destination_path:
            self._show_sync_error(_('No destination path provided'),
                                  _('Please specify the directory where songs '
                                    'should be exported.'))
            return None, None

        # Get text from the export pattern entry
        export_pattern = self.export_pattern_entry.get_text()
        if not export_pattern:
            self._show_sync_error(_('No export pattern provided'),
                                  _('Please specify an export pattern for the '
                                    'names of the exported songs.'))
            return None, None

        # Combine destination path and export pattern to form the full pattern
        full_export_path = os.path.join(destination_path, export_pattern)
        try:
            pattern = FileFromPattern(full_export_path)
        except ValueError:
            self._show_sync_error(
                _('Export path is not absolute'),
                _('The pattern\n\n<b>{}</b>\n\ncontains "/" but does not start '
                  'from root. Please provide an absolute destination path by '
                  'making sure it starts with / or ~/.')
                .format(util.escape(full_export_path)))
            return None, None

        return destination_path, pattern

    def _get_songs_from_queries(self):
        """
        Build a list of songs to be synchronized, filtered using the
        selected saved searches.

        :return: A list of the selected songs.
        """
        enabled_queries = []
        for query_name, query in self.queries.items():
            query_config = self.CONFIG_QUERY_PREFIX + query_name
            if self.config_get_bool(query_config):
                enabled_queries.append(query)

        if not enabled_queries:
            self._show_sync_error(_('No saved searches selected'),
                                  _('Please select at least one saved search.'))
            return []

        selected_songs = []
        for song in app.library.itervalues():
            if any(query.search(song) for query in enabled_queries):
                selected_songs.append(song)

        if not selected_songs:
            self._show_sync_error(_('No songs in the selected saved searches'),
                                  _('All selected saved searches are empty.'))
            return []

        print_d(_('Found {} songs to synchronize').format(len(selected_songs)))
        return selected_songs

    def _get_export_path(self, song, destination_path, export_pattern):
        """
        Use the given pattern of song tags to build the destination path
        for a song.

        :param song:             The song for which to build the export path.
        :param destination_path: The user-entered destination path.
        :param export_pattern:   An fsnative file path pattern.
        :return: A safe full destination path for the song.
        """
        new_name = Path(export_pattern.format(song))

        try:
            relative_name = new_name.relative_to(self.expanded_destination)
        except ValueError as ex:
            self._show_sync_error(
                _('Mismatch between destination path and export '
                  'pattern'),
                _('The export pattern starts with a path that '
                  'differs from the destination path. Please '
                  'correct the pattern.\n\nError:\n{}').format(ex))
            return None

        return os.path.join(destination_path,
                            self._make_safe_name(relative_name))

    def _make_safe_name(self, input_path):
        """
        Make a file path safe by replacing unsafe characters.

        :param input_path: A relative Path.
        :return: The given path, with any unsafe characters replaced.
                 Returned as a string.
        """
        # Remove diacritics (accents)
        safe_filename = unicodedata.normalize('NFKD', str(input_path))
        safe_filename = u''.join(
            [c for c in safe_filename if not unicodedata.combining(c)])

        if os.name != "nt":
            # Ensure that Win32-incompatible chars are always removed.
            # On Windows, this is called during `FileFromPattern`.
            safe_filename = strip_win32_incompat_from_path(safe_filename)

        return safe_filename

    def _start_sync(self, button):
        """
        Start the song synchronization.

        :param button: The start sync button.
        """
        # Check sort column
        sort_columns = [c.get_title() for c in self.details_tree.get_columns()
                        if c.get_sort_indicator()]
        if 'Status' in sort_columns:
            self._show_sync_error(_('Unable to sync'),
                                  _('Cannot start synchronization while '
                                    'sorting by <b>Status</b>.'))
            return

        print_d(_('Starting song synchronization'))
        self.running = True

        # Summary labels
        self.status_operation.set_label(_('Synchronization in progress.'))
        self.status_duplicates.set_visible(False)
        self.status_deletions.set_visible(False)

        # Change button visibility
        self.sync_start_button.set_visible(False)
        self.sync_stop_button.set_visible(True)

        if not self._run_sync():
            return

        self._stop_sync()
        print_d(_('Finished song synchronization'))

    def _stop_sync(self, button=None):
        """
        Stop the song synchronization.

        :param button: The stop sync button.
        """
        if button:
            print_d(_('Stopping song synchronization'))
            self.status_operation.set_label(_('Synchronization was stopped.'))
        else:
            self.status_operation.set_label(_('Synchronization has finished.'))

        self.running = False

        # Change button visibility
        self.sync_start_button.set_visible(True)
        self.sync_stop_button.set_visible(False)

    def _run_sync(self):
        """
        Synchronize the songs from the selected saved searches
        with the specified folder.

        :return: Whether the synchronization was successful.
        """
        self.c_files_copy = self.c_files_skip = self.c_files_skip_previous \
            = self.c_files_dupes = self.c_files_delete = self.c_files_failed = 0
        self.model.foreach(self._sync_entry)
        if not self.running:
            return False
        self._remove_empty_dirs()
        return True

    def _sync_entry(self, model, path, iter_, *data):
        """
        Synchronize a single song.
        This is a callback function passed to Gtk.TreeModel.foreach() to iterate
        over the rows in a tree model.

        :return: True to stop iterating, False to continue.
        """
        entry = model[path][self._model_col_id('entry')]
        if not self.running:
            print_d(_('Stopped song synchronization'))
            return True
        self._run_pending_events()
        if not self.destination_entry.get_text():
            print_d(_('A different plugin was selected - stop synchronization'))
            return True

        print_d(_('{tag} - "{filename}"').format(tag=entry.tag,
                                                 filename=entry.filename))

        if not entry.export_path and not entry.tag:
            return False

        if entry.tag == Entry.Tags.PENDING_COPY:
            # Export, skipping existing files
            expanded_path = os.path.expanduser(entry.export_path)
            if os.path.exists(expanded_path):
                entry.tag = Entry.Tags.RESULT_SKIP_EXISTING
                self._update_model_value(iter_, 'tag', entry.tag)
                self.c_files_skip += 1
            else:
                entry.tag = Entry.Tags.IN_PROGRESS_SYNC
                self._update_model_value(iter_, 'tag', entry.tag)

                song_folders = os.path.dirname(expanded_path)
                os.makedirs(song_folders, exist_ok=True)
                try:
                    shutil.copyfile(entry.filename, expanded_path)
                except Exception as ex:
                    entry.tag = Entry.Tags.RESULT_FAILURE + ': ' + str(ex)
                    self._update_model_value(iter_, 'tag', entry.tag)
                    print_exc()
                    self.c_files_failed += 1
                else:
                    entry.tag = Entry.Tags.RESULT_SUCCESS
                    self._update_model_value(iter_, 'tag', entry.tag)
                    self.c_files_copy += 1

        elif entry.tag == Entry.Tags.SKIP_DUPLICATE:
            self.c_files_dupes += 1

        elif entry.tag == Entry.Tags.PENDING_DELETE:
            # Delete file
            try:
                entry.tag = Entry.Tags.IN_PROGRESS_DELETE
                self._update_model_value(iter_, 'tag', entry.tag)

                os.remove(entry.filename)
            except Exception as ex:
                entry.tag = Entry.Tags.RESULT_FAILURE + ': ' + str(ex)
                self._update_model_value(iter_, 'tag', entry.tag)
                print_exc()
                self.c_files_failed += 1
            else:
                entry.tag = Entry.Tags.RESULT_SUCCESS
                self._update_model_value(iter_, 'tag', entry.tag)
                self.c_files_delete += 1

        else:
            self.c_files_skip_previous += 1

        self._update_sync_summary()
        return False

    def _remove_empty_dirs(self):
        """
        Delete all empty sub-directories from the given path.
        """
        for root, dirs, __ in os.walk(self.expanded_destination, topdown=False):
            for dirname in dirs:
                dir_path = os.path.realpath(os.path.join(root, dirname))
                if not os.listdir(dir_path):
                    entry = Entry(None)
                    entry.filename = dir_path
                    entry.tag = Entry.Tags.IN_PROGRESS_DELETE
                    iter_ = self.model.append(row=self._make_model_row(entry))
                    print_d(_('Removing "{}"').format(entry.filename))
                    self.c_songs_delete += 1
                    try:
                        os.rmdir(dir_path)
                    except Exception as ex:
                        entry.tag = Entry.Tags.RESULT_FAILURE + ': ' + str(ex)
                        self._update_model_value(iter_, 'tag', entry.tag)
                        print_exc()
                        self.c_files_failed += 1
                    else:
                        entry.tag = Entry.Tags.RESULT_SUCCESS
                        self._update_model_value(iter_, 'tag', entry.tag)
                        self.c_files_delete += 1
                    self._update_sync_summary()

    def _update_sync_summary(self):
        """
        Update the synchronization summary text field.
        """
        sync_summary_prefix = _('Synchronization has:') + self.summary_sep
        sync_summary = []

        if self.c_files_copy > 0 or self.c_files_skip > 0:
            text = []

            counter = self.c_files_copy
            text.append(
                ngettext('written {count}/{total} file',
                         'written {count}/{total} files',
                         counter).format(count=counter, total=self.c_songs_copy))

            if self.c_files_skip > 0:
                counter = self.c_files_skip
                text.append(
                    ngettext('(skipped {count} existing file)',
                             '(skipped {count} existing files)',
                             counter).format(count=counter))

            sync_summary.append(self.summary_sep.join(text))

        if self.c_files_dupes > 0:
            counter = self.c_files_dupes
            sync_summary.append(
                ngettext('skipped {count}/{total} duplicate file',
                         'skipped {count}/{total} duplicate files',
                         counter).format(count=counter, total=self.c_song_dupes))

        if self.c_files_delete > 0:
            counter = self.c_files_delete
            sync_summary.append(
                ngettext('deleted {count}/{total} file',
                         'deleted {count}/{total} files',
                         counter).format(count=counter, total=self.c_songs_delete))

        if self.c_files_failed > 0:
            counter = self.c_files_failed
            sync_summary.append(
                ngettext('failed to sync {count} file',
                         'failed to sync {count} files',
                         counter).format(count=counter))

        if self.c_files_skip_previous > 0:
            counter = self.c_files_skip_previous
            sync_summary.append(
                ngettext('skipped {count} file synchronized previously',
                         'skipped {count} files synchronized previously',
                         counter).format(count=counter))

        sync_summary_text = self.summary_sep_list.join(sync_summary)
        sync_summary_text = sync_summary_prefix + sync_summary_text
        self.status_progress.set_label(sync_summary_text)
        print_d(sync_summary_text)
