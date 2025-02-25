# Copyright 2005 Joe Wreschnig, Michael Urman
#           2013 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from gi.repository import Gtk, Gdk, GLib

from quodlibet import _
from quodlibet import config
from quodlibet import qltk
from quodlibet import util
from quodlibet.qltk import get_top_parent
from quodlibet.qltk.tracker import TimeTracker
from quodlibet.qltk import bookmarks
from quodlibet.qltk.x import Align
from quodlibet.qltk import Icons
from quodlibet.qltk.ccb import ConfigCheckMenuItem
from quodlibet.qltk.util import (
    window_grab_and_map,
    window_ungrab_and_unmap,
    position_window_beside_widget,
)
from quodlibet.qltk.x import SeparatorMenuItem
from quodlibet.util import connect_obj, connect_destroy


class TimeLabel(Gtk.Label):
    """A label for displaying the running time

    It tries to minimize size changes due to unequal character widths
    with the same number of characters.

    e.g. a time display -> 04:20
    """

    def __init__(self, time_=0):
        Gtk.Label.__init__(self)
        self.__widths = {}  # num-chars -> (max-min-width, max-natural-width)
        self._disabled = False
        self.set_time(time_)

    def do_get_preferred_width(self):
        widths = Gtk.Label.do_get_preferred_width(self)

        # If for same number of characters, the needed width was larger,
        # use that instead of the current one
        num_chars = len(self.get_text())
        max_widths = self.__widths.get(num_chars, widths)
        widths = max(widths[0], max_widths[0]), max(widths[1], max_widths[1])
        self.__widths[num_chars] = widths
        return widths

    def set_time(self, time_):
        """Set the time in seconds"""

        self._last_time = time_
        if self._disabled:
            return
        self.set_text(util.format_time_display(time_))

    def set_disabled(self, disabled):
        """Disable the time display temporarily, means there is no meaningful
        time to show. Re-enabling will show the previous time value
        """

        self._disabled = disabled
        if disabled:
            self.set_text("‒\u2236‒‒")
        else:
            self.set_time(self._last_time)


class HSlider(Gtk.Button):
    def __init__(self, child=None):
        super().__init__()
        if child:
            self.add(child)
        self.connect("clicked", self.__clicked)

        self._disable_slider = False
        self.__grabbed = []

        window = self.__window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.__adj = Gtk.Adjustment.new(0, 0, 0, 3, 15, 0)

        frame = Gtk.Frame()
        frame.set_border_width(0)
        frame.set_shadow_type(Gtk.ShadowType.OUT)

        self.add_events(Gdk.EventMask.SCROLL_MASK)

        hscale = Gtk.Scale(adjustment=self.__adj)
        hscale.set_orientation(Gtk.Orientation.HORIZONTAL)
        window.connect("button-press-event", self.__button)
        window.connect("key-press-event", self.__key)
        hscale.set_draw_value(False)
        self.scale = hscale
        window.add(frame)
        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._box.add(hscale)
        frame.add(self._box)
        self.connect("scroll-event", self.__scroll, hscale)

        self.connect("destroy", self.__destroy)

        # forward scroll event to the button
        def foward_scroll(scale, event):
            self.emit("scroll-event", event.copy())

        window.connect("scroll-event", foward_scroll)

        # ignore scroll events on the scale, the window handles it instead
        self.scale.connect("scroll-event", lambda *x: True)

        # handle all unhandled button events on the scale
        # so only events not on the scale hide the window
        def handle_all(scale, event):
            return True

        self.scale.connect_after("button-press-event", handle_all)
        self.scale.connect_after("button-release-event", handle_all)

        # forward release event to the scale
        def foward_release(scale, event):
            self.scale.emit("button-release-event", event.copy())

        window.connect("button-release-event", foward_release)

        self.set_slider_length(200)

        if child:
            self.get_child().show_all()

    def __destroy(self, *args):
        self.__window.destroy()
        self.__window = None

    def set_slider_disabled(self, disable):
        """Hide the slider and don't allow showing it again until it is
        enabled again
        """

        self._disable_slider = disable
        if disable:
            self.__popup_hide()

    def set_slider_length(self, length):
        self.scale.set_size_request(length, -1)

        # force a window resize..
        self.__window.resize(1, 1)

    def set_slider_widget(self, widget):
        self._box.pack_start(Align(widget, border=6, left=-3), False, True, 0)

    def __clicked(self, button):
        if self.__window.get_property("visible"):
            return

        if self._disable_slider:
            return

        if self.__grabbed:
            self.__popup_hide()

        window = self.__window
        frame = window.get_child()
        frame.show_all()

        window.set_transient_for(get_top_parent(self))
        # this type hint tells the wayland backend to create a popup
        window.set_type_hint(Gdk.WindowTypeHint.DROPDOWN_MENU)

        position_window_beside_widget(window, self)

        self.__grabbed = window_grab_and_map(
            window,
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK,
        )

    def __scroll(self, widget, event, hscale):
        adj = self.__adj
        v = hscale.get_value()
        direction = event.direction
        if direction in [Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT]:
            v -= adj.props.step_increment
        elif direction in [Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT]:
            v += adj.props.step_increment
        else:
            # newer Gdk.ScrollDirection.SMOOTH
            return
        v = min(adj.props.upper, max(adj.props.lower, v))
        hscale.set_value(v)

    def __button(self, widget, ev):
        self.__popup_hide()

    def __key(self, hscale, ev):
        if ev.string in ["\n", "\r", " ", "\x1b"]:  # enter, space, escape
            self.__popup_hide()

    def __popup_hide(self):
        window_ungrab_and_unmap(self.__window, self.__grabbed)
        del self.__grabbed[:]


