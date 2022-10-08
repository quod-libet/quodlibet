# Copyright 2020 Daniel Petrescu
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
from os import makedirs
from pathlib import Path
from unittest.mock import ANY, patch

from gi.repository import Gtk

from quodlibet import app
from quodlibet import config
from quodlibet import library
from quodlibet import get_cache_dir
from quodlibet.formats import AudioFile
from quodlibet.plugins import PM
from quodlibet.qltk.ccb import ConfigCheckButton
from quodlibet.util.path import strip_win32_incompat_from_path
from tests.plugin import PluginTestCase


QUERIES = {
    'Directory': {'query': '~dirname="/dev/null"',
                  'terms': ('/dev/null',), 'results': 5},
    '2 artists': {'query': 'artist=|("Group1","Group2")',
                  'terms': ('Group',), 'results': 4},
    'No songs': {'query': '#(length < 0)',
                 'terms': (), 'results': 0},
    'Symbols': {'query': '~dirname="/tmp/new"',
                'terms': ('/tmp/new',), 'results': 1}
}

PATTERNS = [
    '<title> - <artist> - <album>',
    '<title><albumartist| - <albumartist>|<artist| - <artist>>>'
]

SONGS = [
    AudioFile({'~filename': '/dev/null/Song1.mp3',
               'title': 'Song1', 'artist': 'Artist1', 'album': 'Album1'}),
    AudioFile({'~filename': '/dev/null/Song2.mp3',
               'title': 'Song2', 'artist': 'Artist1', 'album': 'Album1'}),
    AudioFile({'~filename': '/dev/null/Song3.mp3',
               'title': 'Song3', 'artist': 'Artist1', 'album': 'Album2'}),
    AudioFile({'~filename': '/dev/null/Song4.mp3',
               'title': 'Song4', 'artist': 'Artist2', 'album': 'Album2'}),
    AudioFile({'~filename': '/dev/null/Song5.mp3',
               'title': 'Song5', 'artist': 'Artist2', 'album': 'Album2'}),
    AudioFile({'~filename': '/tmp/music/Song5.mp3',
               'title': 'Song5', 'artist': 'Artist2', 'album': 'Album2'}),
    AudioFile({'~filename': '/tmp/music/Track1.mp3',
               'title': 'Track1', 'artist': 'Group1', 'album': 'Album3'}),
    AudioFile({'~filename': '/tmp/music/Track2-1.mp3',
               'title': 'Track2', 'artist': 'Group1', 'album': 'Album3'}),
    AudioFile({'~filename': '/tmp/music/Track2-2.mp3',
               'title': 'Track2', 'artist': 'Group2', 'album': 'Album4'}),
    AudioFile({'~filename': '/tmp/music/Track3.mp3',
               'title': 'Track3', 'artist': 'Group2', 'album': 'Album4'}),
    AudioFile({'~filename': '/tmp/new/',
               'title': 'Abc123 (~!@#$%^&*|:\'",.\\/?+=;)',
               'artist': r'[ÆÁàçÈéöø] <αΔλΛ> Привет こんにちわ مرحبا',
               'album': r'{‰} → A∩B≥3 ⎈Ⓐ ░ ☔☃☂ ♂♀🤴 😀🎧 🪐👽🖖'})
]


