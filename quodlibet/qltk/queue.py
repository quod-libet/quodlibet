# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#           2016-2021 Nick Boultbee
#                2017 Fredrik Strupe
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import time
from typing import Optional

from gi.repository import Gtk, Gdk, Pango, GLib

from quodlibet.library.base import Library
from quodlibet.player._base import BasePlayer
from senf import bytes2fsn, fsn2bytes

import quodlibet
from quodlibet import ngettext, _, print_e, print_w, print_d
from quodlibet import config
from quodlibet import qltk
from quodlibet import app

from quodlibet.util import (connect_destroy, connect_after_destroy,
                            format_time_preferred, print_exc, DeferredSignal)
from quodlibet.qltk import Icons, gtk_version, add_css
from quodlibet.qltk.ccb import ConfigCheckMenuItem
from quodlibet.qltk.songlist import SongList, DND_QL, DND_URI_LIST
from quodlibet.qltk.songsmenu import SongsMenu
from quodlibet.qltk.menubutton import SmallMenuButton
from quodlibet.qltk.songmodel import PlaylistModel
from quodlibet.qltk.playorder import OrderInOrder, OrderShuffle
from quodlibet.qltk.x import ScrolledWindow, SymbolicIconImage, \
    SmallImageButton, SmallImageToggleButton, MenuItem, RadioMenuItem
from quodlibet.qltk.songlistcolumns import CurrentColumn

QUEUE = os.path.join(quodlibet.get_runtime_dir(), "queue")


class PlaybackStatusIcon(Gtk.Box):
    """A widget showing a play/pause/stop symbolic icon"""

    def __init__(self):
        super().__init__()
        self._icons = {}

    def _set(self, name):
        if name not in self._icons:
            image = SymbolicIconImage(name, Gtk.IconSize.MENU)
            self._icons[name] = image
            image.show()
        else:
            image = self._icons[name]

        children = self.get_children()
        if children:
            self.remove(children[0])
        self.add(image)

    def play(self):
        self._set("media-playback-start")

    def stop(self):
        self._set("media-playback-stop")

    def pause(self):
        self._set("media-playback-pause")


class ExpandBoxHack(Gtk.HBox):

    def do_get_preferred_width(self):
        # Workaround for https://bugzilla.gnome.org/show_bug.cgi?id=765602
        # set_label_fill() no longer works since 3.20. Fake a natural size
        # which is larger than the expander can be to force the parent to
        # allocate to us the whole space.
        min_, nat = Gtk.HBox.do_get_preferred_width(self)
        if gtk_version > (3, 19):
            # if we get too large gtk calcs will overflow..
            nat = max(nat, 2 ** 16)
        return (min_, nat)


