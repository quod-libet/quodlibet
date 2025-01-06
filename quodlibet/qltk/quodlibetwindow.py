# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#           2012 Christoph Reiter
#           2012-2023 Nick Boultbee
#           2017 Uriel Zajaczkovski
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os

from gi.repository import Gtk, Gdk, GLib, Gio, GObject
from senf import uri2fsn, fsnative, path2fsn

import quodlibet

from quodlibet import browsers
from quodlibet import config
from quodlibet import const
from quodlibet import formats
from quodlibet import qltk
from quodlibet import util
from quodlibet import app
from quodlibet import ngettext
from quodlibet import _
from quodlibet.qltk.paned import ConfigRHPaned

from quodlibet.qltk.appwindow import AppWindow
from quodlibet.update import UpdateDialog
from quodlibet.formats.remote import RemoteFile
from quodlibet.qltk.browser import LibraryBrowser, FilterMenu
from quodlibet.qltk.chooser import choose_folders, choose_files, \
    create_chooser_filter
from quodlibet.qltk.controls import PlayControls
from quodlibet.qltk.cover import CoverImage
from quodlibet.qltk.getstring import GetStringDialog
from quodlibet.qltk.bookmarks import EditBookmarks
from quodlibet.qltk.shortcuts import show_shortcuts
from quodlibet.qltk.info import SongInfo
from quodlibet.qltk.information import Information
from quodlibet.qltk.msg import ErrorMessage, WarningMessage
from quodlibet.qltk.notif import StatusBar, TaskController
from quodlibet.qltk.playorder import PlayOrderWidget, RepeatSongForever, \
    RepeatListForever
from quodlibet.qltk.pluginwin import PluginWindow
from quodlibet.qltk.properties import SongProperties
from quodlibet.qltk.prefs import PreferencesWindow
from quodlibet.qltk.queue import QueueExpander
from quodlibet.qltk.songlist import SongList, get_columns, set_columns
from quodlibet.qltk.songmodel import PlaylistMux
from quodlibet.qltk.x import RVPaned, Align, ScrolledWindow, Action
from quodlibet.qltk.x import ToggleAction, RadioAction, HighlightToggleButton
from quodlibet.qltk.x import SeparatorMenuItem, MenuItem
from quodlibet.qltk import Icons
from quodlibet.qltk.about import AboutDialog
from quodlibet.util import copool, connect_destroy, connect_after_destroy
from quodlibet.util.library import get_scan_dirs
from quodlibet.util import connect_obj, print_d
from quodlibet.util.library import background_filter, scan_library
from quodlibet.util.path import uri_is_valid
from quodlibet.qltk.window import PersistentWindowMixin, Window, on_first_map
from quodlibet.qltk.songlistcolumns import CurrentColumn


class PlayerOptions(GObject.Object):
    """Provides a simplified interface for playback options.

    This currently provides a limited view on the play order state which is
    useful for external interfaces (mpd, mpris, etc.) and for reducing
    the dependency on the state holding widgets in the main window.

    Usable as long as the main window is not destroyed, or until `destroy()`
    is called.
    """

    __gproperties__ = {
        "shuffle": (bool, "", "", False,
                   GObject.ParamFlags.READABLE | GObject.ParamFlags.WRITABLE),
        "repeat": (bool, "", "", False,
                   GObject.ParamFlags.READABLE | GObject.ParamFlags.WRITABLE),
        "single": (bool, "", "", False,
                   GObject.ParamFlags.READABLE | GObject.ParamFlags.WRITABLE),
        "stop-after": (
            bool, "", "", False,
            GObject.ParamFlags.READABLE | GObject.ParamFlags.WRITABLE),
    }

    def __init__(self, window):
        """`window` is a QuodLibetWindow"""

        super().__init__()

        self._stop_after = window.stop_after
        self._said = self._stop_after.connect(
            "toggled", lambda *x: self.notify("stop-after"))

        def order_changed(*args):
            self.notify("shuffle")
            self.notify("single")

        self._order_widget = window.order
        self._oid = self._order_widget.connect("changed", order_changed)

        window.connect("destroy", self._window_destroy)

    def _window_destroy(self, window):
        self.destroy()

    def destroy(self):
        if self._order_widget:
            self._order_widget.disconnect(self._oid)
            self._order_widget = None
        if self._stop_after:
            self._stop_after.disconnect(self._said)
            self._stop_after = None

    def do_get_property(self, param):
        return getattr(self, param.name.replace("-", "_"))

    def do_set_property(self, param, value):
        setattr(self, param.name.replace("-", "_"), value)

    @property
    def single(self):
        """If only the current song is considered as next track

        When `repeat` is False the playlist will end after this song finishes.
        When `repeat` is True the current song will be replayed.
        """

        return (self._order_widget and self._order_widget.repeated and
                self._order_widget.repeater is RepeatSongForever)

    @single.setter
    def single(self, value):
        if value:
            self.repeat = True
            self._order_widget.repeater = RepeatSongForever
        else:
            self.repeat = False
            self._order_widget.repeater = RepeatListForever

    @property
    def shuffle(self):
        """If a shuffle-like (reordering) play order is active"""

        return self._order_widget.shuffled

    @shuffle.setter
    def shuffle(self, value):
        self._order_widget.shuffled = value

    @property
    def repeat(self):
        """If the player is in some kind of repeat mode"""

        return self._order_widget.repeated

    @repeat.setter
    def repeat(self, value):
        print_d("setting repeated to %s" % value)
        self._order_widget.repeated = value

    @property
    def stop_after(self):
        """If the player will pause after the current song ends"""

        return self._stop_after.get_active()

    @stop_after.setter
    def stop_after(self, value):
        self._stop_after.set_active(value)


