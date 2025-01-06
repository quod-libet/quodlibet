# Copyright 2018 Phoenix Dailey
#           2020 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import random

from gi.repository import Gtk, GLib

from quodlibet import _
from quodlibet import app
from quodlibet.commands import registry as cmd_registry
from quodlibet.order import OrderInOrder
from quodlibet.order import OrderRemembered
from quodlibet.order.reorder import Reorder
from quodlibet.plugins import PluginConfig
from quodlibet.plugins.playorder import ShufflePlugin
from quodlibet.qltk import Icons
from quodlibet.qltk.notif import Task

from itertools import groupby

pconfig = PluginConfig("shufflebygrouping")
pconfig.defaults.set("grouping", "~grouping~album~albumartist")
pconfig.defaults.set("grouping_filter", "grouping")
pconfig.defaults.set("delay", 0)


class ShuffleByGrouping(ShufflePlugin, OrderRemembered):
    PLUGIN_ID = "Shuffle by Grouping"
    PLUGIN_NAME = _("Shuffle by Grouping")
    PLUGIN_DESC = _("Adds a shuffle mode that groups songs with a common tag "
                    "– similar to album shuffles.\n\n"
                    "This is useful for shuffling multi-movement classical "
                    "pieces, making sure all movements play in order "
                    "before shuffling to the next piece.")
    PLUGIN_ICON = Icons.MEDIA_PLAYLIST_SHUFFLE
    display_name = _("Shuffle by grouping")
    accelerated_name = _("Shuffle by _grouping")
    priority = Reorder.priority

    def __init__(self):
        super().__init__()
        self._order = None
        self._register_commands()

    def next(self, playlist, iter):
        return self._next(playlist, iter)

    def next_explicit(self, playlist, iter):
        return self._next(playlist, iter, delay_on=False)

    def previous(self, playlist, iter):
        return OrderRemembered.previous(self, playlist, iter)

    def reset(self, playlist):
        super().reset(playlist)
        self._order = None

    def _next(self, playlist, current_iter, delay_on=True):
        delay = pconfig.getint("delay")

        # Keep track of played songs
        OrderRemembered.next(self, playlist, current_iter)

        current_song = playlist.get_value(current_iter) if current_iter else None

        song_grouping = self._grouping_function(playlist)

        if not self._order or not self._order.get(current_song):
            iters = (row.iter for row in playlist)
            groups = [list(group) for _, group in groupby(iters, song_grouping)]

            random.shuffle(groups)

            # rotate to put group containing current song to first position
            if current_iter:
                group_i = None

                for i, group in enumerate(groups):
                    if any(playlist.get_value(iter) == current_song for iter in group):
                        group_i = i
                        break

                if group_i is not None:
                    groups = groups[group_i:] + groups[:group_i]

            shuffled = [iter for group in groups for iter in group]
            self._order = dict(
                zip(
                    [None] + [playlist.get_value(iter) for iter in shuffled],
                    shuffled + [None]
                )
            )

        next_iter = self._order.get(current_song)

        if delay_on and current_iter and next_iter and song_grouping(current_iter) != song_grouping(next_iter):
            self._resume_after_delay(delay)

        return next_iter

    @staticmethod
    def _resume_after_delay(delay, refresh_rate=20):
        if delay <= 0:
            return
        app.player.paused = True
        delay_timer = GLib.timeout_add(1000 * delay, app.player.play)
        task = Task(_("Shuffle by Grouping"),
                    _("Waiting to start new group…"),
                    stop=lambda: GLib.source_remove(delay_timer))

        def countdown():
            for i in range(int(refresh_rate * delay)):
                task.update(i / (refresh_rate * delay))
                yield True
            task.finish()
            yield False
        GLib.timeout_add(1000 / refresh_rate, next, countdown())

    @staticmethod
    def _grouping_function(playlist):
        grouping = str(pconfig.gettext("grouping")).strip()
        grouping_filter = str(pconfig.gettext("grouping_filter")).strip()

        def f(iter):
            song = playlist.get_value(iter)
            if not grouping_filter or bool(song(grouping_filter).strip()):
                return song(grouping)
            else:
                return song

        return f

    def _register_commands(self):
        @cmd_registry.register("next-album")
        def _next_album(app):
            playlist = app.window.playlist.pl

            song_grouping = self._grouping_function(playlist)

            def playlist_index(iter):
                return playlist.get_path(iter).get_indices()[0]

            iter = playlist.current_iter
            if iter:
                grouping = song_grouping(iter)
                i = playlist_index(iter)
                while True:
                    playlist.next()
                    iter = playlist.current_iter
                    if song_grouping(iter) != grouping or playlist_index(iter) <= i:
                        break
                app.player.go_to(iter)
                app.player.play()
            else:
                app.player.next()

        @cmd_registry.register("previous-album")
        def _previous_album(app):
            playlist = app.window.playlist.pl

            if not playlist.current_iter:
                return

            song_grouping = self._grouping_function(playlist)

            def playlist_index(iter):
                return playlist.get_path(iter).get_indices()[0]

            playlist.previous()
            playlist.previous() # first is not changing current_iter, why?
            iter = playlist.current_iter

            if not iter:
                app.player.previous()
                return

            grouping = song_grouping(iter)
            i = playlist_index(iter)
            while True:
                playlist.previous()
                previous = playlist.current_iter
                if previous is None or song_grouping(previous) != grouping or playlist_index(previous) >= i:
                    break
                iter = previous

            if playlist.current_iter:
                playlist.next()

            app.player.go_to(iter)
            app.player.play()

    @classmethod
    def PluginPreferences(cls, window):
        def on_change(widget, config_entry):
            config_value = widget.get_text()
            pconfig.set(config_entry, config_value)

        def on_spin(widget, config_entry):
            config_value = widget.get_value()
            pconfig.set(config_entry, config_value)

        def default_on_click(widget):
            pconfig.reset("grouping")
            pconfig.reset("grouping_filter")
            pconfig.reset("delay")
            grouping_entry.set_text(pconfig.gettext("grouping"))
            grouping_filter_entry.set_text(pconfig.gettext("grouping_filter"))
            delay_spin.set_value(pconfig.getint("delay"))

        def make_label(label_text):
            label = Gtk.Label(label_text, selectable=True)
            label.set_alignment(0.0, 0.5)
            label.set_margin_end(3)
            return label

        vbox = Gtk.VBox(spacing=12)

        grouping_label = make_label(_("Grouping tag:"))
        grouping_entry = Gtk.Entry()
        grouping_entry.connect("changed", on_change, "grouping")
        grouping_entry.set_text(pconfig.gettext("grouping"))
        grouping_entry.set_tooltip_text(_("Tag to group songs by"))

        grouping_filter_label = make_label(_("Filter tag:"))
        grouping_filter_entry = Gtk.Entry()
        grouping_filter_entry.connect("changed", on_change, "grouping_filter")
        grouping_filter_entry.set_text(pconfig.gettext("grouping_filter"))
        grouping_filter_entry.set_tooltip_text(_(
            "Grouping is applied only if the filter tag is defined.\n"
            "A song with an undefined filter tag will be treated as\n"
            "a group consisting only of itself. Typically the filter\n"
            "tag should match or partially match the grouping tag."))

        delay_label = make_label(_("Delay:"))
        adj = Gtk.Adjustment.new(pconfig.getint("delay"), 0, 3600, 1, 5, 0)
        delay_spin = Gtk.SpinButton(adjustment=adj, climb_rate=0.1, digits=0)
        delay_spin.set_numeric(True)
        delay_spin.connect("value-changed", on_spin, "delay")
        delay_spin.set_tooltip_text(_(
            "Time delay in seconds before starting next group"))

        table = Gtk.Table(3, 2)
        table.set_col_spacings(6)
        table.set_row_spacings(6)

        table.attach(grouping_label, 0, 1, 0, 1, Gtk.AttachOptions.FILL)
        table.attach(grouping_filter_label, 0, 1, 1, 2, Gtk.AttachOptions.FILL)
        table.attach(delay_label, 0, 1, 2, 3, Gtk.AttachOptions.FILL)

        table.attach(grouping_entry, 1, 2, 0, 1)
        table.attach(grouping_filter_entry, 1, 2, 1, 2)
        table.attach(delay_spin, 1, 2, 2, 3)

        vbox.add(table)

        defaults = Gtk.Button(_("Reset to defaults"))
        defaults.connect("clicked", default_on_click)
        vbox.add(defaults)

        return vbox
