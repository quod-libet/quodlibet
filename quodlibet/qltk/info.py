# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#           2011-2020 Nick Boultbee
#           2014 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os

from gi.repository import Gtk, Gdk, Pango

from quodlibet import _

from quodlibet import print_w
from quodlibet import qltk
from quodlibet.player._base import BasePlayer
from quodlibet.qltk.songsmenu import SongsMenu
from quodlibet.qltk.x import SeparatorMenuItem, Align
from quodlibet.qltk import Icons
from quodlibet.util import connect_destroy

from quodlibet.pattern import XMLFromMarkupPattern
from quodlibet.qltk.textedit import PatternEdit


class SongInfo(Gtk.EventBox):
    """A widget for showing information about the currently playing song.

    Provides a way to change the display pattern for formatting the
    song information and a song context menu.
    """

    _FORMAT_VARS = {
        # Translators: As in "by Artist Name"
        "people": _("by %s") % "<~people>",
        "disc": _("Disc %s") % "<discnumber>",
        "track": _("Track %s") % "<tracknumber>",
    }

    _pattern = """\
[span weight='bold' size='large']<title>[/span]\
<~length| (<~length>)><version|
[small][b]<version>[/b][/small]><~people|
{people}><album|
[b]<album>[/b]<discnumber| - {disc}>\
<discsubtitle| - [b]<discsubtitle>[/b]><tracknumber| - {track}>>""".format(
        **_FORMAT_VARS
    )

    _not_playing = "<span size='xx-large'>{}</span>".format(_("Not playing"))

    def __init__(self, library, player, pattern_filename):
        super().__init__()
        self._pattern_filename = pattern_filename
        self.set_visible_window(False)
        align = Align(halign=Gtk.Align.START, valign=Gtk.Align.START)
        label = Gtk.Label()
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        label.set_track_visited_links(False)
        label.set_selectable(True)
        align.add(label)
        label.set_alignment(0.0, 0.0)
        self._label = label
        connect_destroy(library, "changed", self._on_library_changed, player)
        connect_destroy(player, "song-started", self._on_song_started)

        label.connect("populate-popup", self._on_label_popup, player, library)
        self.connect("key-press-event", self._on_key_press_event, player)
        self.connect("button-press-event", self._on_button_press_event, player, library)

        try:
            with open(self._pattern_filename, "rb") as h:
                self._pattern = h.read().strip().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            pass

        self._compiled = XMLFromMarkupPattern(self._pattern)
        align.show_all()
        self.add(align)

    def _on_key_press_event(self, widget, event, player: BasePlayer):
        if qltk.is_accel(event, "space"):
            player.playpause()

    def _on_button_press_event(self, widget, event, player, library):
        if event.triggers_context_menu():
            menu = self._get_menu(player, library)
            menu.attach_to_widget(widget, None)
            menu.popup(None, None, None, None, event.button, event.time)
            return True
        return False

    def _on_label_popup(self, label, menu, player, library):
        song_menu = self._get_menu(player, library)

        has_selection = label.get_selection_bounds()[0]

        if not has_selection:
            for child in menu.get_children():
                child.destroy()
            for item in song_menu:
                song_menu.remove(item)
                menu.append(item)
        else:
            sub = Gtk.MenuItem.new_with_mnemonic(_("Current _Song"))
            sub.set_submenu(song_menu)
            sub.set_sensitive(player.song is not None)
            sub.show_all()
            sep = SeparatorMenuItem()
            sep.show()
            menu.append(sep)
            menu.append(sub)

    def _get_menu(self, player, library):
        item = qltk.MenuItem(_("_Edit Display…"), Icons.EDIT)
        item.connect("activate", self._on_edit_display, player)

        songs = [player.song] if player.song else []
        song_menu = SongsMenu(
            library, songs, remove=False, delete=True, accels=False, items=[[item]]
        )

        song_menu.show_all()
        return song_menu

    def _on_edit_display(self, menu_item, player):
        editor = PatternEdit(
            self, SongInfo._pattern, alternative_markup=True, links=True
        )
        editor.text = self._pattern
        editor.apply.connect("clicked", self._on_set_pattern, editor, player)
        editor.show()

    def _on_set_pattern(self, button, edit, player):
        self._pattern = edit.text.rstrip()
        if self._pattern == SongInfo._pattern:
            try:
                os.unlink(self._pattern_filename)
            except OSError:
                pass
        else:
            try:
                with open(self._pattern_filename, "wb") as h:
                    h.write(self._pattern.encode("utf-8") + b"\n")
            except OSError as e:
                print_w(f"Couldn't save display pattern '{self._pattern}' ({e})")
        self._compiled = XMLFromMarkupPattern(self._pattern)
        self._update_info(player)

    def _on_library_changed(self, library, songs, player):
        if player.info in songs:
            self._update_info(player)

    def _on_song_started(self, player, song):
        self._update_info(player)

    def _update_info(self, player, _last={}):  # noqa
        text = (
            self._not_playing if player.info is None else self._compiled % player.info
        )

        # some radio streams update way too often and updating the label
        # destroys the text selection
        if text not in _last:
            self._label.set_markup(text)
            _last.clear()
            _last[text] = True
