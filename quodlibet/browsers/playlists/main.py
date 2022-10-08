# Copyright 2005 Joe Wreschnig
#      2011-2022 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
from typing import Optional, Tuple

from gi.repository import Gtk, GLib, Pango, Gdk

import quodlibet
from quodlibet import _
from quodlibet import config
from quodlibet import qltk
from quodlibet.browsers import Browser
from quodlibet.browsers._base import DisplayPatternMixin, BrowserError
from quodlibet.browsers.playlists.prefs import Preferences, DEFAULT_PATTERN_TEXT
from quodlibet.formats import AudioFile
from quodlibet.library import SongFileLibrary
from quodlibet.library.playlist import PlaylistLibrary
from quodlibet.plugins.playlist import PLAYLIST_HANDLER
from quodlibet.qltk import Icons
from quodlibet.qltk.chooser import choose_files, create_chooser_filter
from quodlibet.qltk.completion import LibraryTagCompletion
from quodlibet.qltk.information import Information
from quodlibet.qltk.menubutton import MenuButton
from quodlibet.qltk.models import ObjectStore, ObjectModelSort
from quodlibet.qltk.msg import ConfirmationPrompt
from quodlibet.qltk.properties import SongProperties
from quodlibet.qltk.searchbar import SearchBarBox
from quodlibet.qltk.songlist import SongList
from quodlibet.qltk.songsmenu import SongsMenu
from quodlibet.qltk.views import RCMHintedTreeView
from quodlibet.qltk.x import ScrolledWindow, Align, MenuItem, SymbolicIconImage
from quodlibet.util import connect_obj
from quodlibet.util.collection import Playlist
from quodlibet.util.dprint import print_d, print_w
from quodlibet.util.urllib import urlopen
from .util import parse_m3u, parse_pls, confirm_remove_playlist_dialog_invoke, _name_for

DND_QL, DND_URI_LIST, DND_MOZ_URL = range(3)


