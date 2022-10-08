# Copyright 2004-2007 Joe Wreschnig, Michael Urman, Iñigo Serna
#           2009-2010 Steven Robertson
#           2012-2022 Nick Boultbee
#           2009-2014 Christoph Reiter
#           2022 Thomas Leberbauer
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from __future__ import absolute_import

import os

from gi.repository import GLib, Gtk, Gdk, Gio

from .prefs import Preferences, DEFAULT_PATTERN_TEXT

import quodlibet
from quodlibet import app
from quodlibet import ngettext
from quodlibet import config
from quodlibet import qltk
from quodlibet import util
from quodlibet import _
from quodlibet.browsers import Browser
from quodlibet.browsers.albums.main import (
    AlbumTagCompletion,
    PreferencesButton as AlbumPreferencesButton,
)
from quodlibet.browsers.covergrid.models import (
    AlbumListFilterModel,
    AlbumListModel,
    AlbumListSortModel,
)
from quodlibet.browsers.covergrid.widgets import AlbumWidget
from quodlibet.browsers._base import DisplayPatternMixin
from quodlibet.query import Query
from quodlibet.qltk.information import Information
from quodlibet.qltk.properties import SongProperties
from quodlibet.qltk.songsmenu import SongsMenu
from quodlibet.qltk.x import MenuItem, Align, ScrolledWindow, RadioMenuItem
from quodlibet.qltk.x import SymbolicIconImage
from quodlibet.qltk.searchbar import SearchBarBox
from quodlibet.qltk.menubutton import MenuButton
from quodlibet.qltk import Icons
from quodlibet.util import connect_destroy
from quodlibet.util import connect_obj
from quodlibet.qltk import popup_menu_at_widget


class PreferencesButton(AlbumPreferencesButton):
    def __init__(self, browser, model):
        Gtk.HBox.__init__(self)

        sort_orders = [
            (_("_Title"), self.__compare_title),
            (_("_People"), self.__compare_people),
            (_("_Date"), self.__compare_date),
            (_("_Date Added"), self.__compare_date_added),
            (_("_Original Date"), self.__compare_original_date),
            (_("_Genre"), self.__compare_genre),
            (_("_Rating"), self.__compare_rating),
            (_("Play_count"), self.__compare_avgplaycount),
        ]

        menu = Gtk.Menu()

        sort_item = Gtk.MenuItem(
            label=_(u"Sort _by…"), use_underline=True)
        sort_menu = Gtk.Menu()

        active = config.getint("browsers", "album_sort", 1)

        item = None
        for i, (label, func) in enumerate(sort_orders):
            item = RadioMenuItem(group=item, label=label,
                                 use_underline=True)
            model.set_sort_func(100 + i, func)
            if i == active:
                model.set_sort_column_id(100 + i, Gtk.SortType.ASCENDING)
                item.set_active(True)
            item.connect("toggled",
                         util.DeferredSignal(self.__sort_toggled_cb),
                         model, i)
            sort_menu.append(item)

        sort_item.set_submenu(sort_menu)
        menu.append(sort_item)

        pref_item = MenuItem(_("_Preferences"), Icons.PREFERENCES_SYSTEM)
        menu.append(pref_item)
        connect_obj(pref_item, "activate", Preferences, browser)

        menu.show_all()

        button = MenuButton(
                SymbolicIconImage(Icons.EMBLEM_SYSTEM, Gtk.IconSize.MENU),
                arrow=True)
        button.set_menu(menu)
        self.pack_start(button, True, True, 0)