class DockMenu(Gtk.Menu):
    """Menu used for the OSX dock and the tray icon"""

    def __init__(self, app):
        super().__init__()

        player = app.player

        play_item = MenuItem(_("_Play"), Icons.MEDIA_PLAYBACK_START)
        play_item.connect("activate", self._on_play, player)
        pause_item = MenuItem(_("P_ause"), Icons.MEDIA_PLAYBACK_PAUSE)
        pause_item.connect("activate", self._on_pause, player)
        self.append(play_item)
        self.append(pause_item)

        previous = MenuItem(_("Pre_vious"), Icons.MEDIA_SKIP_BACKWARD)
        previous.connect("activate", lambda *args: player.previous())
        self.append(previous)

        next_ = MenuItem(_("_Next"), Icons.MEDIA_SKIP_FORWARD)
        next_.connect("activate", lambda *args: player.next())
        self.append(next_)

        browse = qltk.MenuItem(_("_Browse Library"), Icons.EDIT_FIND)
        browse_sub = Gtk.Menu()
        for Kind in browsers.browsers:
            i = Gtk.MenuItem(label=Kind.accelerated_name, use_underline=True)
            connect_obj(i,
                "activate", LibraryBrowser.open, Kind, app.library, app.player)
            browse_sub.append(i)

        browse.set_submenu(browse_sub)
        self.append(SeparatorMenuItem())
        self.append(browse)

        self.show_all()
        self.hide()

    def _on_play(self, item, player):
        player.paused = False

    def _on_pause(self, item, player):
        player.paused = True


class MainSongList(SongList):
    """SongList for the main browser's displayed songs."""

    _activated = False

    def __init__(self, library, player):
        super().__init__(library, player, update=True)
        self.set_first_column_type(CurrentColumn)

        self.connect("row-activated", self.__select_song, player)

        # ugly.. so the main window knows if the next song-started
        # comes from an row-activated or anything else.
        def reset_activated(*args):
            self._activated = False
        connect_after_destroy(player, "song-started", reset_activated)

        self.connect("orders-changed", self.__orders_changed)

    def __orders_changed(self, *args):
        l = []
        for tag, reverse in self.get_sort_orders():
            l.append("%d%s" % (int(reverse), tag))
        config.setstringlist("memory", "sortby", l)

    def __select_song(self, widget, indices, col, player):
        self._activated = True
        iter = self.model.get_iter(indices)
        if player.go_to(iter, explicit=True, source=self.model):
            player.paused = False


class TopBar(Gtk.Toolbar):
    def __init__(self, parent, player, library):
        super().__init__()

        # play controls
        control_item = Gtk.ToolItem()
        self.insert(control_item, 0)
        t = PlayControls(player, library.librarian)
        self.volume = t.volume

        # only restore the volume in case it is managed locally, otherwise
        # this could affect the system volume
        if not player.has_external_volume:
            player.volume = config.getfloat("memory", "volume")

        connect_destroy(player, "notify::volume", self._on_volume_changed)
        control_item.add(t)

        self.insert(Gtk.SeparatorToolItem(), 1)

        info_item = Gtk.ToolItem()
        self.insert(info_item, 2)
        info_item.set_expand(True)

        box = Gtk.Box(spacing=6)
        info_item.add(box)
        qltk.add_css(self, "GtkToolbar {padding: 3px;}")

        self._pattern_box = Gtk.VBox(spacing=3)

        # song text
        info_pattern_path = os.path.join(quodlibet.get_user_dir(), "songinfo")
        text = SongInfo(library.librarian, player, info_pattern_path)
        self._pattern_box.pack_start(text, True, True, 0)
        box.pack_start(self._pattern_box, True, True, 0)

        # cover image
        if config.getboolean("settings", "disable_cover"):
            self.image = None
        else:
            self.image = CoverImage(resize=True)
            connect_destroy(player, "song-started", self.__new_song)

            # FIXME: makes testing easier
            if app.cover_manager:
                connect_destroy(
                    app.cover_manager, "cover-changed",
                    self.__song_art_changed, library)

            box.pack_start(Align(self.image, top=3, right=3), False, True, 0)

            # On older Gtk+ (3.4, at least)
            # setting a margin on CoverImage leads to errors and result in the
            # QL window not being visible for some reason.
            assert self.image.props.margin == 0

        for child in self.get_children():
            child.show_all()

        context = self.get_style_context()
        context.add_class("primary-toolbar")

    def set_seekbar_widget(self, widget):
        children = self._pattern_box.get_children()
        if len(children) > 1:
            self._pattern_box.remove(children[-1])

        if widget:
            self._pattern_box.pack_start(widget, False, True, 0)

    def _on_volume_changed(self, player, *args):
        config.set("memory", "volume", str(player.volume))

    def __new_song(self, player, song):
        if self.image: self.image.set_song(song)

    def __song_art_changed(self, player, songs, library):
        if self.image: self.image.refresh()


class QueueButton(HighlightToggleButton):

    def __init__(self):
        # XXX: view-list isn't part of the fdo spec, so fall back t justify..
        gicon = Gio.ThemedIcon.new_from_names(
            ["view-list-symbolic", "format-justify-fill-symbolic",
             "view-list", "format-justify"])
        image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.SMALL_TOOLBAR)

        super().__init__(image=image)

        self.set_name("ql-queue-button")
        qltk.add_css(self, """
            #ql-queue-button {
                padding: 0px;
            }
        """)
        self.set_size_request(26, 26)

        self.set_tooltip_text(_("Toggle queue visibility"))