class PlaylistsBrowser(Browser, DisplayPatternMixin):
    name = _("Playlists")
    accelerated_name = _("_Playlists")
    keys = ["Playlists", "PlaylistsBrowser"]
    priority = 2
    replaygain_profiles = ["track"]
    __last_render = None
    # TODO: Maybe on the Config folder?
    _PATTERN_FN = os.path.join(quodlibet.get_data_dir(), "playlist_pattern")
    _DEFAULT_PATTERN_TEXT = DEFAULT_PATTERN_TEXT

    def __init__(self, songs_lib: SongFileLibrary, Confirmer=ConfirmationPrompt):
        super().__init__(spacing=3)
        self.songs_lib = songs_lib
        try:
            self.pl_lib: PlaylistLibrary = songs_lib.playlists
        except (AttributeError, TypeError):
            raise BrowserError(f"No playlist library available in {songs_lib!r}")

        self._lists = ObjectModelSort(model=ObjectStore())
        self._lists.set_default_sort_func(ObjectStore._sort_on_value)
        model = self._lists.get_model()
        print_d(f"Reading playlists from library: {self.pl_lib}")
        for playlist in self.pl_lib:
            model.append(row=[playlist])

        # this is instanced with the necessary gtkdialog-settings, and afterwards
        # its run-method is called to get a to-be-compared Gtk.ResponseType
        self.Confirmer = Confirmer
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.__render = self.__create_cell_renderer()
        self.__view = view = self.__create_playlists_view(self.__render)
        self.__embed_in_scrolledwin(view)
        self.__configure_buttons(songs_lib)
        self.__configure_dnd(view)
        self.__connect_signals(view)
        self._sb_box = self.__create_searchbar(songs_lib)
        self._rh_box = None
        self._main_box = self.__create_box()
        self.show_all()

        for child in self.get_children():
            child.show_all()

        self._ids = [
            self.pl_lib.connect('removed', self.__removed),
            self.pl_lib.connect('added', self.__added),
            self.pl_lib.connect('changed', self.__changed),
        ]
        print_d(f"Connected signals: {self._ids} from {self.pl_lib!r} for {self}")
        self.connect("destroy", self._destroy)

    def _destroy(self, _browser):
        for id_ in self._ids:
            self.pl_lib.disconnect(id_)
        del self._ids

    def pack(self, songpane):
        self._main_box.pack1(self, True, False)
        self._rh_box = rhbox = Gtk.VBox(spacing=6)
        align = Align(self._sb_box, left=0, right=6, top=0)
        rhbox.pack_start(align, False, True, 0)
        rhbox.pack_start(songpane, True, True, 0)
        self._main_box.pack2(rhbox, True, False)
        rhbox.show()
        align.show_all()
        return self._main_box

    def unpack(self, container, songpane):
        self._rh_box.remove(songpane)
        container.remove(self._rh_box)
        container.remove(self)

    @classmethod
    def init(klass, library):
        klass.load_pattern()

    def playlists(self):
        return [row[0] for row in self._lists]

    def changed(self, playlist, refresh=True):
        current = self._selected_playlist()
        for row in self._lists:
            if row[0] is playlist:
                if refresh:
                    # Changes affect aggregate caches etc
                    print_d(f"Refreshing view in {self} for {playlist}")
                    self._lists.row_changed(row.path, row.iter)
                    if playlist is current:
                        self.activate()
                break

    def __removed(self, lib, playlists):
        for row in self.model:
            pl = row[0]
            if pl in playlists:
                print_d(f"Removing {pl} from view", str(self))
                self.__playlist_deleted(row)
        self.activate()

    def __added(self, lib, playlists):
        for playlist in playlists:
            print_d(f"Looks like a new playlist: {playlist}")
            self.model.append(row=[playlist])

    def __changed(self, lib, playlists):
        for playlist in playlists:
            self.changed(playlist)

    def cell_data(self, col, cell, model, iter, data):
        playlist = model[iter][0]
        cell.markup = markup = self.display_pattern % playlist
        if self.__last_render == markup:
            return
        self.__last_render = markup
        cell.markup = markup
        cell.set_property('markup', markup)

    def Menu(self, songs, library, items):
        model, iters = self.__get_selected_songs()
        remove = qltk.MenuItem(_("_Remove from Playlist"), Icons.LIST_REMOVE)
        qltk.add_fake_accel(remove, "Delete")
        connect_obj(remove, 'activate', self.__remove_songs, iters, model)
        playlist_iter = self.__selected_playlists()[1]
        remove.set_sensitive(bool(playlist_iter))
        items.append([remove])
        menu = super().Menu(songs, library, items)
        return menu

    def __get_selected_songs(self):
        songlist = qltk.get_top_parent(self).songlist
        model, rows = songlist.get_selection().get_selected_rows()
        iters = map(model.get_iter, rows)
        return model, iters

    @property
    def _query(self):
        return self._sb_box.get_query(SongList.star)

    def __destroy(self, *args):
        del self._sb_box

    def __create_box(self):
        box = qltk.ConfigRHPaned("browsers", "playlistsbrowser_pos", 0.4)
        box.show_all()
        return box

    def __create_searchbar(self, library):
        self.accelerators = Gtk.AccelGroup()
        completion = LibraryTagCompletion(library.librarian)
        sbb = SearchBarBox(completion=completion,
                           accel_group=self.accelerators)
        sbb.connect('query-changed', self.__text_parse)
        sbb.connect('focus-out', self.__focus)
        return sbb

    def __embed_in_scrolledwin(self, view):
        swin = ScrolledWindow()
        swin.set_shadow_type(Gtk.ShadowType.IN)
        swin.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        swin.add(view)
        self.pack_start(swin, True, True, 0)

    def __configure_buttons(self, library):
        new_pl = qltk.Button(_("_New"), Icons.DOCUMENT_NEW, Gtk.IconSize.MENU)
        new_pl.connect('clicked', self.__new_playlist, library)
        import_pl = qltk.Button(_("_Import…"), Icons.DOCUMENT_OPEN, Gtk.IconSize.MENU)
        import_pl.connect('clicked', self.__import, library)

        fb = Gtk.FlowBox()
        fb.set_selection_mode(Gtk.SelectionMode.NONE)
        fb.set_homogeneous(True)
        fb.insert(new_pl, 0)
        fb.insert(import_pl, 1)
        fb.set_max_children_per_line(2)
        fb.set_column_spacing(3)

        # The pref button is in its own flowbox instead of directly under the
        # HBox to make it the same height as the other buttons
        pref = PreferencesButton(self)
        fb2 = Gtk.FlowBox()
        fb2.insert(pref, 0)

        hb = Gtk.HBox()
        hb.pack_start(fb, True, True, 3)
        hb.pack_end(fb2, False, False, 0)
        self.pack_start(hb, False, False, 0)

    def __create_playlists_view(self, render):
        view = RCMHintedTreeView()
        view.set_enable_search(True)
        view.set_search_column(0)
        view.set_search_equal_func(
            lambda model, col, key, iter, data:
            not model[iter][col].name.lower().startswith(key.lower()), None)
        col = Gtk.TreeViewColumn("Playlists", render)
        col.set_cell_data_func(render, self.cell_data)
        view.append_column(col)
        view.set_model(self._lists)
        view.set_rules_hint(True)
        view.set_headers_visible(False)
        return view

    def __configure_dnd(self, view):
        targets = [
            ("text/x-quodlibet-songs", Gtk.TargetFlags.SAME_APP, DND_QL),
            ("text/uri-list", 0, DND_URI_LIST),
            ("text/x-moz-url", 0, DND_MOZ_URL)
        ]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]
        view.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        view.enable_model_drag_source(
            Gdk.ModifierType.BUTTON1_MASK, targets[:2], Gdk.DragAction.COPY)
        view.connect('drag-data-received', self.__drag_data_received)
        view.connect('drag-data-get', self._drag_data_get)
        view.connect('drag-motion', self.__drag_motion)
        view.connect('drag-leave', self.__drag_leave)

    def __connect_signals(self, view):
        view.connect('row-activated', lambda *x: self.songs_activated())
        view.connect('popup-menu', self.__popup_menu, self.songs_lib)
        view.get_selection().connect('changed', self.activate)
        self.connect('key-press-event', self.__key_pressed)

    def __create_cell_renderer(self):
        render = Gtk.CellRendererText()
        render.set_padding(3, 3)
        render.set_property('ellipsize', Pango.EllipsizeMode.END)
        render.connect('editing-started', self.__start_editing)
        render.connect('edited', self.__edited)
        return render

    def key_pressed(self, event):
        if qltk.is_accel(event, "Delete"):
            self.__handle_songlist_delete()
            return True
        return False

    def __handle_songlist_delete(self, *args):
        model, iters = self.__get_selected_songs()
        self.__remove_songs(iters, model)

    def __key_pressed(self, widget, event):
        if qltk.is_accel(event, "Delete"):
            model, iter = self.__selected_playlists()
            if not iter:
                return False

            playlist = model[iter][0]
            if confirm_remove_playlist_dialog_invoke(self, playlist, self.Confirmer):
                playlist.delete()
            else:
                print_d("Playlist removal cancelled through prompt")
            return True
        elif qltk.is_accel(event, "F2"):
            model, iter = self.__selected_playlists()
            if iter:
                self._start_rename(model.get_path(iter))
            return True
        elif qltk.is_accel(event, "<Primary>I"):
            songs = self._get_playlist_songs()
            if songs:
                window = Information(self.songs_lib.librarian, songs, self)
                window.show()
            return True
        elif qltk.is_accel(event, "<Primary>Return", "<Primary>KP_Enter"):
            qltk.enqueue(self._get_playlist_songs())
            return True
        elif qltk.is_accel(event, "<alt>Return"):
            songs = self._get_playlist_songs()
            if songs:
                window = SongProperties(self.songs_lib.librarian, songs, self)
                window.show()
            return True
        return False

    def __playlist_deleted(self, row) -> None:
        self.model.remove(row.iter)

    def __drag_motion(self, view, ctx, x, y, time):
        targets = [t.name() for t in ctx.list_targets()]
        if "text/x-quodlibet-songs" in targets:
            view.set_drag_dest(x, y, into_only=True)
            return True
        else:
            # Highlighting the view itself doesn't work.
            view.get_parent().drag_highlight()
            return True

    def __drag_leave(self, view, ctx, time):
        view.get_parent().drag_unhighlight()

    def __remove_songs(self, iters, smodel):
        def song_at(itr):
            return smodel[smodel.get_path(itr)][0]

        def remove_from_model(iters, smodel):
            for it in iters:
                smodel.remove(it)

        model, iter = self.__selected_playlists()
        if iter:
            playlist = model[iter][0]
            # Build a {iter: song} dict, exhausting `iters` once.
            removals = {iter_remove: song_at(iter_remove)
                        for iter_remove in iters}
            if not removals:
                print_w("No songs selected to remove")
                return
            if self._query is None or not self.get_filter_text():
                # Calling playlist.remove_songs(songs) won't remove the
                # right ones if there are duplicates
                remove_from_model(removals.keys(), smodel)
                self.__rebuild_playlist_from_songs_model(playlist, smodel)
                # Emit manually
                self.songs_lib.emit('changed', removals.values())
            else:
                playlist.remove_songs(removals.values(), True)
                remove_from_model(removals.keys(), smodel)
            print_d("Removed %d song(s) from %s" % (len(removals), playlist))

    def __rebuild_playlist_from_songs_model(self, playlist, smodel):
        self.pl_lib.recreate(playlist, [row[0] for row in smodel])

    def _selected_playlist(self) -> Optional[Playlist]:
        """The currently selected playlist's, or None if none selected"""
        model, iter = self.__selected_playlists()
        if not iter:
            return None
        path = model.get_path(iter)
        playlist = model[path][0]
        return playlist

    def __drag_data_received(self, view, ctx, x, y, sel, tid, etime):
        # TreeModelSort doesn't support GtkTreeDragDestDrop.
        view.emit_stop_by_name('drag-data-received')
        model = view.get_model()
        if tid == DND_QL:
            filenames = qltk.selection_get_filenames(sel)
            songs = list(filter(None, [self.songs_lib.get(f) for f in filenames]))
            if not songs:
                Gtk.drag_finish(ctx, False, False, etime)
                return
            try:
                path, pos = view.get_dest_row_at_pos(x, y)
            except TypeError:
                playlist = self.pl_lib.create_from_songs(songs)
                GLib.idle_add(self._select_playlist, playlist)
            else:
                playlist = model[path][0]
                playlist.extend(songs)
            # self.changed(playlist)
            Gtk.drag_finish(ctx, True, False, etime)
            # Cause a refresh to the dragged-to playlist if it is selected
            # so that the dragged (duplicate) track(s) appears
            if playlist is self._selected_playlist():
                model, plist_iter = self.__selected_playlists()
                songlist = qltk.get_top_parent(self).songlist
                self.activate(resort=not songlist.is_sorted())
        else:
            if tid == DND_URI_LIST:
                uri = sel.get_uris()[0]
                name = os.path.basename(uri)
            elif tid == DND_MOZ_URL:
                data = sel.get_data()
                uri, name = data.decode('utf16', 'replace').split('\n')
            else:
                Gtk.drag_finish(ctx, False, False, etime)
                return
            name = _name_for(name or os.path.basename(uri))
            try:
                sock = urlopen(uri)
                uri = uri.lower()
                if uri.endswith('.pls'):
                    playlist = parse_pls(sock, name,
                                         songs_lib=self.songs_lib, pl_lib=self.pl_lib)
                elif uri.endswith('.m3u') or uri.endswith('.m3u8'):
                    playlist = parse_m3u(sock, name,
                                         songs_lib=self.songs_lib, pl_lib=self.pl_lib)
                else:
                    raise IOError
                self.songs_lib.add(playlist.songs)
                # TODO: change to use playlist library too?
                # self.changed(playlist)
                Gtk.drag_finish(ctx, True, False, etime)
            except IOError:
                Gtk.drag_finish(ctx, False, False, etime)
                qltk.ErrorMessage(
                    qltk.get_top_parent(self),
                    _("Unable to import playlist"),
                    _("Quod Libet can only import playlists in the M3U/M3U8 "
                      "and PLS formats.")).run()

    def _drag_data_get(self, view, ctx, sel, tid, etime):
        model, iters = self.__view.get_selection().get_selected_rows()
        songs = []
        for itr in iters:
            if itr:
                songs += model[itr][0].songs
        if tid == 0:
            qltk.selection_set_songs(sel, songs)
        else:
            sel.set_uris([song("~uri") for song in songs])

    def _select_playlist(self, playlist, scroll=False):
        view = self.__view
        model = view.get_model()
        for row in model:
            if row[0] is playlist:
                view.get_selection().select_iter(row.iter)
                if scroll:
                    view.scroll_to_cell(row.path, use_align=True,
                                        row_align=0.5)

    def __popup_menu(self, view, library):
        model, itr = view.get_selection().get_selected()
        if itr is None:
            return
        songs = list(model[itr][0])
        songs = [s for s in songs if isinstance(s, AudioFile)]
        menu = SongsMenu(library, songs, playlists=False, remove=False, ratings=False)
        menu.preseparate()

        def _remove(model, itr):
            playlist = model[itr][0]
            response = confirm_remove_playlist_dialog_invoke(
                self, playlist, self.Confirmer)
            if response:
                playlist.delete()
            else:
                print_d("Playlist removal cancelled through prompt")

        rem = MenuItem(_("_Delete"), Icons.EDIT_DELETE)
        connect_obj(rem, 'activate', _remove, model, itr)
        menu.prepend(rem)

        def _rename(path):
            self._start_rename(path)

        ren = qltk.MenuItem(_("_Rename"), Icons.EDIT)
        qltk.add_fake_accel(ren, "F2")
        connect_obj(ren, 'activate', _rename, model.get_path(itr))
        menu.prepend(ren)

        playlist = model[itr][0]
        PLAYLIST_HANDLER.populate_menu(menu, library, self, [playlist])
        menu.show_all()
        return view.popup_menu(menu, 0, Gtk.get_current_event_time())

    def _start_rename(self, path):
        view = self.__view
        self.__render.set_property('editable', True)
        view.set_cursor(path, view.get_columns()[0], start_editing=True)

    def __focus(self, widget, *args):
        qltk.get_top_parent(widget).songlist.grab_focus()

    def __text_parse(self, bar, text):
        self.activate()

    def _set_text(self, text):
        self._sb_box.set_text(text)

    def activate(self, widget=None, resort=True):
        songs = self._get_playlist_songs()
        query = self._sb_box.get_query(SongList.star)
        if query and query.is_parsable:
            songs = query.filter(songs)
        GLib.idle_add(self.songs_selected, songs, resort)

    def refresh_all(self):
        print_d("Refreshing all items...")
        model = self._lists.get_model()
        for iter_, value in model.iterrows():
            print_d(f"Refreshing row {iter_}")
            model.row_changed(model.get_path(iter_), iter_)

    @property
    def model(self):
        return self._lists.get_model()

    def _get_playlist_songs(self):
        model, iter = self.__selected_playlists()
        songs = iter and list(model[iter][0]) or []
        return [s for s in songs if isinstance(s, AudioFile)]

    def can_filter_text(self):
        return True

    def filter_text(self, text):
        self._set_text(text)
        self.activate()

    def get_filter_text(self):
        return self._sb_box.get_text()

    def can_filter(self, key):
        # TODO: special-case the ~playlists tag maybe?
        return super().can_filter(key)

    def finalize(self, restore):
        config.set("browsers", "query_text", "")

    def unfilter(self):
        self.filter_text("")

    def active_filter(self, song):
        return (song in self._get_playlist_songs()
                and (self._query is None or self._query.search(song)))

    def save(self):
        model, iter = self.__selected_playlists()
        name = iter and model[iter][0].name or ""
        config.set("browsers", "playlist", name)
        text = self.get_filter_text()
        config.set("browsers", "query_text", text)

    def __new_playlist(self, activator, library):
        playlist = self.pl_lib.create()
        self._select_playlist(playlist, scroll=True)

        model, iter = self.__selected_playlists()
        path = model.get_path(iter)
        GLib.idle_add(self._start_rename, path)

    def __start_editing(self, render, editable, path):
        editable.set_text(self._lists[path][0].name)

    def __edited(self, render, path, newname):
        return self._rename(path, newname)

    def _rename(self, path: Gtk.TreePath,
                new_name: str, show_error: bool = True) -> bool:
        playlist = self._lists[path][0]
        try:
            playlist.rename(new_name)
        except ValueError as e:
            if show_error:
                qltk.ErrorMessage(None, _("Unable to rename playlist"), str(e)).run()
            print_w(f"Unable to rename playlist {playlist} ({e})")
            return False
        else:
            row = self._lists[path]
            child_model = self.model
            child_model.remove(
                self._lists.convert_iter_to_child_iter(row.iter))
            child_model.append(row=[playlist])
            self._select_playlist(playlist, scroll=True)
            return True

    def __import(self, activator, library):
        formats = ["*.pls", "*.m3u", "*.m3u8"]
        cf = create_chooser_filter(_("Playlists"), formats)
        fns = choose_files(self, _("Import Playlist"), _("_Import"), cf)
        self._import_playlists(fns)

    def _import_playlists(self, fns) -> Tuple[int, int]:
        """ Import m3u / pls playlists into QL
        Returns the (total playlists, total songs) added
        TODO: move this to Playlists library and watch here for new playlists
        """
        total_pls = 0
        total_songs = 0
        for filename in fns:
            name = _name_for(filename)
            with open(filename, "rb") as f:
                if filename.endswith(".m3u") or filename.endswith(".m3u8"):
                    playlist = parse_m3u(f, name,
                                         songs_lib=self.songs_lib, pl_lib=self.pl_lib)
                elif filename.endswith(".pls"):
                    playlist = parse_pls(f, name,
                                         songs_lib=self.songs_lib, pl_lib=self.pl_lib)
                else:
                    print_w("Unsupported playlist type for '%s'" % filename)
                    continue
            # Import all the songs in the playlist to the *songs* library
            total_songs += len(self.songs_lib.add(playlist))
            total_pls += 1
        return total_pls, total_songs

    def restore(self):
        try:
            name = config.get("browsers", "playlist")
        except config.Error as e:
            print_d("Couldn't get last playlist from config: %s" % e)
        else:
            self.__view.select_by_func(lambda r: r[0].name == name, one=True)
        try:
            text = config.get("browsers", "query_text")
        except config.Error as e:
            print_d("Couldn't get last search string from config: %s" % e)
        else:
            self._set_text(text)

    can_reorder = True

    def scroll(self, song):
        self.__view.iter_select_by_func(lambda r: song in r[0])

    def reordered(self, songs):
        model, iter = self.__selected_playlists()
        playlist = None
        if iter:
            playlist = model[iter][0]
            playlist[:] = songs
        elif songs:
            playlist = self.pl_lib.create_from_songs(songs)
            GLib.idle_add(self._select_playlist, playlist)
        if playlist:
            self.changed(playlist, refresh=False)

    def __selected_playlists(self):
        """Returns a tuple of (model, iter) for the current playlist(s)"""
        return self.__view.get_selection().get_selected()


class PreferencesButton(Gtk.HBox):
    def __init__(self, browser):
        super().__init__()

        menu = Gtk.Menu()

        pref_item = MenuItem(_("_Preferences"), Icons.PREFERENCES_SYSTEM)
        menu.append(pref_item)
        connect_obj(pref_item, "activate", Preferences, browser)

        menu.show_all()

        button = MenuButton(
            SymbolicIconImage(Icons.EMBLEM_SYSTEM, Gtk.IconSize.MENU),
            arrow=True)
        button.set_menu(menu)
        self.pack_start(button, True, True, 0)