class QueueExpander(Gtk.Expander):

    def __init__(self, library, player):
        super().__init__(spacing=3)
        sw = ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)
        save_interval_secs = config.getint("autosave", "queue_interval")
        self.queue = PlayQueue(library, player, save_interval_secs)
        self.queue.props.expand = True
        sw.add(self.queue)

        add_css(self, ".ql-expanded title { margin-bottom: 5px; }")

        outer = ExpandBoxHack()

        left = Gtk.HBox(spacing=12)

        hb2 = Gtk.HBox(spacing=3)
        state_icon = PlaybackStatusIcon()
        state_icon.stop()
        state_icon.show()
        hb2.pack_start(state_icon, True, True, 0)
        name_label = Gtk.Label(label=_("_Queue"), use_underline=True)
        name_label.set_size_request(-1, 24)
        hb2.pack_start(name_label, True, True, 0)
        left.pack_start(hb2, False, True, 0)

        menu = Gtk.Menu()

        self.count_label = count_label = Gtk.Label()
        self.count_label.set_property("ellipsize", Pango.EllipsizeMode.END)
        self.count_label.set_width_chars(10)
        self.count_label.get_style_context().add_class("dim-label")
        left.pack_start(count_label, False, True, 0)

        outer.pack_start(left, True, True, 0)

        self.set_label_fill(True)

        clear_item = SmallImageButton(
            image=SymbolicIconImage(Icons.USER_TRASH,
                                    Gtk.IconSize.MENU),
            relief=Gtk.ReliefStyle.NONE,
            tooltip_text=_("Clear Queue"))
        clear_item.connect("clicked", self.__clear_queue)
        outer.pack_start(clear_item, False, False, 3)

        toggle = SmallImageToggleButton(
            image=SymbolicIconImage(Icons.SYSTEM_LOCK_SCREEN,
                                    Gtk.IconSize.MENU),
            relief=Gtk.ReliefStyle.NONE,
            tooltip_text=_(
                "Disable queue - the queue will be ignored when playing"))
        disabled = config.getboolean("memory", "queue_disable", False)
        toggle.props.active = disabled
        self.__queue_disable(disabled)
        toggle.connect('toggled',
                       lambda b: self.__queue_disable(b.props.active))
        outer.pack_start(toggle, False, False, 3)

        mode_menu = Gtk.Menu()

        norm_mode_item = RadioMenuItem(
            label=_("Ephemeral"),
            tooltip_text=_("Remove songs from the queue after playing them"),
            group=None)
        mode_menu.append(norm_mode_item)
        norm_mode_item.set_active(True)
        norm_mode_item.connect("toggled", lambda _: self.__keep_songs_enable(False))

        keep_mode_item = RadioMenuItem(
            label=_("Persistent"),
            tooltip_text=_("Keep songs in the queue after playing them"),
            group=norm_mode_item)
        mode_menu.append(keep_mode_item)
        keep_mode_item.connect("toggled",
                               lambda b: self.__keep_songs_enable(True))
        keep_mode_item.set_active(
                config.getboolean("memory", "queue_keep_songs", False))

        mode_item = MenuItem(_("Mode"), Icons.SYSTEM_RUN)
        mode_item.set_submenu(mode_menu)
        menu.append(mode_item)

        rand_checkbox = ConfigCheckMenuItem(
                _("_Random"), "memory", "shufflequeue", populate=True)
        rand_checkbox.connect('toggled', self.__queue_shuffle)
        self.set_shuffled(rand_checkbox.get_active())
        menu.append(rand_checkbox)

        stop_checkbox = ConfigCheckMenuItem(
            _("Stop at End"), "memory", "queue_stop_at_end",
            populate=True)
        menu.append(stop_checkbox)

        button = SmallMenuButton(
            SymbolicIconImage(Icons.EMBLEM_SYSTEM, Gtk.IconSize.MENU),
            arrow=True)
        button.set_relief(Gtk.ReliefStyle.NORMAL)
        button.show_all()
        button.hide()
        button.set_no_show_all(True)
        menu.show_all()
        button.set_menu(menu)

        outer.pack_start(button, False, False, 3)

        close_button = SmallImageButton(
            image=SymbolicIconImage("window-close", Gtk.IconSize.MENU),
            relief=Gtk.ReliefStyle.NONE)

        close_button.connect("clicked", lambda *x: self.hide())

        outer.pack_start(close_button, False, False, 6)

        self.set_label_widget(outer)
        self.add(sw)
        self.connect('notify::expanded', self.__expand, button)
        self.connect('notify::expanded', self.__expand, button)

        targets = [
            ("text/x-quodlibet-songs", Gtk.TargetFlags.SAME_APP, DND_QL),
            ("text/uri-list", 0, DND_URI_LIST)
        ]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]

        self.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        self.connect('drag-motion', self.__motion)
        self.connect('drag-data-received', self.__drag_data_received)

        self.queue.model.connect_after('row-inserted',
                                       DeferredSignal(self.__check_expand), count_label)
        self.queue.model.connect_after('row-deleted',
                                       DeferredSignal(self.__update_count), count_label)

        self.__update_count(self.model, None, count_label)

        connect_destroy(
            player, 'song-started', self.__update_state_icon, state_icon)
        connect_destroy(
            player, 'paused', self.__update_state_icon_pause,
            state_icon, True)
        connect_destroy(
            player, 'unpaused', self.__update_state_icon_pause,
            state_icon, False)

        connect_destroy(
            player, 'song-started', self.__song_started, self.queue.model)
        connect_destroy(
            player, 'song-ended', self.__update_queue_stop, self.queue.model)

        # to make the children clickable if mapped
        # ....no idea why, but works
        def hack(expander):
            label = expander.get_label_widget()
            if label:
                label.unmap()
                label.map()
        self.connect("map", hack)

        self.set_expanded(config.getboolean("memory", "queue_expanded"))
        self.notify("expanded")

        for child in self.get_children():
            child.show_all()

    @property
    def model(self):
        return self.queue.model

    def refresh(self):
        self.__update_count(self.model, None, self.count_label)

    def __update_state_icon(self, player, song, state_icon):
        if self.model.sourced:
            state_icon.play()
        else:
            state_icon.stop()

    def __update_state_icon_pause(self, player, state_icon, paused):
        if self.model.sourced:
            if paused:
                state_icon.pause()
            else:
                state_icon.play()
        else:
            state_icon.stop()

    def __clear_queue(self, activator):
        self.model.clear()

    def __keep_songs_enable(self, enabled):
        config.set("memory", "queue_keep_songs", enabled)
        if enabled:
            self.queue.set_first_column_type(CurrentColumn)
        else:
            for col in self.queue.get_columns():
                # Remove the CurrentColum if it exists
                if isinstance(col, CurrentColumn):
                    self.queue.set_first_column_type(None)
                    break

    def __motion(self, wid, context, x, y, time):
        Gdk.drag_status(context, Gdk.DragAction.COPY, time)
        return True

    def __update_count(self, model, path, lab):
        if len(model) == 0:
            text = ""
        else:
            time = sum([row[0].get("~#length", 0) for row in model])
            text = ngettext("%(count)d song (%(time)s)",
                            "%(count)d songs (%(time)s)",
                            len(model)) % {
                "count": len(model), "time": format_time_preferred(time)}
        lab.set_text(text)

    def __check_expand(self, model, path, iter, lab):
        self.__update_count(model, path, lab)
        self.show()

    def __drag_data_received(self, expander, *args):
        self.queue.emit('drag-data-received', *args)

    def __queue_shuffle(self, button):
        self.set_shuffled(button.get_active())

    def set_shuffled(self, is_shuffled):
        self.queue.model.order = (OrderShuffle() if is_shuffled
                                  else OrderInOrder())

    def __update_queue_stop(self, player, song, stopped, model):
        enabled = config.getboolean("memory", "queue_stop_at_end", False)
        songs_left = len(model.get())
        if (enabled
                and not stopped
                and songs_left == 0
                and self._queue_sourced):
            app.player.stop()

    def __song_started(self, player, song, model):
        self._queue_sourced = model.sourced

    def __expand(self, widget, prop, menu_button):
        expanded = self.get_expanded()

        style_context = self.get_style_context()
        if expanded:
            style_context.add_class("ql-expanded")
        else:
            style_context.remove_class("ql-expanded")

        menu_button.set_property('visible', expanded)
        config.set("memory", "queue_expanded", str(expanded))

    def __queue_disable(self, disabled):
        config.set("memory", "queue_disable", disabled)
        self.queue.set_sensitive(not disabled)