class CoverGridContainer(ScrolledWindow):
    def __init__(self, fb):
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            shadow_type=Gtk.ShadowType.IN)
        self._fb = fb
        fb.set_hadjustment(self.props.hadjustment)
        fb.set_vadjustment(self.props.vadjustment)
        self.add(fb)

    def scroll_up(self):
        va = self.props.vadjustment
        va.props.value = va.props.lower

    def scroll_to_child(self, child):
        def scroll():
            va = self.props.vadjustment
            if va is None:
                return
            v = va.props.value
            coords = child.translate_coordinates(self, 0, v)
            if coords is None:
                return
            x, y = coords
            h = child.get_allocation().height
            p = va.props.page_size
            if y < v:
                va.props.value = y
            elif y + h > v + p:
                va.props.value = y - p + h

        GLib.idle_add(scroll, priority=GLib.PRIORITY_LOW)

    def do_focus(self, direction):
        is_tab = (direction == Gtk.DirectionType.TAB_FORWARD
            or direction == Gtk.DirectionType.TAB_BACKWARD)
        if not is_tab:
            self._fb.child_focus(direction)
            return True

        if self.get_focus_child():
            # [Tab] moves focus beyond this container
            return False

        children = self._fb.get_selected_children()
        if children:
            children[0].grab_focus()
        else:
            self._fb.child_focus(direction)
        return True


def _get_cover_size():
    mag = config.getfloat("browsers", "covergrid_magnification", 3.)
    size = config.getint("browsers", "cover_size")
    if size <= 0:
        size = 48
    return mag * size