class SeekButton(HSlider):
    __lock = False
    __sig = None
    __seekable = True

    def __init__(self, player, library):
        hbox = Gtk.HBox(spacing=3)
        l = TimeLabel()
        self._time_label = l
        hbox.pack_start(l, True, True, 0)
        arrow = Gtk.Arrow.new(Gtk.ArrowType.RIGHT, Gtk.ShadowType.NONE)
        hbox.pack_start(arrow, False, True, 0)
        super().__init__(hbox)

        self._slider_label = TimeLabel()
        self.set_slider_widget(self._slider_label)

        self._on_seekable_changed(player)
        connect_destroy(player, "notify::seekable", self._on_seekable_changed)

        self.scale.connect("button-press-event", self.__seek_lock)
        self.scale.connect("button-release-event", self.__seek_unlock, player)
        self.scale.connect("key-press-event", self.__seek_lock)
        self.scale.connect("key-release-event", self.__seek_unlock, player)
        self.connect("scroll-event", self.__scroll, player)
        self.scale.connect("value-changed", self.__update_time, l)

        m = Gtk.Menu()
        c = ConfigCheckMenuItem(_("Display remaining time"), "player", "time_remaining")
        c.set_active(config.getboolean("player", "time_remaining"))
        connect_obj(c, "toggled", self.scale.emit, "value-changed")
        self.__remaining = c
        m.append(c)
        m.append(SeparatorMenuItem())
        i = qltk.MenuItem(_("_Edit Bookmarks…"), Icons.EDIT)

        def edit_bookmarks_cb(menu_item):
            window = bookmarks.EditBookmarks(self, library, player)
            window.show()

        i.connect("activate", edit_bookmarks_cb)
        m.append(i)
        m.show_all()

        connect_obj(self, "button-press-event", self.__check_menu, m, player, c)
        connect_obj(self, "popup-menu", self.__popup_menu, m, player)

        timer = TimeTracker(player)
        connect_obj(timer, "tick", self.__check_time, player)

        connect_destroy(library, "changed", self.__songs_changed, player, m)

        connect_destroy(player, "song-started", self.__song_started, m)
        connect_destroy(player, "seek", self.__seeked)

    def _on_seekable_changed(self, player, *args):
        self._time_label.set_disabled(not player.seekable)
        self.set_slider_disabled(not player.seekable)

    def __check_menu(self, menu, event, player, remaining_item):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return

        if event.triggers_context_menu():
            return self.__popup_menu(menu, player, event)
        elif event.button == Gdk.BUTTON_MIDDLE:
            remaining_item.set_active(not remaining_item.get_active())
            return True

    def __popup_menu(self, menu, player, event=None):
        for child in menu.get_children()[2:-1]:
            menu.remove(child)
            child.destroy()

        try:
            marks = player.song.bookmarks
        except AttributeError:
            # song is None
            pass
        else:
            items = qltk.bookmarks.MenuItems(marks, player, self.__seekable)
            items.reverse()
            for i in items:
                menu.insert(i, 2)

        if event:
            qltk.popup_menu_at_widget(menu, self, 3, event.time)
        else:
            time = Gtk.get_current_event_time()
            qltk.popup_menu_under_widget(menu, self, 3, time)
        return True

    def __seeked(self, player, song, ms):
        self.scale.set_value(ms / 1000.0)

    def __scroll(self, widget, event, player):
        self.__lock = True
        if self.__sig is not None:
            GLib.source_remove(self.__sig)
        self.__sig = GLib.timeout_add(100, self.__scroll_timeout, player)

    def __scroll_timeout(self, player):
        self.__lock = False
        if self.__seekable:
            player.seek(self.scale.get_value() * 1000)
        self.__sig = None

    def __seek_lock(self, scale, event):
        self.__lock = True

    def __seek_unlock(self, scale, event, player):
        self.__lock = False
        if self.__seekable:
            player.seek(self.scale.get_value() * 1000)

    def __check_time(self, player):
        # When the song is paused GStreamer returns < 1 for position
        # queries, so if it's paused just ignore it.
        if not (player.paused or self.__lock):
            position = player.get_position() / 1000.0
            if (
                not self.__seekable
                and position > self.scale.get_adjustment().get_upper()
            ):
                self.scale.set_range(0, position)
            self.scale.set_value(position)
        return True

    def __update_time(self, scale, timer):
        value = scale.get_value()
        max_ = scale.get_adjustment().get_upper()
        remaining = value - max_
        if self.__remaining.get_active():
            remaining, value = value, remaining
        timer.set_time(value)
        self._slider_label.set_time(remaining)

    def __songs_changed(self, library, songs, player, menu):
        song = player.song
        if song in songs:
            self.__update_slider(song, menu)

    def __song_started(self, player, song, menu):
        self.scale.set_value(0)
        self.__update_slider(song, menu)

    def __update_slider(self, song, menu):
        if song and song("~#length") > 0:
            self.scale.set_range(0, song("~#length"))
            slider_width = int(song("~#length") / 1.5) + 80
            self.__seekable = True
        else:
            self.scale.set_range(0, 1)
            slider_width = 0
            self.__seekable = False

        slider_width = min(max(slider_width, 170), 400)
        self.set_slider_length(slider_width)

        for child in menu.get_children()[2:-1]:
            menu.remove(child)
            child.destroy()
        menu.get_children()[-1].set_sensitive(self.__seekable)
        self.scale.emit("value-changed")