class StatusBarBox(Gtk.HBox):

    def __init__(self, play_order, queue):
        super().__init__(spacing=6)
        self.pack_start(play_order, False, True, 0)
        self.statusbar = StatusBar(TaskController.default_instance)
        self.pack_start(self.statusbar, True, True, 0)
        queue_button = QueueButton()
        queue_button.bind_property("active", queue, "visible",
                                   GObject.BindingFlags.BIDIRECTIONAL)
        queue_button.props.active = queue.props.visible

        self.pack_start(queue_button, False, True, 0)


class PlaybackErrorDialog(ErrorMessage):

    def __init__(self, parent, player_error):
        def add_full_stop(s):
            return s and s.rstrip(".") + "."
        description = add_full_stop(util.escape(player_error.short_desc))
        details = add_full_stop(util.escape(player_error.long_desc or ""))
        if details:
            description += " " + details

        super().__init__(
            parent, _("Playback Error"), description)


class ConfirmLibDirSetup(WarningMessage):

    RESPONSE_SETUP = 1

    def __init__(self, parent):
        title = _("Set up library directories?")
        description = _("You don't have any music library set up. "
                        "Would you like to do that now?")

        super().__init__(
            parent, title, description, buttons=Gtk.ButtonsType.NONE)

        self.add_button(_("_Not Now"), Gtk.ResponseType.CANCEL)
        self.add_button(_("_Set Up"), self.RESPONSE_SETUP)
        self.set_default_response(Gtk.ResponseType.CANCEL)


MENU = """
<ui>
  <menubar name='Menu'>

    <menu action='File'>
      <menuitem action='AddFolders' always-show-image='true'/>
      <menuitem action='AddFiles' always-show-image='true'/>
      <menuitem action='AddLocation' always-show-image='true'/>
      <separator/>
      <menuitem action='Preferences' always-show-image='true'/>
      <menuitem action='Plugins' always-show-image='true'/>
      <separator/>
      <menuitem action='RefreshLibrary' always-show-image='true'/>
      <separator/>
      <menuitem action='Quit' always-show-image='true'/>
    </menu>

    <menu action='Song'>
      <menuitem action='EditBookmarks' always-show-image='true'/>
      <menuitem action='EditTags' always-show-image='true'/>
      <separator/>
      <menuitem action='Information' always-show-image='true'/>
      <separator/>
      <menuitem action='Jump' always-show-image='true'/>
    </menu>

    <menu action='Control'>
      <menuitem action='Previous' always-show-image='true'/>
      <menuitem action='PlayPause' always-show-image='true'/>
      <menuitem action='Next' always-show-image='true'/>
      <menuitem action='Stop' always-show-image='true'/>
      <menuitem action='StopAfter' always-show-image='true'/>
    </menu>

    <menu action='Browse'>
      %(filters_menu)s
      <separator/>
      <menu action='BrowseLibrary' always-show-image='true'>
        %(browsers)s
      </menu>
      <separator />

      %(views)s
    </menu>

    <menu action='Help'>
      <menuitem action='OnlineHelp' always-show-image='true'/>
      <menuitem action='Shortcuts' always-show-image='true'/>
      <menuitem action='SearchHelp' always-show-image='true'/>
      <separator/>
      <menuitem action='CheckUpdates' always-show-image='true'/>
      <menuitem action='About' always-show-image='true'/>
    </menu>

  </menubar>
</ui>
"""


def secondary_browser_menu_items():
    items = (_browser_items("Browser") + ["<separator />"] +
             _browser_items("Browser", True))
    return "\n".join(items)


def browser_menu_items():
    items = (_browser_items("View") + ["<separator />"] +
             _browser_items("View", True))
    return "\n".join(items)


def _browser_items(prefix, external=False):
    return [f"<menuitem action='{prefix}{kind.__name__}'/>"
            for kind in browsers.browsers if kind.uses_main_library ^ external]


DND_URI_LIST, = range(1)


class SongListPaned(RVPaned):

    def __init__(self, song_scroller, qexpander):
        super().__init__()

        self.pack1(song_scroller, resize=True, shrink=False)
        self.pack2(qexpander, resize=True, shrink=False)

        self.set_relative(config.getfloat("memory", "queue_position", 0.75))
        self.connect(
            "notify::position", self._changed, "memory", "queue_position")

        self._handle_position = self.get_relative()
        qexpander.connect("notify::visible", self._expand_or)
        qexpander.connect("notify::expanded", self._expand_or)
        qexpander.connect("draw", self._check_minimize)

        self.connect("button-press-event", self._on_button_press)
        self.connect("notify", self._moved_pane_handle)

    @property
    def _expander(self):
        return self.get_child2()

    def _on_button_press(self, pane, event):
        # If we start to drag the pane handle while the
        # queue expander is unexpanded, expand it and move the handle
        # to the bottom, so we can 'drag' the queue out

        if event.window != pane.get_handle_window():
            return False

        if not self._expander.get_expanded():
            self._expander.set_expanded(True)
            pane.set_relative(1.0)
        return False

    def _expand_or(self, widget, prop):
        if self._expander.get_property("expanded"):
            self.set_relative(self._handle_position)

    def _moved_pane_handle(self, widget, prop):
        if self._expander.get_property("expanded"):
            self._handle_position = self.get_relative()

    def _check_minimize(self, *args):
        if not self._expander.get_property("expanded"):
            p_max = self.get_property("max-position")
            p_cur = self.get_property("position")
            if p_max != p_cur:
                self.set_property("position", p_max)

    def _changed(self, widget, event, section, option):
        if self._expander.get_expanded() and self.get_property("position-set"):
            config.set(section, option, str(self.get_relative()))