class QueueModel(PlaylistModel):
    """Own class for debugging"""


class PlayQueue(SongList):

    sortable = False
    _activated = False
    _MAX_PENDING = 5
    """Maximum number of queue items to leave unpersisted (batching)"""

    def __init__(self, library: Library, player: BasePlayer,
                 autosave_interval_secs: Optional[int] = None):
        super().__init__(library, player, model_cls=QueueModel)
        self._updated_time = time.time()
        self.autosave_interval = autosave_interval_secs
        self._pending = 0
        keep_song = config.getboolean("memory", "queue_keep_songs", False)
        if keep_song:
            self.set_first_column_type(CurrentColumn)
        self.set_size_request(-1, 120)
        self.connect('row-activated', self.__go_to, player)

        def reset_activated(*args):
            self._activated = False
        connect_after_destroy(player, 'song-started', reset_activated)

        self.connect('popup-menu', self.__popup, library)
        self.enable_drop()

        def write(*args, **kwargs):
            self._write(self, self.model)
            return True

        self.connect('destroy', self.__destroy)
        if self.autosave_interval:
            self._tid = GLib.timeout_add(self.autosave_interval * 1000, write)
        else:
            self._tid = None
        connect_after_destroy(self.model, "row-inserted", write)
        connect_after_destroy(self.model, "row-deleted", write)
        self.__fill(library)

        self.connect('key-press-event', self.__delete_key_pressed)

    def __destroy(self, widget):
        self._write(widget, self.model, force=True)
        if self._tid:
            GLib.source_remove(self._tid)
            self._tid = None
            print_d("Stopped autosave")

    def __delete_key_pressed(self, widget, event):
        if qltk.is_accel(event, "Delete"):
            self.__remove()
            return True
        return False

    def __go_to(self, view, path, column, player):
        self._activated = True
        if player.go_to(self.model.get_iter(path), explicit=True,
                        source=self.model):
            player.paused = False

    def __fill(self, library):
        try:
            with open(QUEUE, "rb") as f:
                lines = f.readlines()
        except EnvironmentError:
            return

        filenames = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                filename = bytes2fsn(line, "utf-8")
            except ValueError:
                print_exc()
                continue
            filenames.append(filename)

        if library.librarian:
            library = library.librarian
        songs = filter(None, map(library.get, filenames))
        for song in songs:
            self.model.append([song])

    def _write(self, _widget: Gtk.Widget, model: QueueModel, force=False):
        diff = time.time() - self._updated_time
        if not self._should_write(force, diff):
            self._pending += 1
            return
        print_d(f"Saving play queue after {diff:.1f}s")
        filenames = [row[0]["~filename"] for row in model]
        try:
            with open(QUEUE, "wb") as f:
                for filename in filenames:
                    try:
                        line = fsn2bytes(filename, "utf-8")
                    except ValueError as e:
                        print_w(f"Ignoring queue save error ({e})")
                        continue
                    f.write(line + b"\n")
        except EnvironmentError as e:
            print_e(f"Error saving queue ({e})")
        self._updated_time = time.time()
        self._pending = 0

    def _should_write(self, force: bool, diff: float):
        """Whether it's appropriate to write (flush)
        Either it's disabled by config, in which case we use batching,
        or we respect the last write time and the delta
        """
        if force:
            return True
        if self.autosave_interval:
            # Generally only save if there are *known* pending updates,
            # but these don´t catch everything, so save regardless every now and again.
            return ((diff > self.autosave_interval and self._pending)
                    or diff > self.autosave_interval * 5)
        return self._pending >= self._MAX_PENDING

    def __popup(self, widget, library):
        songs = self.get_selected_songs()
        if not songs:
            return

        menu = SongsMenu(
            library, songs, queue=False, remove=False, delete=False, ratings=False)
        menu.preseparate()
        remove = MenuItem(_("_Remove"), Icons.LIST_REMOVE)
        qltk.add_fake_accel(remove, "Delete")
        remove.connect('activate', self.__remove)
        menu.prepend(remove)
        menu.show_all()
        return self.popup_menu(menu, 0, Gtk.get_current_event_time())

    def __remove(self, *args):
        self.remove_selection()