class CoverGrid(Browser, util.InstanceTracker, DisplayPatternMixin):
    __model = None

    # TODO: Maybe on the Config folder?
    _PATTERN_FN = os.path.join(quodlibet.get_data_dir(), "album_pattern")
    _DEFAULT_PATTERN_TEXT = DEFAULT_PATTERN_TEXT

    name = _("Cover Grid")
    accelerated_name = _("_Cover Grid")
    keys = ["CoverGrid"]
    priority = 5

    def pack(self, songpane):
        container = self.songcontainer
        container.pack1(self, True, False)
        container.pack2(songpane, True, False)
        return container

    def unpack(self, container, songpane):
        container.remove(songpane)
        container.remove(self)

    @classmethod
    def init(klass, library):
        super(CoverGrid, klass).load_pattern()

    @classmethod
    def _init_model(klass, library):
        if klass.__model is None:
            klass.__model = AlbumListModel(library)
            klass.__library = library

    @classmethod
    def _destroy_model(klass):
        klass.__model.destroy()
        klass.__model = None

    @classmethod
    def toggle_text(klass):
        text_visible = config.getboolean("browsers", "album_text", True)
        for covergrid in klass.instances():
            for child in covergrid.view:
                child.props.text_visible = text_visible

    @classmethod
    def toggle_item_all(klass):
        show = config.getboolean("browsers", "covergrid_all", True)
        for covergrid in klass.instances():
            covergrid.__model_filter.props.include_item_all = show

    @classmethod
    def toggle_wide(klass):
        wide = config.getboolean("browsers", "covergrid_wide", False)
        for covergrid in klass.instances():
            covergrid.songcontainer.set_orientation(
                Gtk.Orientation.HORIZONTAL if wide
                else Gtk.Orientation.VERTICAL)

    @classmethod
    def update_mag(klass):
        cover_size = _get_cover_size()
        for covergrid in klass.instances():
            for child in covergrid.view:
                child.cover_size = cover_size
            covergrid.view.queue_resize()

    def __init__(self, library):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6)

        self.songcontainer = qltk.paned.ConfigRVPaned(
            "browsers", "covergrid_pos", 0.4)
        if config.getboolean("browsers", "covergrid_wide", False):
            self.songcontainer.set_orientation(Gtk.Orientation.HORIZONTAL)

        self._register_instance()
        self._init_model(library)

        self.__cover_cancel = Gio.Cancellable()

        model_sort = AlbumListSortModel(model=self.__model)
        self.__model_filter = model_filter = AlbumListFilterModel(
            include_item_all=config.getboolean("browsers", "covergrid_all", True),
            child_model=model_sort)

        def create_album_widget(model):
            item_padding = config.getint("browsers", "item_padding", 6)
            text_visible = config.getboolean("browsers", "album_text", True)
            cover_size = _get_cover_size()
            widget = AlbumWidget(
                model,
                display_pattern=self.display_pattern,
                cover_size=cover_size,
                padding=item_padding,
                text_visible=text_visible,
                cancelable=self.__cover_cancel)
            widget.connect("songs-menu", self.__popup)
            return widget

        self.view = view = Gtk.FlowBox(
            valign=Gtk.Align.START,
            activate_on_single_click=False,
            selection_mode=Gtk.SelectionMode.MULTIPLE,
            homogeneous=True,
            min_children_per_line=1,
            max_children_per_line=10,
            row_spacing=config.getint("browsers", "row_spacing", 6),
            column_spacing=config.getint("browsers", "column_spacing", 6))

        self.scrollwin = sw = CoverGridContainer(view)

        view.connect("selected-children-changed",
            util.DeferredSignal(
                lambda _: self.__update_songs(select_default=False),
                owner=self))

        targets = [("text/x-quodlibet-songs", Gtk.TargetFlags.SAME_APP, 1),
                   ("text/uri-list", 0, 2)]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]
        view.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK, targets, Gdk.DragAction.COPY)

        view.connect("drag-data-get", self.__drag_data_get)
        view.connect("child-activated", self.__child_activated)

        self.accelerators = Gtk.AccelGroup()
        search = SearchBarBox(completion=AlbumTagCompletion(),
                              accel_group=self.accelerators)
        search.connect("query-changed", lambda *a: self.__update_filter())
        connect_obj(search, "focus-out", lambda w: w.grab_focus(), view)
        self.__search = search

        prefs = PreferencesButton(self, model_sort)
        search.pack_start(prefs, False, True, 0)
        self.pack_start(Align(search, left=6, top=0), False, True, 0)
        self.pack_start(sw, True, True, 0)

        self.__update_filter()
        model_filter.connect("notify::filter",
            util.DeferredSignal(lambda *a: self.__update_songs(), owner=self))

        self.connect("key-press-event", self.__key_pressed, library.librarian)
        self.connect("destroy", self.__destroy)

        if app.cover_manager:
            connect_destroy(
                app.cover_manager, "cover-changed", self.__cover_changed)

        # show all before binding the model, so a label in a flowbox child will
        # stay hidden if so configured by the "browsers.album_text" property.
        self.show_all()
        view.bind_model(model_filter, create_album_widget)

    def __update_songs(self, select_default=True):
        songs = self.__get_selected_songs(sort=False)
        if not select_default or songs:
            self.songs_selected(songs)
        else:
            child = self.view.get_child_at_index(0)
            if child:
                self.view.select_child(child)
            else:
                self.songs_selected(songs)

    def __key_pressed(self, widget, event, librarian):
        if qltk.is_accel(event, "<Primary>I"):
            songs = self.__get_selected_songs()
            if songs:
                window = Information(librarian, songs, self)
                window.show()
            return True
        elif qltk.is_accel(event, "<Primary>Return", "<Primary>KP_Enter"):
            qltk.enqueue(self.__get_selected_songs())
            return True
        elif qltk.is_accel(event, "<alt>Return"):
            songs = self.__get_selected_songs()
            if songs:
                window = SongProperties(librarian, songs, self)
                window.show()
            return True
        return False

    def __destroy(self, browser):
        self.__cover_cancel.cancel()

        self.view.bind_model(None, lambda _: None)
        self.__model_filter.destroy()
        self.__model_filter = None

        if not CoverGrid.instances():
            CoverGrid._destroy_model()

    def __cover_changed(self, manager, songs):
        songs = set(songs)

        for child in self.view:
            if not songs:
                break
            album = child.model.album
            if album is None:
                continue
            match = songs & album.songs
            if match:
                child.populate()
                songs -= match

    def __update_filter(self, scroll_up=True):
        if scroll_up:
            self.scrollwin.scroll_up()

        q = self.__search.get_query(star=["~people", "album"])
        self.__model_filter.props.filter = None if q.matches_all else q.search

    def __popup(self, widget):
        if not widget.is_selected():
            self.view.unselect_all()
        self.view.select_child(widget)

        albums = self.__get_selected_albums()
        songs = self.__get_songs_from_albums(albums)

        button_label = ngettext(
            "Reload album _cover", "Reload album _covers", len(albums))
        button = MenuItem(button_label, Icons.VIEW_REFRESH)
        button.connect("activate", self.__refresh_cover, widget)

        menu = SongsMenu(self.__library, songs, items=[[button]])
        menu.show_all()
        popup_menu_at_widget(
            menu, widget, Gdk.BUTTON_SECONDARY, Gtk.get_current_event_time())

    def __refresh_cover(self, menuitem, view):
        for child in self.view.get_selected_children():
            child.populate()

    def refresh_all(self):
        display_pattern = self.display_pattern
        for child in self.view:
            child.display_pattern = display_pattern

    def __get_selected_albums(self):
        items = []
        for child in self.view.get_selected_children():
            album = child.model.album
            if album is None:
                model = self.__model_filter
                return [item.album for item in model if item.album is not None]
            items.append(album)
        return items

    def __get_songs_from_albums(self, albums, sort=True):
        # Sort first by how the albums appear in the model itself,
        # then within the album using the default order.
        songs = []
        if sort:
            for album in albums:
                songs.extend(sorted(album.songs, key=lambda s: s.sort_key))
        else:
            for album in albums:
                songs.extend(album.songs)
        return songs

    def __get_selected_songs(self, sort=True):
        albums = self.__get_selected_albums()
        return self.__get_songs_from_albums(albums, sort)

    def __drag_data_get(self, view, ctx, sel, tid, etime):
        songs = self.__get_selected_songs()
        if tid == 1:
            qltk.selection_set_songs(sel, songs)
        else:
            sel.set_uris([song("~uri") for song in songs])

    def __child_activated(self, view, child):
        self.songs_activated()

    def active_filter(self, song):
        for album in self.__get_selected_albums():
            if song in album.songs:
                return True
        return False

    def can_filter_text(self):
        return True

    def filter_text(self, text):
        self.__search.set_text(text)
        if Query(text).is_parsable:
            self.__update_filter()
            self.__update_songs()

    def get_filter_text(self):
        return self.__search.get_text()

    def can_filter_albums(self):
        return True

    def can_filter(self, key):
        # Numerics are different for collections, and although title works,
        # it's not of much use here.
        if key is not None and (key.startswith("~#") or key == "title"):
            return False
        return super().can_filter(key)

    def filter_albums(self, values):
        changed = self.__select_by_func(
            lambda album: album is not None and album.key in values)
        self.view.grab_focus()
        if changed:
            self.__update_songs()

    def list_albums(self):
        model = self.__model_filter
        return [item.album.key for item in model if item.album is not None]

    def unfilter(self):
        self.filter_text("")

    def __select_by_func(self, func, scroll=True, one=False):
        first = True
        view = self.view
        for i, item in enumerate(self.__model_filter):
            if not func(item.album):
                continue
            child = view.get_child_at_index(i)
            if first:
                view.unselect_all()
                view.select_child(child)
                if scroll:
                    self.scrollwin.scroll_to_child(child)
                first = False
                if one:
                    break
            else:
                view.select_child(child)
        return not first

    def save(self):
        conf = self.__get_config_string()
        config.settext("browsers", "covergrid", conf)
        text = self.__search.get_text()
        config.settext("browsers", "query_text", text)

    def restore(self):
        text = config.gettext("browsers", "query_text")
        entry = self.__search
        entry.set_text(text)

        if Query(text).is_parsable:
            self.__update_filter(scroll_up=False)

        keys = config.gettext("browsers", "covergrid", "").split("\n")

        if keys != [""]:
            self.__select_by_func(
                lambda album: album is not None and album.str_key in keys)
        else:
            self.__select_by_func(lambda album: album is None, one=True)

    def finalize(self, restored):
        if not restored:
            self.__select_by_func(lambda album: album is None, one=True)

    def scroll(self, song):
        album_key = song.album_key
        self.__select_by_func(
            lambda album: album is not None and album.key == album_key,
            one=True)

    def activate(self):
        self.__update_songs()

    def __get_config_string(self):
        albums = []
        for child in self.view.get_selected_children():
            album = child.model.album
            if album is None:
                albums.clear()
                break
            albums.append(album)

        if not albums:
            return ""

        confval = "\n".join((a.str_key for a in albums))
        # ConfigParser strips a trailing \n so we move it to the front
        if confval and confval[-1] == "\n":
            confval = "\n" + confval[:-1]
        return confval