class QuodLibetWindow(Window, PersistentWindowMixin, AppWindow):

    def __init__(self, library, player, headless=False, restore_cb=None):
        super().__init__(dialog=False)

        self.__destroyed = False
        self.__update_title(player)
        self.set_default_size(600, 480)

        main_box = Gtk.VBox()
        self.add(main_box)
        self.side_book = qltk.Notebook()

        # get the playlist up before other stuff
        self.songlist = MainSongList(library, player)
        self.songlist.connect("key-press-event", self.__songlist_key_press)
        self.songlist.connect_after(
            "drag-data-received", self.__songlist_drag_data_recv)
        self.song_scroller = ScrolledWindow()
        self.song_scroller.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.song_scroller.set_shadow_type(Gtk.ShadowType.IN)
        self.song_scroller.add(self.songlist)

        self.qexpander = QueueExpander(library, player)
        self.qexpander.set_no_show_all(True)
        self.qexpander.set_visible(config.getboolean("memory", "queue"))

        def on_queue_visible(qex, param):
            config.set("memory", "queue", str(qex.get_visible()))

        self.qexpander.connect("notify::visible", on_queue_visible)

        self.playlist = PlaylistMux(
            player, self.qexpander.model, self.songlist.model)

        self.__player = player
        # create main menubar, load/restore accelerator groups
        self.__library = library
        ui = self.__create_menu(player, library)
        accel_group = ui.get_accel_group()
        self.add_accel_group(accel_group)

        def scroll_and_jump(*args):
            self.__jump_to_current(True, None, True)

        keyval, mod = Gtk.accelerator_parse("<Primary><shift>J")
        accel_group.connect(keyval, mod, 0, scroll_and_jump)

        # custom accel map
        accel_fn = os.path.join(quodlibet.get_user_dir(), "accels")
        Gtk.AccelMap.load(accel_fn)
        # save right away so we fill the file with example comments of all
        # accels
        Gtk.AccelMap.save(accel_fn)

        menubar = ui.get_widget("/Menu")

        # Since https://git.gnome.org/browse/gtk+/commit/?id=b44df22895c79
        # toplevel menu items show an empty 16x16 image. While we don't
        # need image items there UIManager creates them by default.
        # Work around by removing the empty GtkImages
        for child in menubar.get_children():
            if isinstance(child, Gtk.ImageMenuItem):
                child.set_image(None)

        main_box.pack_start(menubar, False, True, 0)

        top_bar = TopBar(self, player, library)
        main_box.pack_start(top_bar, False, True, 0)
        self.top_bar = top_bar

        self.__browserbox = Align(top=3, bottom=3)
        self.__paned = paned = ConfigRHPaned("memory", "sidebar_pos", 0.25)
        paned.pack1(self.__browserbox, resize=True)
        # We'll pack2 when necessary (when the first sidebar plugin is set up)

        main_box.pack_start(paned, True, True, 0)

        play_order = PlayOrderWidget(self.songlist.model, player)
        statusbox = StatusBarBox(play_order, self.qexpander)
        self.order = play_order
        self.statusbar = statusbox.statusbar

        align = Align(statusbox, top=1, bottom=4, left=6, right=6)
        main_box.pack_start(align, False, True, 0)

        self.songpane = SongListPaned(self.song_scroller, self.qexpander)
        self.songpane.show_all()

        try:
            orders = []
            for e in config.getstringlist("memory", "sortby", []):
                orders.append((e[1:], int(e[0])))
        except ValueError:
            pass
        else:
            self.songlist.set_sort_orders(orders)

        self.browser = None
        self.ui = ui

        main_box.show_all()

        self._playback_error_dialog = None
        connect_destroy(player, "song-started", self.__song_started)
        connect_destroy(player, "paused", self.__update_paused, True)
        connect_destroy(player, "unpaused", self.__update_paused, False)
        # make sure we redraw all error indicators before opening
        # a dialog (blocking the main loop), so connect after default handlers
        connect_after_destroy(player, "error", self.__player_error)
        # connect after to let SongTracker update stats
        connect_after_destroy(player, "song-ended", self.__song_ended)

        # set at least the playlist. the song should be restored
        # after the browser emits the song list
        player.setup(self.playlist, None, 0)
        self.__restore_cb = restore_cb
        self.__first_browser_set = True

        restore_browser = not headless
        try:
            self._select_browser(
                self, config.get("memory", "browser"), library, player,
                restore_browser)
        except Exception:
            config.set("memory", "browser", browsers.name(browsers.default))
            config.save()
            raise

        self.songlist.connect("popup-menu", self.__songs_popup_menu)
        self.songlist.connect("columns-changed", self.__cols_changed)
        self.songlist.connect("columns-changed", self.__hide_headers)
        self.songlist.info.connect("changed", self.__set_totals)

        lib = library.librarian
        connect_destroy(lib, "changed", self.__song_changed, player)

        targets = [("text/uri-list", Gtk.TargetFlags.OTHER_APP, DND_URI_LIST)]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]

        self.drag_dest_set(
            Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        self.connect("drag-data-received", self.__drag_data_received)

        if not headless:
            on_first_map(self, self.__configure_scan_dirs, library)

        if config.getboolean("library", "refresh_on_start"):
            self.__rebuild(None, False)

        self.connect("key-press-event", self.__key_pressed, player)

        self.connect("destroy", self.__destroy)

        self.enable_window_tracking("quodlibet")

    def hide_side_book(self):
        self.side_book.hide()

    def add_sidebar(self, box, name):
        vbox = Gtk.Box(margin=0)
        vbox.pack_start(box, True, True, 0)
        vbox.show()
        if self.side_book_empty:
            self.add_sidebar_to_layout(self.side_book)
        self.side_book.append_page(vbox, label=name)
        self.side_book.set_tab_detachable(vbox, False)
        self.side_book.show_all()
        return vbox

    def remove_sidebar(self, widget):
        self.side_book.remove_page(self.side_book.page_num(widget))
        if self.side_book_empty:
            print_d("Hiding sidebar")
            self.__paned.remove(self.__paned.get_children()[1])

    def add_sidebar_to_layout(self, widget):
        print_d("Recreating sidebar")
        align = Align(widget, top=6, bottom=3)
        self.__paned.pack2(align, shrink=True)
        align.show_all()

    @property
    def side_book_empty(self):
        return not self.side_book.get_children()

    def set_seekbar_widget(self, widget):
        """Add an alternative seek bar widget.

        Args:
            widget (Gtk.Widget): a new widget or None to remove the current one
        """

        self.top_bar.set_seekbar_widget(widget)

    def set_as_osx_window(self, osx_app):
        assert osx_app

        self._dock_menu = DockMenu(app)
        osx_app.set_dock_menu(self._dock_menu)

        menu = self.ui.get_widget("/Menu")
        menu.hide()
        osx_app.set_menu_bar(menu)
        # Reparent some items to the "Application" menu
        item = self.ui.get_widget("/Menu/Help/About")
        osx_app.insert_app_menu_item(item, 0)
        osx_app.insert_app_menu_item(Gtk.SeparatorMenuItem(), 1)
        item = self.ui.get_widget("/Menu/File/Preferences")
        osx_app.insert_app_menu_item(item, 2)
        quit_item = self.ui.get_widget("/Menu/File/Quit")
        quit_item.hide()

    def get_is_persistent(self):
        return True

    def open_file(self, filename):
        assert isinstance(filename, fsnative)

        song = self.__library.add_filename(filename, add=False)
        if song is not None:
            if self.__player.go_to(song):
                self.__player.paused = False
            return True
        else:
            return False

    def enqueue(self, songs, limit=0):
        """Append `songs` to the queue

        Ask for confimation if the number of songs exceeds `limit`.
        """

        if len(songs) > limit:
            dialog = ConfirmEnqueue(self, len(songs))
            if dialog.run() != Gtk.ResponseType.YES:
                return

        self.playlist.enqueue(songs)

    def __player_error(self, player, song, player_error):
        # it's modal, but mmkeys etc. can still trigger new ones
        if self._playback_error_dialog:
            self._playback_error_dialog.destroy()
        dialog = PlaybackErrorDialog(self, player_error)
        self._playback_error_dialog = dialog
        dialog.run()
        self._playback_error_dialog = None

    def __configure_scan_dirs(self, library):
        """Get user to configure scan dirs, if none is set up"""
        if not get_scan_dirs() and not len(library) and \
                quodlibet.is_first_session("quodlibet"):
            print_d("Couldn't find any scan dirs")

            resp = ConfirmLibDirSetup(self).run()
            if resp == ConfirmLibDirSetup.RESPONSE_SETUP:
                prefs = PreferencesWindow(self)
                prefs.set_page("library")
                prefs.show()

    def __keyboard_shortcuts(self, action):
        show_shortcuts(self)

    def __edit_bookmarks(self, librarian, player):
        if player.song:
            window = EditBookmarks(self, librarian, player)
            window.show()

    def __key_pressed(self, widget, event, player):
        if not player.song:
            return

        def seek_relative(seconds):
            current = player.get_position()
            current += seconds * 1000
            current = min(player.song("~#length") * 1000 - 1, current)
            current = max(0, current)
            player.seek(current)

        if qltk.is_accel(event, "<alt>Right"):
            seek_relative(10)
            return True
        elif qltk.is_accel(event, "<alt>Left"):
            seek_relative(-10)
            return True

    def __destroy(self, *args):
        self.playlist.destroy()

        # The tray icon plugin tries to unhide QL because it gets disabled
        # on Ql exit. The window should stay hidden after destroy.
        self.show = lambda: None
        self.present = self.show

    def __drag_data_received(self, widget, ctx, x, y, sel, tid, etime):
        assert tid == DND_URI_LIST

        uris = sel.get_uris()

        dirs = []
        error = False
        for uri in uris:
            try:
                filename = uri2fsn(uri)
            except ValueError:
                filename = None

            if filename is not None:
                loc = os.path.normpath(filename)
                if os.path.isdir(loc):
                    dirs.append(loc)
                else:
                    loc = os.path.realpath(loc)
                    if loc not in self.__library:
                        self.__library.add_filename(loc)
            elif app.player.can_play_uri(uri):
                if uri not in self.__library:
                    self.__library.add([RemoteFile(uri)])
            else:
                error = True
                break
        Gtk.drag_finish(ctx, not error, False, etime)
        if error:
            ErrorMessage(
                self, _("Unable to add songs"),
                _("%s uses an unsupported protocol.") % util.bold(uri),
                escape_desc=False).run()
        else:
            if dirs:
                copool.add(
                    self.__library.scan, dirs,
                    cofuncid="library", funcid="library")

    def __songlist_key_press(self, songlist, event):
        return self.browser.key_pressed(event)

    def __songlist_drag_data_recv(self, view, *args):
        if self.browser.can_reorder:
            songs = view.get_songs()
            self.browser.reordered(songs)
        self.songlist.clear_sort()

    def __create_menu(self, player, library):
        def add_view_items(ag):
            act = Action(name="Information", label=_("_Information"),
                         icon_name=Icons.DIALOG_INFORMATION)
            act.connect("activate", self.__current_song_info)
            ag.add_action(act)

            act = Action(name="Jump", label=_("_Jump to Playing Song"),
                         icon_name=Icons.GO_JUMP)
            self.__jump_to_current(True, None, True)
            act.connect("activate", self.__jump_to_current)
            ag.add_action_with_accel(act, "<Primary>J")

        def add_top_level_items(ag):
            ag.add_action(Action(name="File", label=_("_File")))
            ag.add_action(Action(name="Song", label=_("_Song")))
            ag.add_action(Action(name="View", label=_("_View")))
            ag.add_action(Action(name="Browse", label=_("_Browse")))
            ag.add_action(Action(name="Control", label=_("_Control")))
            ag.add_action(Action(name="Help", label=_("_Help")))

        ag = Gtk.ActionGroup.new("QuodLibetWindowActions")
        add_top_level_items(ag)
        add_view_items(ag)

        act = Action(name="AddFolders", label=_("_Add a Folder…"),
                     icon_name=Icons.LIST_ADD)
        act.connect("activate", self.open_chooser)
        ag.add_action_with_accel(act, "<Primary>O")

        act = Action(name="AddFiles", label=_("_Add a File…"),
                     icon_name=Icons.LIST_ADD)
        act.connect("activate", self.open_chooser)
        ag.add_action(act)

        act = Action(name="AddLocation", label=_("_Add a Location…"),
                     icon_name=Icons.LIST_ADD)
        act.connect("activate", self.open_location)
        ag.add_action(act)

        act = Action(name="BrowseLibrary", label=_("Open _Browser"),
                     icon_name=Icons.EDIT_FIND)
        ag.add_action(act)

        act = Action(name="Preferences", label=_("_Preferences"),
                     icon_name=Icons.PREFERENCES_SYSTEM)
        act.connect("activate", self.__preferences)
        ag.add_action(act)

        act = Action(name="Plugins", label=_("_Plugins"),
                     icon_name=Icons.SYSTEM_RUN)
        act.connect("activate", self.__plugins)
        ag.add_action(act)

        act = Action(name="Quit", label=_("_Quit"),
                     icon_name=Icons.APPLICATION_EXIT)
        act.connect("activate", lambda *x: self.destroy())
        ag.add_action_with_accel(act, "<Primary>Q")

        act = Action(name="EditTags", label=_("Edit _Tags"),
                     icon_name=Icons.DOCUMENT_PROPERTIES)
        act.connect("activate", self.__current_song_prop)
        ag.add_action(act)

        act = Action(name="EditBookmarks", label=_("Edit Bookmarks…"))
        connect_obj(act, "activate", self.__edit_bookmarks,
                           library.librarian, player)
        ag.add_action_with_accel(act, "<Primary>B")

        act = Action(name="Previous", label=_("Pre_vious"),
                     icon_name=Icons.MEDIA_SKIP_BACKWARD)
        act.connect("activate", self.__previous_song)
        ag.add_action_with_accel(act, "<Primary>comma")

        act = Action(name="PlayPause", label=_("_Play"),
                     icon_name=Icons.MEDIA_PLAYBACK_START)
        act.connect("activate", self.__play_pause)
        ag.add_action_with_accel(act, "<Primary>space")

        act = Action(name="Next", label=_("_Next"),
                     icon_name=Icons.MEDIA_SKIP_FORWARD)
        act.connect("activate", self.__next_song)
        ag.add_action_with_accel(act, "<Primary>period")

        act = Action(name="Stop", label=_("Stop"),
                     icon_name=Icons.MEDIA_PLAYBACK_STOP)
        act.connect("activate", self.__stop)
        ag.add_action(act)

        act = ToggleAction(name="StopAfter", label=_("Stop After This Song"))
        ag.add_action_with_accel(act, "<shift>space")

        # access point for the tray icon
        self.stop_after = act

        act = Action(name="Shortcuts", label=_("_Keyboard Shortcuts"))
        act.connect("activate", self.__keyboard_shortcuts)
        ag.add_action_with_accel(act, "<Primary>question")

        act = Action(name="About", label=_("_About"),
                     icon_name=Icons.HELP_ABOUT)
        act.connect("activate", self.__show_about)
        ag.add_action_with_accel(act, None)

        act = Action(name="OnlineHelp", label=_("Online Help"),
                     icon_name=Icons.HELP_BROWSER)

        def website_handler(*args):
            util.website(const.ONLINE_HELP)

        act.connect("activate", website_handler)
        ag.add_action_with_accel(act, "F1")

        act = Action(name="SearchHelp", label=_("Search Help"))

        def search_help_handler(*args):
            util.website(const.SEARCH_HELP)

        act.connect("activate", search_help_handler)
        ag.add_action_with_accel(act, None)

        act = Action(name="CheckUpdates", label=_("_Check for Updates…"),
                     icon_name=Icons.NETWORK_SERVER)

        def check_updates_handler(*args):
            d = UpdateDialog(self)
            d.run()
            d.destroy()

        act.connect("activate", check_updates_handler)
        ag.add_action_with_accel(act, None)

        act = Action(
            name="RefreshLibrary", label=_("_Scan Library"),
            icon_name=Icons.VIEW_REFRESH)
        act.connect("activate", self.__rebuild, False)
        ag.add_action_with_accel(act, "<Primary>R")

        current = config.get("memory", "browser")
        try:
            browsers.get(current)
        except ValueError:
            current = browsers.name(browsers.default)

        first_action = None
        for Kind in browsers.browsers:
            name = browsers.name(Kind)
            index = browsers.index(name)
            action_name = "View" + Kind.__name__
            act = RadioAction(name=action_name, label=Kind.accelerated_name,
                              value=index)
            act.join_group(first_action)
            first_action = first_action or act
            if name == current:
                act.set_active(True)
            ag.add_action_with_accel(act, "<Primary>%d" % ((index + 1) % 10,))
        assert first_action
        self._browser_action = first_action

        def action_callback(view_action, current_action):
            current = browsers.name(
                browsers.get(current_action.get_current_value()))
            self._select_browser(view_action, current, library, player)

        first_action.connect("changed", action_callback)

        for Kind in browsers.browsers:
            action = "Browser" + Kind.__name__
            label = Kind.accelerated_name
            name = browsers.name(Kind)
            index = browsers.index(name)
            act = Action(name=action, label=label)

            def browser_activate(action, browser_cls):
                LibraryBrowser.open(browser_cls, library, player)

            act.connect("activate", browser_activate, Kind)
            ag.add_action_with_accel(act,
                                     "<Primary><alt>%d" % ((index + 1) % 10,))

        ui = Gtk.UIManager()
        ui.insert_action_group(ag, -1)

        menustr = MENU % {
            "views": browser_menu_items(),
            "browsers": secondary_browser_menu_items(),
            "filters_menu": FilterMenu.MENU
        }
        ui.add_ui_from_string(menustr)
        self._filter_menu = FilterMenu(library, player, ui)

        # Cute. So. UIManager lets you attach tooltips, but when they're
        # for menu items, they just get ignored. So here I get to actually
        # attach them.
        ui.get_widget("/Menu/File/RefreshLibrary").set_tooltip_text(
            _("Check for changes in your library"))

        return ui

    def __show_about(self, *args):
        about = AboutDialog(self, app)
        about.run()
        about.destroy()

    def select_browser(self, browser_key, library, player):
        """Given a browser name (see browsers.get()) changes the current
        browser.

        Returns True if the passed browser ID is known and the change
        was initiated.
        """

        try:
            Browser = browsers.get(browser_key)
        except ValueError:
            return False

        action_name = "View%s" % Browser.__name__
        for action in self._browser_action.get_group():
            if action.get_name() == action_name:
                action.set_active(True)
                return True
        return False

    def _select_browser(self, activator, current, library, player,
                        restore=False):

        Browser = browsers.get(current)

        window = self.get_window()
        if window:
            window.set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))

        # Wait for the cursor to update before continuing
        while Gtk.events_pending():
            Gtk.main_iteration()

        config.set("memory", "browser", current)
        if self.browser:
            if not (self.browser.uses_main_library and
                    Browser.uses_main_library):
                self.songlist.clear()
            container = self.browser.__container
            self.browser.unpack(container, self.songpane)
            if self.browser.accelerators:
                self.remove_accel_group(self.browser.accelerators)
            container.destroy()
            self.browser.destroy()
        self.browser = Browser(library)
        self.browser.connect("songs-selected",
            self.__browser_cb, library, player)
        self.browser.connect("songs-activated", self.__browser_activate)
        if restore:
            self.browser.restore()
            self.browser.activate()
        self.browser.finalize(restore)
        if not restore:
            self.browser.unfilter()
        if self.browser.can_reorder:
            self.songlist.enable_drop()
        elif self.browser.dropped:
            self.songlist.enable_drop(False)
        else:
            self.songlist.disable_drop()
        if self.browser.accelerators:
            self.add_accel_group(self.browser.accelerators)
        self.set_sortability()
        container = self.browser.__container = self.browser.pack(self.songpane)

        # Reset the cursor when done loading the browser
        if window:
            GLib.idle_add(window.set_cursor, None)

        player.replaygain_profiles[1] = self.browser.replaygain_profiles
        player.reset_replaygain()
        self.__browserbox.add(container)
        container.show()
        self._filter_menu.set_browser(self.browser)
        self.__hide_headers()

    def set_sortability(self):
        self.songlist.sortable = not self.browser.can_reorder

    def __update_paused(self, player, paused):
        menu = self.ui.get_widget("/Menu/Control/PlayPause")
        image = menu.get_image()

        if paused:
            label, icon = _("_Play"), Icons.MEDIA_PLAYBACK_START
        else:
            label, icon = _("P_ause"), Icons.MEDIA_PLAYBACK_PAUSE

        menu.set_label(label)
        image.set_from_icon_name(icon, Gtk.IconSize.MENU)

    def __song_ended(self, player, song, stopped):
        # Check if the song should be removed, based on the
        # active filter of the current browser.
        active_filter = self.browser.active_filter
        if song and active_filter and not active_filter(song):
            iter_ = self.songlist.model.find(song)
            if iter_:
                self.songlist.remove_iters([iter_])

        if self.stop_after.get_active():
            player.paused = True
            self.stop_after.set_active(False)

    def __song_changed(self, library, songs, player):
        if player.info in songs:
            self.__update_title(player)

    def __update_title(self, player):
        song = player.info
        title = "Quod Libet"
        if song:
            tag = config.gettext("settings", "window_title_pattern")
            if tag:
                title = song.comma(tag) + " - " + title
        self.set_title(title)

    def __song_started(self, player, song):
        self.__update_title(player)

        for wid in ["Control/Next", "Control/StopAfter",
                    "Song/EditTags", "Song/Information",
                    "Song/EditBookmarks", "Song/Jump"]:
            self.ui.get_widget("/Menu/" + wid).set_sensitive(bool(song))

        # don't jump on stream changes (player.info != player.song)
        main_should_jump = (song and player.song is song and
                       not self.songlist._activated and
                       config.getboolean("settings", "jump") and
                       self.songlist.sourced)
        queue_should_jump = (song and player.song is song and
                        not self.qexpander.queue._activated and
                        config.getboolean("settings", "jump") and
                        self.qexpander.queue.sourced and
                        config.getboolean("memory", "queue_keep_songs"))
        if main_should_jump:
            self.__jump_to_current(False, self.songlist)
        elif queue_should_jump:
            self.__jump_to_current(False, self.qexpander.queue)

    def __play_pause(self, *args):
        app.player.playpause()

    def __stop(self, *args):
        app.player.stop()

    def __jump_to_current(self, explicit, songlist=None, force_scroll=False):
        """Select/scroll to the current playing song in the playlist.
        If it can't be found tell the browser to properly fill the playlist
        with an appropriate selection containing the song.

        explicit means that the jump request comes from the user and not
        from an event like song-started.

        songlist is the songlist to be jumped within. Usually the main song
        list or the queue. If None, the currently sourced songlist will be
        used.

        force_scroll will ask the browser to refill the playlist in any case.
        """

        def idle_jump_to(song, select):
            ok = songlist.jump_to_song(song, select=select)
            if ok:
                songlist.grab_focus()
            return False

        if not songlist:
            if (config.getboolean("memory", "queue_keep_songs")
                    and self.qexpander.queue.sourced):
                songlist = self.qexpander.queue
            else:
                songlist = self.songlist

        if app.player is None:
            return

        song = app.player.song

        # We are not playing a song
        if song is None:
            return

        if not force_scroll:
            ok = songlist.jump_to_song(song, select=explicit)
        else:
            assert explicit
            ok = False

        if ok:
            songlist.grab_focus()
        elif explicit:
            # if we can't find it and the user requested it, try harder
            self.browser.scroll(song)
            # We need to wait until the browser has finished
            # scrolling/filling and the songlist is ready.
            # Not perfect, but works for now.
            GLib.idle_add(
                idle_jump_to, song, explicit, priority=GLib.PRIORITY_LOW)

    def __next_song(self, *args):
        app.player.next()

    def __previous_song(self, *args):
        app.player.previous()

    def __rebuild(self, activator, force):
        scan_library(self.__library, force)

    # Set up the preferences window.
    def __preferences(self, activator):
        window = PreferencesWindow(self)
        window.show()

    def __plugins(self, activator):
        window = PluginWindow(self)
        window.show()

    def open_location(self, action):
        name = GetStringDialog(self, _("Add a Location"),
            _("Enter the location of an audio file:"),
            button_label=_("_Add"), button_icon=Icons.LIST_ADD).run()
        if name:
            if not uri_is_valid(name):
                ErrorMessage(
                    self, _("Unable to add location"),
                    _("%s is not a valid location.") % util.bold(name)).run()
            elif not app.player.can_play_uri(name):
                ErrorMessage(
                    self, _("Unable to add location"),
                    _("%s uses an unsupported protocol.") % (util.bold(name)),
                    escape_desc=False).run()
            else:
                if name not in self.__library:
                    self.__library.add([RemoteFile(name)])

    def open_chooser(self, action):
        if action.get_name() == "AddFolders":
            fns = choose_folders(self, _("Add Music"), _("_Add Folders"))
            if fns:
                # scan them
                copool.add(self.__library.scan, fns, cofuncid="library",
                           funcid="library")
        else:
            patterns = ["*" + path2fsn(k) for k in formats.loaders.keys()]
            choose_filter = create_chooser_filter(_("Music Files"), patterns)
            fns = choose_files(
                self, _("Add Music"), _("_Add Files"), choose_filter)
            if fns:
                for filename in fns:
                    self.__library.add_filename(filename)

    def __songs_popup_menu(self, songlist):
        path, col = songlist.get_cursor()
        header = col.header_name
        menu = self.songlist.menu(header, self.browser, self.__library)
        if menu is not None:
            return self.songlist.popup_menu(menu, 0,
                    Gtk.get_current_event_time())

    def __current_song_prop(self, *args):
        song = app.player.song
        if song:
            librarian = self.__library.librarian
            window = SongProperties(librarian, [song], parent=self)
            window.show()

    def __current_song_info(self, *args):
        song = app.player.song
        if song:
            librarian = self.__library.librarian
            window = Information(librarian, [song], self)
            window.show()

    def __browser_activate(self, browser):
        app.player._reset()

    def __browser_cb(self, browser, songs, sorted, library, player):
        if browser.background:
            bg = background_filter()
            if bg:
                songs = list(filter(bg, songs))
        self.songlist.set_songs(songs, sorted)

        # After the first time the browser activates, which should always
        # happen if we start up and restore, restore the playing song.
        # Because the browser has send us songs we can be sure it has
        # registered all its libraries.
        if self.__first_browser_set:
            self.__first_browser_set = False

            song = library.librarian.get(config.get("memory", "song"))
            seek_pos = config.getfloat("memory", "seek", 0)
            config.set("memory", "seek", 0)
            if song is not None:
                player.setup(self.playlist, song, seek_pos, False)

            if self.__restore_cb:
                self.__restore_cb()
                self.__restore_cb = None

    def __hide_headers(self, activator=None):
        for column in self.songlist.get_columns():
            if self.browser.headers is None:
                column.set_visible(True)
            else:
                for tag in util.tagsplit(column.header_name):
                    if tag in self.browser.headers:
                        column.set_visible(True)
                        break
                else:
                    column.set_visible(False)

    def __cols_changed(self, songlist):
        headers = [col.header_name for col in songlist.get_columns()]
        try:
            headers.remove("~current")
        except ValueError:
            pass
        if len(headers) == len(get_columns()):
            # Not an addition or removal (handled separately)
            set_columns(headers)
            SongList.headers = headers

    def __make_query(self, query):
        if self.browser.can_filter_text():
            self.browser.filter_text(query.encode("utf-8"))
            self.browser.activate()

    def __set_totals(self, info, songs):
        length = sum(song.get("~#length", 0) for song in songs)
        t = self.browser.status_text(count=len(songs),
                                     time=util.format_time_preferred(length))
        self.statusbar.set_default_text(t)


class ConfirmEnqueue(qltk.Message):
    def __init__(self, parent, count):
        title = ngettext("Are you sure you want to enqueue %d song?",
                         "Are you sure you want to enqueue %d songs?",
                         count) % count
        description = ""

        super().__init__(
            Gtk.MessageType.WARNING, parent, title, description,
            Gtk.ButtonsType.NONE)

        self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        self.add_icon_button(_("_Enqueue"), Icons.LIST_ADD,
                             Gtk.ResponseType.YES)