class TSyncToDevice(PluginTestCase):

    QUERIES_SAVED = '\n'.join([details['query'] + '\n' + name
                               for name, details in QUERIES.items()])
    RENAMEPATTERNS = '\n'.join(PATTERNS)

    @classmethod
    def setUpClass(cls):
        plugin_id = 'synchronize_to_device'
        cls.module = cls.modules[plugin_id]
        cls.plugin = cls.module.SyncToDevice()
        cls.gtk_window = Gtk.Window()

    @classmethod
    def tearDownClass(cls):
        cls.gtk_window.destroy()
        del cls.plugin
        del cls.module

    def setUp(self):
        path_query = Path(self.plugin.path_query)
        path_query.parent.mkdir(parents=True, exist_ok=True)
        with open(self.plugin.path_query, 'w') as f:
            f.write(self.QUERIES_SAVED)

        path_pattern = Path(self.plugin.path_pattern)
        path_pattern.parent.mkdir(parents=True, exist_ok=True)
        with open(self.plugin.path_pattern, 'w') as f:
            f.write(self.RENAMEPATTERNS)

        path_dest = Path(get_cache_dir(), 'export')
        path_dest.mkdir(parents=True, exist_ok=True)
        self.path_dest = str(path_dest)

        self.main_vbox = self._start_plugin()

        self.searches = {}
        for button in self.plugin.saved_search_vbox.get_children():
            self.searches[button] = button.get_active()

        self.dest_entry = self.plugin.destination_entry
        self.path_dest_old_text = self.dest_entry.get_text()

        self.pattern_entry = self.plugin.export_pattern_entry
        self.pattern_old_text = self.pattern_entry.get_text()

        self.Tags = self.module.Entry.Tags

    def tearDown(self):
        for button, value in self.searches.items():
            button.set_active(value)
        self.dest_entry.set_text(self.path_dest_old_text)
        self.pattern_entry.set_text(self.pattern_old_text)

        if os.path.exists(self.plugin.path_query):
            os.remove(self.plugin.path_query)
        if os.path.exists(self.plugin.path_pattern):
            os.remove(self.plugin.path_pattern)

        for root, dirs, files in os.walk(self.path_dest, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))

    def _start_plugin(self):
        return self.plugin.PluginPreferences(self.gtk_window)

    def _make_query_config(self, label):
        return self.module.PLUGIN_CONFIG_SECTION + '_' \
               + self.plugin.CONFIG_QUERY_PREFIX \
               + label.lower()

    def _select_searches(self, *labels):
        for button in self.plugin.saved_search_vbox.get_children():
            if button.get_label() in labels:
                button.set_active(True)

    def _make_library(self, add_songs=True):
        app.library = library.init()
        if add_songs:
            app.library.add(SONGS)

    def _make_files_for_deletion(self, *files):
        if not files:
            files = ['song1.mp3', 'file1.txt', 'file2.csv']
        for f in files:
            file_path = os.path.join(self.path_dest, f)
            song_folders = os.path.dirname(file_path)
            makedirs(song_folders, exist_ok=True)
            with open(file_path, 'w'):
                # Create a blank file
                pass
        return len(files)

    def _verify_child(self, model, path, iter_, *data):
        tag = model[path][self.plugin._model_col_id('tag')]
        self.assertTrue(
            any(tag.startswith(tag) for tag in data[0]),
            'Song status "{}" does not start with "{}"'.format(tag, data[0]))

        filename = model[path][self.plugin._model_col_id('filename')]
        self.assertIsNotNone(filename, "filename field shouldn't be None")
        self.assertNotEqual(filename, '', "filename field shouldn't be empty")

        export = model[path][self.plugin._model_col_id('export')]
        self.assertTrue(
            any(export.startswith(export_path) for export_path in data[1]),
            'Export path "{}" does not start with "{}"'.format(export, data[1]))

        song = model[path][self.plugin._model_col_id('entry')]._song
        if song and data[2] and data[3]:
            self.assertTrue(song[data[2]].startswith(data[3]),
                'Data in given field "{}" does not start with {}'.format(
                    song[data[2]], data[3]))

        return False

    def _model_set_value(self, column_name, cell_id, value):
        col_id = self.plugin._model_col_id(column_name) - 1
        column = self.plugin.details_tree.get_column(col_id)
        self.plugin.renders[column].emit('edited', cell_id, value)

    def _model_get_value(self, cell_id, column):
        iter_ = self.plugin.model.get_iter(cell_id)
        return self.plugin.model.get_value(
            iter_, self.plugin._model_col_id(column))

    def _model_remove_by_tag(self, rm_tag):
        iter_ = self.plugin.model.get_iter_first()
        while iter_:
            entry_tag = self.plugin.model.get_value(
                iter_, self.plugin._model_col_id('tag'))
            if entry_tag == rm_tag:
                self.plugin.model.remove(iter_)
            else:
                iter_ = self.plugin.model.iter_next(iter_)

    def _mark_song_unique(self, cell_edit, new_text='updated path', check=True):
        self._model_set_value('export', cell_edit, new_text)
        if check:
            self.assertEqual(self._model_get_value(cell_edit, 'export'),
                             new_text)
            self.assertEqual(self._model_get_value(cell_edit, 'tag'),
                             self.Tags.PENDING_COPY)

    def _mark_song_duplicate(self, cell_edit, cell_copy, check=True):
        new_text = self._model_get_value(cell_copy, 'export')
        self._model_set_value('export', cell_edit, new_text)
        if check:
            self.assertEqual(self._model_get_value(cell_edit, 'export'),
                             new_text)
            self.assertEqual(self._model_get_value(cell_edit, 'tag'),
                             self.Tags.SKIP_DUPLICATE)

    def _mark_song_delete(self, cell_edit, check=True):
        new_text = self.Tags.DELETE
        self._model_set_value('export', cell_edit, new_text)
        if check:
            self.assertEqual(self._model_get_value(cell_edit, 'export'), '')
            self.assertEqual(self._model_get_value(cell_edit, 'tag'),
                             self.Tags.SKIP)

    def _mark_song_empty(self, cell_edit, check=True):
        new_text = ''
        self._model_set_value('export', cell_edit, new_text)
        if check:
            self.assertEqual(self._model_get_value(cell_edit, 'export'),
                             new_text)
            self.assertEqual(self._model_get_value(cell_edit, 'tag'),
                             self.Tags.SKIP)

    def test_pluginpreferences_missing_saved_queries_file(self):
        os.remove(self.plugin.path_query)
        self.main_vbox = self._start_plugin()
        self.assertFalse(os.path.exists(self.plugin.path_query))
        self.assertEqual(type(self.main_vbox), Gtk.Frame)

    def test_pluginpreferences_no_saved_queries(self):
        with open(self.plugin.path_query, 'w') as f:
            f.write('')
        self.main_vbox = self._start_plugin()
        self.assertEqual(type(self.main_vbox), Gtk.Frame)

    def test_pluginpreferences_success(self):
        self.assertEqual(type(self.main_vbox), Gtk.VBox)

        self.assertEqual(len(self.plugin.queries), len(QUERIES))
        self.assertTrue(all(isinstance(button, ConfigCheckButton) for button in
                            self.plugin.saved_search_vbox.get_children()))
        self.assertFalse(any(button.get_active() for button in
                             self.plugin.saved_search_vbox.get_children()))

        self.assertNotEqual(
            self.plugin.destination_entry.get_placeholder_text(), '')
        self.assertEqual(self.plugin.destination_entry.get_text(), '')

        self.assertNotEqual(
            self.plugin.export_pattern_entry.get_placeholder_text(), '')
        self.assertNotEqual(self.plugin.export_pattern_entry.get_text(), '')

        n_cols = self.plugin.model.get_n_columns()
        self.assertEqual(n_cols, len(self.plugin.model_cols))

        self.assertTrue(self.plugin.preview_start_button.get_visible())
        self.assertFalse(self.plugin.preview_stop_button.get_visible())
        self.assertFalse(self.plugin.status_operation.get_visible())
        self.assertEqual(self.plugin.status_operation.get_text(), '')
        self.assertFalse(self.plugin.status_progress.get_visible())
        self.assertEqual(self.plugin.status_progress.get_text(), '')
        self.assertFalse(self.plugin.status_duplicates.get_visible())
        self.assertFalse(self.plugin.status_deletions.get_visible())

        self.assertTrue(self.plugin.sync_start_button.get_visible())
        self.assertFalse(self.plugin.sync_stop_button.get_visible())

    def test_select_saved_search(self):
        button = self.plugin.saved_search_vbox.get_children()[0]

        button.set_active(True)
        self.assertTrue(button.get_active())
        query_config = self._make_query_config(button.get_label())
        self.assertTrue(
            config.getboolean(PM.CONFIG_SECTION, query_config, None))

    def test_destination_path_changed(self):
        self.dest_entry.set_text(self.path_dest)
        self.assertEqual(self.dest_entry.get_text(), self.path_dest)
        self.assertEqual(
            config.get(PM.CONFIG_SECTION, self.plugin.CONFIG_PATH_KEY, None),
            self.path_dest)

    def test_export_pattern_changed(self):
        pattern_new_text = PATTERNS[0]
        self.pattern_entry.set_text(pattern_new_text)
        self.assertEqual(self.pattern_entry.get_text(), pattern_new_text)
        self.assertEqual(
            config.get(PM.CONFIG_SECTION, self.plugin.CONFIG_PATTERN_KEY, None),
            pattern_new_text)

    @patch('quodlibet.qltk.ErrorMessage')
    def test_start_preview_no_searches_selected(self, mock_message):
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        mock_message.assert_called_once_with(
            self.main_vbox, 'No saved searches selected', ANY)

    @patch('quodlibet.qltk.ErrorMessage')
    def test_start_preview_no_destination_path(self, mock_message):
        self._select_searches('Directory')

        self.plugin._start_preview(self.plugin.preview_start_button)
        mock_message.assert_called_once_with(
            self.main_vbox, 'No destination path provided', ANY)

    @patch('quodlibet.qltk.ErrorMessage')
    def test_start_preview_no_export_pattern(self, mock_message):
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.pattern_entry.set_text('')

        self.plugin._start_preview(self.plugin.preview_start_button)
        mock_message.assert_called_once_with(
            self.main_vbox, 'No export pattern provided', ANY)

    @patch('quodlibet.qltk.ErrorMessage')
    def test_start_preview_export_path_not_absolute(self, mock_message):
        self._select_searches('Directory')
        self.dest_entry.set_text('./path')

        self.plugin._start_preview(self.plugin.preview_start_button)
        mock_message.assert_called_once_with(
            self.main_vbox, 'Export path is not absolute', ANY)

    @patch('quodlibet.qltk.ErrorMessage')
    def test_start_preview_no_songs(self, mock_message):
        self._make_library()
        self._select_searches('No songs')
        self.dest_entry.set_text(self.path_dest)

        self.plugin._start_preview(self.plugin.preview_start_button)
        mock_message.assert_called_once_with(
            self.main_vbox, 'No songs in the selected saved searches', ANY)

    @patch('quodlibet.qltk.ErrorMessage')
    def test_start_preview_path_pattern_mismatch(self, mock_message):
        self._make_library()

        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.pattern_entry.set_text(
            str(Path('/dev/null', self.plugin.default_export_pattern)))

        self.plugin._start_preview(self.plugin.preview_start_button)
        mock_message.assert_called_once_with(self.main_vbox,
            'Mismatch between destination path and export pattern', ANY)

    @patch('quodlibet.qltk.ErrorMessage')
    def test_start_preview_destination_in_pattern(self, mock_message):
        self._make_library()

        query_name = 'Directory'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)
        self.pattern_entry.set_text(
            str(Path(self.path_dest, self.plugin.default_export_pattern)))

        self.plugin._start_preview(self.plugin.preview_start_button)
        mock_message.assert_not_called()

        self.assertTrue(self.plugin.status_progress.get_visible())
        self.assertNotEqual(self.plugin.status_progress.get_text(), '')
        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, QUERIES[query_name]['results'])
        query_terms = QUERIES[query_name]['terms']
        self.plugin.model.foreach(self._verify_child,
            [self.Tags.PENDING_COPY], [self.path_dest],
            '~filename', query_terms)

    def test_start_preview_queries_directory(self):
        self._make_library()

        query_name = 'Directory'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)

        self.plugin._start_preview(self.plugin.preview_start_button)

        self.assertTrue(self.plugin.status_progress.get_visible())
        self.assertNotEqual(self.plugin.status_progress.get_text(), '')
        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, QUERIES[query_name]['results'])
        query_terms = QUERIES[query_name]['terms']
        self.plugin.model.foreach(self._verify_child,
            [self.Tags.PENDING_COPY], [self.path_dest],
            '~filename', query_terms)

    def test_start_preview_queries_artists(self):
        self._make_library()

        query_name = '2 artists'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)

        self.plugin._start_preview(self.plugin.preview_start_button)

        self.assertTrue(self.plugin.status_progress.get_visible())
        self.assertNotEqual(self.plugin.status_progress.get_text(), '')
        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, QUERIES[query_name]['results'])
        query_terms = QUERIES[query_name]['terms']
        self.plugin.model.foreach(self._verify_child,
            [self.Tags.PENDING_COPY], [self.path_dest],
            'artist', query_terms)

    def test_start_preview_queries_multiple(self):
        self._make_library()

        queries = ('Directory', '2 artists')
        self._select_searches(*queries)
        self.dest_entry.set_text(self.path_dest)
        n_expected = 0
        for query in queries:
            n_expected += QUERIES[query]['results']

        self.plugin._start_preview(self.plugin.preview_start_button)

        self.assertTrue(self.plugin.status_progress.get_visible())
        self.assertNotEqual(self.plugin.status_progress.get_text(), '')
        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, n_expected)

        self.assertFalse(self.plugin.status_duplicates.get_visible())
        self.assertFalse(self.plugin.status_deletions.get_visible())

    def test_start_preview_export_path_check(self):
        def _verify_path(model, path, iter_, *data):
            song = model[path][self.plugin._model_col_id('entry')]._song
            expected_path = data[0]
            for part in data[1:]:
                field = part.replace('<', '').replace('>', '')
                expected_path = os.path.join(expected_path, song[field])
            expected_path += '.mp3'
            export_path = model[path][self.plugin._model_col_id('export')]
            self.assertEqual(export_path, expected_path)
            return False

        self._make_library()

        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        pattern = Path(self.plugin.export_pattern_entry.get_text()).parts

        self.plugin._start_preview(self.plugin.preview_start_button)
        self.plugin.model.foreach(_verify_path, self.path_dest, *pattern)

    def test_start_preview_pattern_custom_text(self):
        def _verify_path(model, path, iter_, *data):
            song = model[path][self.plugin._model_col_id('entry')]._song
            expected_path = os.path.join(data[0], 'A ' + song['artist'],
                                         '_' + song['title'] + '_',
                                         'Unknown' + '.mp3')
            export_path = model[path][self.plugin._model_col_id('export')]
            self.assertEqual(export_path, expected_path)
            return False

        self._make_library()

        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        pattern = str(Path('A <artist>', '_<title>_',
                           '<albumartist|<albumartist>|Unknown>'))
        self.pattern_entry.set_text(pattern)

        self.plugin._start_preview(self.plugin.preview_start_button)
        self.plugin.model.foreach(_verify_path, self.path_dest)

    def test_start_preview_file_deletion(self):
        self._make_library()
        num_files = self._make_files_for_deletion()

        self._select_searches('Symbols')
        self.dest_entry.set_text(self.path_dest)

        self.plugin._start_preview(self.plugin.preview_start_button)
        self._model_remove_by_tag(self.Tags.PENDING_COPY)

        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, num_files)

        self.assertFalse(self.plugin.status_duplicates.get_visible())
        self.assertTrue(self.plugin.status_deletions.get_visible())

    def test_start_preview_query_and_file_deletion(self):
        self._make_library()
        num_files = self._make_files_for_deletion()

        query_name = 'Directory'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)

        self.plugin._start_preview(self.plugin.preview_start_button)

        n_children = self.plugin.model.iter_n_children(None)
        n_expected = QUERIES[query_name]['results'] + num_files
        self.assertEqual(n_children, n_expected)

    def test_start_preview_unicode_basic_latin(self):
        # Characters in the range 0x0021 - 0x007E
        self._make_library()

        query_name = 'Symbols'
        self._select_searches(query_name)
        path_dest = os.path.join(self.path_dest, 'Привет')
        self.dest_entry.set_text(path_dest)
        self.pattern_entry.set_text('<title>')

        self.plugin._start_preview(self.plugin.preview_start_button)

        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, QUERIES[query_name]['results'])

        export_path = self._model_get_value(0, 'export')
        expected_value = strip_win32_incompat_from_path(export_path)
        expected_path = str(Path(path_dest, expected_value))
        self.assertEqual(export_path, expected_path)

    def test_start_preview_unicode_various_languages(self):
        # Characters in: 0x00A0 - 0x04FF, 0x3040 - 0x309F, 0x0600 - 0x06FF
        self._make_library()

        query_name = 'Symbols'
        self._select_searches(query_name)
        path_dest = os.path.join(self.path_dest, 'こんにちわ')
        self.dest_entry.set_text(path_dest)
        self.pattern_entry.set_text('<artist>')

        self.plugin._start_preview(self.plugin.preview_start_button)

        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, QUERIES[query_name]['results'])

        export_path = self._model_get_value(0, 'export')
        expected_value = strip_win32_incompat_from_path(export_path)
        expected_path = str(Path(path_dest, expected_value))
        self.assertEqual(export_path, expected_path)

    def test_start_preview_unicode_other_symbols(self):
        # Characters in the range 0x2030 - 0x1F47E
        self._make_library()

        query_name = 'Symbols'
        self._select_searches(query_name)
        path_dest = os.path.join(self.path_dest, 'مرحبا')
        self.dest_entry.set_text(path_dest)
        self.pattern_entry.set_text('<album>')

        self.plugin._start_preview(self.plugin.preview_start_button)

        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, QUERIES[query_name]['results'])

        export_path = self._model_get_value(0, 'export')
        expected_value = strip_win32_incompat_from_path(export_path)
        expected_path = str(Path(path_dest, expected_value))
        self.assertEqual(export_path, expected_path)

    def test_row_edited_unique_to_unique(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        cell_id_edit = 0
        self._mark_song_unique(cell_id_edit, 'initial value')

        old_c_songs_copy = self.plugin.c_songs_copy
        self._mark_song_unique(cell_id_edit)
        self.assertEqual(self.plugin.c_songs_copy, old_c_songs_copy)

    def test_row_edited_unique_to_duplicate(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        cell_id_edit = 0
        cell_id_copy = 1
        self._mark_song_unique(cell_id_edit, 'initial value')

        old_c_songs_copy = self.plugin.c_songs_copy
        old_c_song_dupes = self.plugin.c_song_dupes
        self._mark_song_duplicate(cell_id_edit, cell_id_copy)
        self.assertEqual(self.plugin.c_songs_copy, old_c_songs_copy - 1)
        self.assertEqual(self.plugin.c_song_dupes, old_c_song_dupes + 1)

    def test_row_edited_unique_to_delete(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        cell_id_edit = 0
        self._mark_song_unique(cell_id_edit, 'initial value')

        old_c_songs_copy = self.plugin.c_songs_copy
        old_c_songs_delete = self.plugin.c_songs_delete
        self._mark_song_delete(cell_id_edit)
        self.assertEqual(self.plugin.c_songs_copy, old_c_songs_copy - 1)
        self.assertEqual(self.plugin.c_songs_delete, old_c_songs_delete)

    def test_row_edited_unique_to_empty(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        cell_id_edit = 0
        self._mark_song_unique(cell_id_edit, 'initial value')

        old_c_songs_copy = self.plugin.c_songs_copy
        old_c_songs_delete = self.plugin.c_songs_delete
        self._mark_song_empty(cell_id_edit)
        self.assertEqual(self.plugin.c_songs_copy, old_c_songs_copy - 1)
        self.assertEqual(self.plugin.c_songs_delete, old_c_songs_delete)

    def test_row_edited_duplicate_to_unique(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        cell_id_edit = 0
        cell_id_copy = 1
        self._mark_song_duplicate(cell_id_edit, cell_id_copy)
        self.assertTrue(self.plugin.status_duplicates.get_visible())

        old_c_songs_copy = self.plugin.c_songs_copy
        old_c_song_dupes = self.plugin.c_song_dupes
        self._mark_song_unique(cell_id_edit)
        self.assertEqual(self.plugin.c_songs_copy, old_c_songs_copy + 1)
        self.assertEqual(self.plugin.c_song_dupes, old_c_song_dupes - 1)
        self.assertFalse(self.plugin.status_duplicates.get_visible())

    def test_row_edited_duplicate_to_duplicate(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        cell_id_edit = 0
        cell_id_copy_1 = 1
        cell_id_copy_2 = 2
        self._mark_song_duplicate(cell_id_edit, cell_id_copy_1)

        old_c_song_dupes = self.plugin.c_song_dupes
        self._mark_song_duplicate(cell_id_edit, cell_id_copy_2)
        self.assertEqual(self.plugin.c_song_dupes, old_c_song_dupes)

        self.assertTrue(self.plugin.status_duplicates.get_visible())
        self.assertFalse(self.plugin.status_deletions.get_visible())

    def test_row_edited_duplicate_to_delete(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        cell_id_edit = 0
        cell_id_copy = 1
        self._mark_song_duplicate(cell_id_edit, cell_id_copy)

        old_c_song_dupes = self.plugin.c_song_dupes
        old_c_songs_delete = self.plugin.c_songs_delete
        self._mark_song_delete(cell_id_edit)
        self.assertEqual(self.plugin.c_song_dupes, old_c_song_dupes - 1)
        self.assertEqual(self.plugin.c_songs_delete, old_c_songs_delete)

    def test_row_edited_duplicate_to_empty(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        cell_id_edit = 0
        cell_id_copy = 1
        self._mark_song_duplicate(cell_id_edit, cell_id_copy)

        old_c_song_dupes = self.plugin.c_song_dupes
        old_c_songs_delete = self.plugin.c_songs_delete
        self._mark_song_empty(cell_id_edit)
        self.assertEqual(self.plugin.c_song_dupes, old_c_song_dupes - 1)
        self.assertEqual(self.plugin.c_songs_delete, old_c_songs_delete)

    def test_row_edited_delete_to_unique(self):
        self._make_library()
        self._make_files_for_deletion()
        self._select_searches('Symbols')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        self._model_remove_by_tag(self.Tags.PENDING_COPY)
        cell_id_edit = 0
        old_c_songs_copy = self.plugin.c_songs_copy
        old_c_songs_delete = self.plugin.c_songs_delete

        self._mark_song_unique(cell_id_edit, check=False)
        self.assertEqual(self.plugin.c_songs_copy, old_c_songs_copy)
        self.assertEqual(self.plugin.c_songs_delete, old_c_songs_delete)

    def test_row_edited_delete_to_duplicate(self):
        self._make_library()
        self._make_files_for_deletion()
        self._select_searches('Symbols')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        self._model_remove_by_tag(self.Tags.PENDING_COPY)
        cell_id_edit = 0
        cell_id_copy = 1
        old_c_song_dupes = self.plugin.c_song_dupes
        old_c_songs_delete = self.plugin.c_songs_delete

        self._mark_song_duplicate(cell_id_edit, cell_id_copy, check=False)
        self.assertEqual(self.plugin.c_song_dupes, old_c_song_dupes)
        self.assertEqual(self.plugin.c_songs_delete, old_c_songs_delete)

    def test_row_edited_delete_to_delete(self):
        self._make_library()
        self._make_files_for_deletion()
        self._select_searches('Symbols')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        self._model_remove_by_tag(self.Tags.PENDING_COPY)
        cell_id_edit = 0
        old_c_songs_delete = self.plugin.c_songs_delete

        self._mark_song_delete(cell_id_edit)
        self.assertEqual(self.plugin.c_songs_delete, old_c_songs_delete - 1)

    def test_row_edited_delete_to_empty(self):
        self._make_library()
        self._make_files_for_deletion()
        self._select_searches('Symbols')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        self._model_remove_by_tag(self.Tags.PENDING_COPY)
        cell_id_edit = 0
        old_c_songs_delete = self.plugin.c_songs_delete

        self._mark_song_empty(cell_id_edit, check=False)
        self.assertEqual(self.plugin.c_songs_delete, old_c_songs_delete)

    def test_row_edited_empty_to_unique(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)

        cell_id_edit = 0
        self._mark_song_empty(cell_id_edit)
        self._mark_song_unique(cell_id_edit)

    def test_row_edited_empty_to_duplicate(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)

        cell_id_edit = 0
        cell_id_copy = 1
        self._mark_song_empty(cell_id_edit)
        self._mark_song_duplicate(cell_id_edit, cell_id_copy)

    def test_row_edited_empty_to_delete(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)

        cell_id_edit = 0
        self._mark_song_empty(cell_id_edit)
        self._mark_song_delete(cell_id_edit)

    def test_row_edited_empty_to_empty(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)

        cell_id_edit = 0
        self._mark_song_empty(cell_id_edit)
        self._mark_song_empty(cell_id_edit)

    def test_row_edited_others_duplicate_to_unique_single(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)

        cell_id_unique = 0
        cell_id_duplicate = 1
        self._mark_song_duplicate(cell_id_duplicate, cell_id_unique)
        self._mark_song_unique(cell_id_unique)
        self.assertEqual(self._model_get_value(cell_id_duplicate, 'tag'),
                         self.Tags.PENDING_COPY)

    def test_row_edited_others_duplicate_to_unique_multiple(self):
        self._make_library()
        self._select_searches('Directory')
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)

        cell_id_unique = 1
        cell_id_duplicate_1 = 0
        cell_id_duplicate_2 = 2
        self._mark_song_duplicate(cell_id_duplicate_1, cell_id_unique)
        self._mark_song_duplicate(cell_id_duplicate_2, cell_id_unique)

        self._mark_song_unique(cell_id_unique)
        self.assertEqual(self._model_get_value(cell_id_duplicate_1, 'tag'),
                         self.Tags.SKIP_DUPLICATE)
        self.assertEqual(self._model_get_value(cell_id_duplicate_2, 'tag'),
                         self.Tags.SKIP_DUPLICATE)

        self._mark_song_unique(cell_id_duplicate_1, new_text='new_text')
        self.assertEqual(self._model_get_value(cell_id_duplicate_2, 'tag'),
                         self.Tags.PENDING_COPY)

    @patch('os.remove')
    @patch('shutil.copyfile')
    @patch('os.makedirs')
    def test_start_sync_basic_success(self, mock_mkdir, mock_cp, mock_rm):
        self._make_library()
        query_name = 'Directory'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        n_songs = QUERIES[query_name]['results']

        self.plugin._start_sync(self.plugin.sync_start_button)

        self.assertEqual(self.plugin.c_files_copy, n_songs)
        self.assertEqual(mock_mkdir.call_count, n_songs)
        self.assertEqual(mock_cp.call_count, n_songs)
        self.assertEqual(mock_rm.call_count, 0)
        self.plugin.model.foreach(self._verify_child,
            [self.Tags.RESULT_SUCCESS], [self.path_dest],
            '~filename', QUERIES[query_name]['terms'])

    @patch('os.remove')
    @patch('shutil.copyfile')
    @patch('os.makedirs')
    def test_start_sync_duplicates(self, mock_mkdir, mock_cp, mock_rm):
        self._make_library()
        query_name = 'Directory'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        n_songs = QUERIES[query_name]['results']
        cell_id_copy = 0
        for cell_id in range(cell_id_copy + 1, n_songs):
            self._mark_song_duplicate(cell_id, cell_id_copy)
        self.assertTrue(self.plugin.status_duplicates.get_visible())

        self.plugin._start_sync(self.plugin.sync_start_button)
        self.assertFalse(self.plugin.status_duplicates.get_visible())

        expected_sync = 1
        expected_skip = n_songs - expected_sync
        self.assertEqual(self.plugin.c_files_copy, expected_sync)
        self.assertEqual(self.plugin.c_files_dupes, expected_skip)
        self.assertEqual(mock_mkdir.call_count, expected_sync)
        self.assertEqual(mock_cp.call_count, expected_sync)
        self.assertEqual(mock_rm.call_count, 0)
        self.plugin.model.foreach(self._verify_child,
            [self.Tags.RESULT_SUCCESS, self.Tags.SKIP_DUPLICATE],
            [self.path_dest], '~filename', QUERIES[query_name]['terms'])

    @patch('os.remove')
    @patch('shutil.copyfile')
    @patch('os.makedirs')
    def test_start_sync_deletion(self, mock_mkdir, mock_cp, mock_rm):
        self._make_library()
        n_files = self._make_files_for_deletion()
        query_name = 'Symbols'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        n_songs = QUERIES[query_name]['results']
        self.assertTrue(self.plugin.status_deletions.get_visible())

        self.plugin._start_sync(self.plugin.sync_start_button)
        self.assertFalse(self.plugin.status_deletions.get_visible())

        self.assertEqual(self.plugin.c_files_copy, n_songs)
        self.assertEqual(self.plugin.c_files_delete, n_files)
        self.assertEqual(mock_mkdir.call_count, n_songs)
        self.assertEqual(mock_cp.call_count, n_songs)
        self.assertEqual(mock_rm.call_count, n_files)
        self.plugin.model.foreach(self._verify_child,
            [self.Tags.RESULT_SUCCESS], [''],
            '~filename', QUERIES[query_name]['terms'])

    @patch('os.rmdir')
    @patch('os.remove', side_effect=os.remove)
    @patch('shutil.copyfile')
    @patch('os.makedirs')
    def test_start_sync_deletion_with_dirs(self, mkdir, cp, rm, rmdir):
        self._make_library()
        n_files = self._make_files_for_deletion('song1.mp3', 'song2.mp3',
            str(Path('other', 'file1.txt')), str(Path('other', 'file2.txt')))
        n_dirs = 1
        query_name = 'Symbols'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        n_songs = QUERIES[query_name]['results']

        self.plugin._start_sync(self.plugin.sync_start_button)

        n_children_updated = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children_updated, n_songs + n_files + n_dirs)
        self.assertEqual(self.plugin.c_files_copy, n_songs)
        self.assertEqual(self.plugin.c_files_delete, n_files + n_dirs)
        self.assertEqual(mkdir.call_count, n_songs)
        self.assertEqual(cp.call_count, n_songs)
        self.assertEqual(rm.call_count, n_files)
        self.assertEqual(rmdir.call_count, n_dirs)
        self.plugin.model.foreach(self._verify_child,
            [self.Tags.RESULT_SUCCESS], [''],
            '~filename', QUERIES[query_name]['terms'])

    @patch('os.remove', side_effect=Exception('Mocked failure on remove file'))
    @patch('shutil.copyfile', side_effect=Exception('Mocked failure on copy'))
    @patch('os.makedirs')
    def test_start_sync_failures(self, mock_mkdir, mock_cp, mock_rm):
        self._make_library()
        n_files = self._make_files_for_deletion()
        query_name = 'Directory'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        n_songs = QUERIES[query_name]['results']
        n_total = n_songs + n_files

        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, n_total)

        self.plugin._start_sync(self.plugin.sync_start_button)

        self.assertEqual(self.plugin.c_files_failed, n_total)
        self.assertEqual(mock_mkdir.call_count, n_songs)
        self.assertEqual(mock_cp.call_count, n_songs)
        self.assertEqual(mock_rm.call_count, n_files)
        self.plugin.model.foreach(self._verify_child,
            [self.Tags.RESULT_FAILURE], [self.path_dest, ''],
            '~filename', QUERIES[query_name]['terms'])

    @patch('os.rmdir')
    @patch('os.remove', side_effect=os.remove)
    @patch('shutil.copyfile')
    @patch('os.makedirs')
    def test_start_sync_complex_success(self, mkdir, cp, rm, rmdir):
        case_insensitive_filesystem = \
            os.path.exists(__file__) == os.path.exists(__file__.upper())

        self._make_library()
        n_files = self._make_files_for_deletion('song1.mp3', 'file1.mp3',
            str(Path('other', 'file1.txt')), str(Path('other', 'file2.txt')))
        n_dirs = 1
        queries = ('Directory', '2 artists')
        self._select_searches(*queries)
        self.dest_entry.set_text(self.path_dest)
        self.pattern_entry.set_text('<title>')
        self.plugin._start_preview(self.plugin.preview_start_button)
        n_songs = 0
        for query in queries:
            n_songs += QUERIES[query]['results']
        n_songs_duplicate = 1
        n_songs_existing = 1 if case_insensitive_filesystem else 0
        n_total = n_songs + n_files

        n_children = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children, n_total)

        self.plugin._start_sync(self.plugin.sync_start_button)

        n_children_updated = self.plugin.model.iter_n_children(None)
        self.assertEqual(n_children_updated, n_children + n_dirs)
        n_expected_songs = n_songs - n_songs_duplicate - n_songs_existing
        self.assertEqual(self.plugin.c_files_copy, n_expected_songs)
        self.assertEqual(self.plugin.c_files_skip, n_songs_existing)
        self.assertEqual(self.plugin.c_files_dupes, n_songs_duplicate)
        self.assertEqual(self.plugin.c_files_delete, n_files + n_dirs)
        self.assertEqual(mkdir.call_count, n_expected_songs)
        self.assertEqual(cp.call_count, n_expected_songs)
        self.assertEqual(rm.call_count, n_files)
        self.assertEqual(rmdir.call_count, n_dirs)
        self.plugin.model.foreach(self._verify_child,
            [self.Tags.RESULT_SUCCESS, self.Tags.SKIP_DUPLICATE,
             self.Tags.RESULT_SKIP_EXISTING], [self.path_dest, ''],
            None, None)

    @patch('os.remove')
    @patch('shutil.copyfile')
    @patch('os.makedirs')
    def test_start_sync_twice(self, mock_mkdir, mock_cp, mock_rm):
        self._make_library()
        query_name = 'Directory'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)
        self.plugin._start_preview(self.plugin.preview_start_button)
        n_songs = QUERIES[query_name]['results']

        self.plugin._start_sync(self.plugin.sync_start_button)
        self.assertEqual(self.plugin.c_files_copy, n_songs)
        self.assertEqual(mock_mkdir.call_count, n_songs)
        self.assertEqual(mock_cp.call_count, n_songs)
        self.assertEqual(mock_rm.call_count, 0)

        mock_mkdir.reset_mock()
        mock_cp.reset_mock()
        mock_rm.reset_mock()

        self.plugin._start_sync(self.plugin.sync_start_button)
        self.assertEqual(self.plugin.c_files_skip_previous, n_songs)
        self.assertEqual(mock_mkdir.call_count, 0)
        self.assertEqual(mock_cp.call_count, 0)
        self.assertEqual(mock_rm.call_count, 0)

    @patch('os.remove')
    @patch('shutil.copyfile')
    @patch('os.makedirs')
    def test_preview_sync_twice(self, mock_mkdir, mock_cp, mock_rm):
        self._make_library()
        query_name = 'Directory'
        self._select_searches(query_name)
        self.dest_entry.set_text(self.path_dest)
        n_songs = QUERIES[query_name]['results']

        self.plugin._start_preview(self.plugin.preview_start_button)
        self.plugin._start_sync(self.plugin.sync_start_button)
        self.assertEqual(self.plugin.c_files_copy, n_songs)
        self.assertEqual(mock_mkdir.call_count, n_songs)
        self.assertEqual(mock_cp.call_count, n_songs)
        self.assertEqual(mock_rm.call_count, 0)

        mock_mkdir.reset_mock()
        mock_cp.reset_mock()
        mock_rm.reset_mock()

        self.plugin._start_preview(self.plugin.preview_start_button)
        self.plugin._start_sync(self.plugin.sync_start_button)
        self.assertEqual(self.plugin.c_files_copy, n_songs)
        self.assertEqual(mock_mkdir.call_count, n_songs)
        self.assertEqual(mock_cp.call_count, n_songs)
        self.assertEqual(mock_rm.call_count, 0)
