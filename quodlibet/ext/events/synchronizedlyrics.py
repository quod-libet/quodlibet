# Synchronized Lyrics: a Quod Libet plugin for showing synchronized lyrics.
# Copyright (C) 2015 elfalem
#            2016-20 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
import functools
import os
import re
from os.path import splitext
from typing import List, Tuple, Optional

from gi.repository import Gtk, Gdk, GLib

from quodlibet import _
from quodlibet import app
from quodlibet import qltk
from quodlibet.formats import AudioFile
from quodlibet.plugins import PluginConfigMixin
from quodlibet.plugins.events import EventPlugin
from quodlibet.qltk import Icons
from quodlibet.util.dprint import print_d


class SynchronizedLyrics(EventPlugin, PluginConfigMixin):
    PLUGIN_ID = 'SynchronizedLyrics'
    PLUGIN_NAME = _('Synchronized Lyrics')
    PLUGIN_DESC = _('Shows synchronized lyrics from an .lrc file '
                    'with same name as the track (or similar), or '
                    'embedded tags.')
    PLUGIN_ICON = Icons.FORMAT_JUSTIFY_FILL

    SYNC_PERIOD = 10000

    DEFAULT_BGCOLOR = '#343428282C2C'
    DEFAULT_HLCOLOR = '#FFFFF000000'
    DEFAULT_TXTCOLOR = '#FFFFFFFFFFF'
    DEFAULT_FONTSIZE = 25

    CFG_BGCOLOR_KEY = "backgroundColor"
    CFG_HLCOLOR_KEY = "highlightColor"
    CFG_TXTCOLOR_KEY = "textColor"
    CFG_FONTSIZE_KEY = "fontSize"

    LINE_REGEX = re.compile(r"<[\d]{2}:[\d]{2}\.[\d]{2}>|\[[\d]{2}:[\d]{2}\.[\d]{2}\]")

    def __init__(self) -> None:
        super().__init__()
        self._lines: List[Tuple[int, str]] = []
        self._timers: List[Tuple[int, int]] = []
        self._start_clearing_from = 0
        self.textview = None
        self.scrolled_window = None

    def PluginPreferences(cls, window):
        vb = Gtk.VBox(spacing=6)
        vb.set_border_width(6)

        t = Gtk.Table(n_rows=5, n_columns=2, homogeneous=True)
        t.set_col_spacings(6)
        t.set_row_spacings(3)

        clr_section = Gtk.Label()
        clr_section.set_markup("<b>" + _("Colors") + "</b>")
        t.attach(clr_section, 0, 2, 0, 1)

        l = Gtk.Label(label=_("Text:"))
        l.set_alignment(xalign=1.0, yalign=0.5)
        t.attach(l, 0, 1, 1, 2, xoptions=Gtk.AttachOptions.FILL)

        c = Gdk.RGBA()
        c.parse(cls._get_text_color())
        b = Gtk.ColorButton(rgba=c)
        t.attach(b, 1, 2, 1, 2)
        b.connect('color-set', cls._set_text_color)

        l = Gtk.Label(label=_("Background:"))
        l.set_alignment(xalign=1.0, yalign=0.5)
        t.attach(l, 0, 1, 2, 3, xoptions=Gtk.AttachOptions.FILL)

        c = Gdk.RGBA()
        c.parse(cls._get_background_color())
        b = Gtk.ColorButton(rgba=c)
        t.attach(b, 1, 2, 2, 3)
        b.connect('color-set', cls._set_background_color)

        l = Gtk.Label(label=_("Highlight"))
        l.set_alignment(xalign=1.0, yalign=0.5)
        t.attach(l, 0, 1, 3, 4, xoptions=Gtk.AttachOptions.FILL)

        c = Gdk.RGBA()
        c.parse(cls._get_highlight_color())
        b = Gtk.ColorButton(rgba=c)
        t.attach(b, 1, 2, 3, 4)
        b.connect('color-set', cls._set_highlight_color)

        font_section = Gtk.Label()
        font_section.set_markup("<b>" + _("Font") + "</b>")
        t.attach(font_section, 0, 2, 4, 5)

        l = Gtk.Label(label=_("Size (px):"))
        l.set_alignment(xalign=1.0, yalign=0.5)
        t.attach(l, 0, 1, 5, 6, xoptions=Gtk.AttachOptions.FILL)

        a = Gtk.Adjustment.new(cls._get_font_size(), 10, 72, 2, 3, 0)
        s = Gtk.SpinButton(adjustment=a)
        s.set_numeric(True)
        s.set_text(str(cls._get_font_size()))
        t.attach(s, 1, 2, 5, 6)
        s.connect('value-changed', cls._set_font_size)

        vb.pack_start(t, False, False, 0)
        return vb

    def _get_text_color(self):
        v = self.config_get(self.CFG_TXTCOLOR_KEY, self.DEFAULT_TXTCOLOR)
        return v[:3] + v[5:7] + v[9:11]

    def _get_background_color(self):
        v = self.config_get(self.CFG_BGCOLOR_KEY, self.DEFAULT_BGCOLOR)
        return v[:3] + v[5:7] + v[9:11]

    def _get_highlight_color(self):
        v = self.config_get(self.CFG_HLCOLOR_KEY, self.DEFAULT_HLCOLOR)
        return v[:3] + v[5:7] + v[9:11]

    def _get_font_size(self):
        return int(self.config_get(self.CFG_FONTSIZE_KEY,
                                   self.DEFAULT_FONTSIZE))

    def _set_text_color(self, button):
        self.config_set(self.CFG_TXTCOLOR_KEY, button.get_color().to_string())
        self._style_lyrics_window()

    def _set_background_color(self, button):
        self.config_set(self.CFG_BGCOLOR_KEY, button.get_color().to_string())
        self._style_lyrics_window()

    def _set_highlight_color(self, button):
        self.config_set(self.CFG_HLCOLOR_KEY, button.get_color().to_string())
        self._style_lyrics_window()

    def _set_font_size(self, button):
        self.config_set(self.CFG_FONTSIZE_KEY, button.get_value_as_int())
        self._style_lyrics_window()

    def enabled(self):
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC,
                                        Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.get_vadjustment().set_value(0)

        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.textview.set_justification(Gtk.Justification.CENTER)
        self.textview.get_buffer().create_tag("highlight",\
                foreground=self._get_background_color())
        self.scrolled_window.add_with_viewport(self.textview)

        vb = Gtk.HBox()
        vb.pack_start(self.scrolled_window, True, True, 6)
        vb.show_all()
        app.window.get_child().pack_start(vb, False, True, 0)
        app.window.get_child().reorder_child(vb, 2)

        self._style_lyrics_window()
        self.scrolled_window.show()

        self._sync_timer = GLib.timeout_add(self.SYNC_PERIOD, self._sync)
        self._build_data(app.player.song)
        self._timer_control()

    def disabled(self):
        self._clear_timers()
        GLib.source_remove(self._sync_timer)
        self.textview.destroy()
        self.textview = None
        self.scrolled_window.destroy()
        self.scrolled_window = None

    def _style_lyrics_window(self):
        if self.scrolled_window is None:
            return
        self.scrolled_window.set_size_request(-1, 1.5 * self._get_font_size())
        qltk.add_css(self.textview, f"""
          * {{
                background-color: {self._get_background_color()};
                color: {self._get_text_color()};
                font-size: {self._get_font_size()}px;
                padding: 0.25rem;
                border-radius: 6px;
            }}
        """)

    def _cur_position(self):
        return app.player.get_position()

    @functools.lru_cache()
    def _build_data(self, song: Optional[AudioFile]) -> List[Tuple[int, str]]:
        if self.textview:
            self.textview.get_buffer().set_text("")
        if song:
            # check in same location as track
            track_name = splitext(song("~basename") or "")[0]
            dir_ = song("~dirname")
            print_d(f"Looking for .lrc files in {dir_}, or lyrics tag")
            for filename in [f"{s}.lrc"
                             for s in {
                                 track_name,
                                 track_name.lower(),
                                 track_name.upper(),
                                 song("~artist~title"),
                                 song("~artist~tracknumber~title"),
                                 song("~tracknumber~title")
                             }
                             ]:
                print_d(f"Looking for tags")
                contents = song("~lyrics")
                if contents is None:
                    try:
                        print_d(f"Looking for {filename!r}")
                        with open(os.path.join(dir_, filename), 'r', encoding="utf-8") as f:
                            print_d(f"Found lyrics file: {filename}")
                            contents = f.read()
                    except FileNotFoundError:
                        continue
                else:
                    print_d(f"Found lyrics in tag")
                    return self._parse_lrc(contents)
            print_d(f"No lyrics found for {track_name!r}")
        return []

    def _parse_lrc(self, contents: str) -> List[Tuple[int, str]]:
        data = []
        for line in contents.splitlines():
            matches = self.LINE_REGEX.finditer(line)

            groups = self._define_groups(matches, line)

            if not groups:
                continue
            text = ''
            # rebuild text
            for group in groups:
                text += group[1]

            index = 0
            line_timing = self._get_timestamp(groups[0][0])
            line_data = []
            line_data.append(line_timing)
            line_data.append(text)

            for group in groups:
                words = group[1]
                timing = self._get_timestamp(group[0])
                index = text.find(words) + len(words)
                line_data.append(timing)
                line_data.append(index)

            data.append(tuple(line_data))

        return sorted(data)

    def _get_timestamp(_self, timing):
        minutes, seconds = (float(p) for p in timing.split(":", 1))
        timestamp = int(1000 * (minutes * 60 + seconds))

        return timestamp

    def _define_groups(_self, matches, line):
        found = []
        last_match_end = None

        for matchNum, match in enumerate(matches, start=1):
            if len(found) > 0 and last_match_end is not None:
                # add the text from the end of the last match
                # to the start of the current match as the text of
                # the last match (to previous list value)
                found[-1].append(line[last_match_end:match.start()])

            # take the timestamp (=match) from the current match
            found.append([match.group().strip("<>[]")])
            # save end of this match
            last_match_end = match.end()

        if len(found) > 0 and last_match_end is not None:
            # add missing text of last match
            found[-1].append(line[last_match_end:])

        return found

    def _set_timers(self):
        if not self._timers:
            print_d("Setting timers")
            cur_time = self._cur_position()
            cur_idx = self._greater(self._lines, cur_time)
            if cur_idx != -1:
                while (cur_idx < len(self._lines) and
                       self._lines[cur_idx][0] < cur_time + self.SYNC_PERIOD):

                    cur_line = self._lines[cur_idx]
                    timestamp = cur_line[0]
                    line = cur_line[1]
                    tid = GLib.timeout_add(timestamp - cur_time,\
                            self._show, line)
                    self._timers.append((timestamp, tid))

                    # if the length of the line is equal to the part that
                    # should be first highlighted, then it won't be highlighted
                    if cur_line[3] != len(cur_line[1]):
                        iterator = zip(cur_line[2::2], cur_line[3::2])
                        for timestamp, hl_idx in iterator:
                            tid = GLib.timeout_add(timestamp - cur_time,\
                                    self._set_highlight, hl_idx)
                            self._timers.append((timestamp, tid))

                    cur_idx += 1

    def _sync(self):
        if not app.player.paused:
            self._clear_timers()
            self._set_timers()
        return True

    def _timer_control(self):
        if app.player.paused:
            self._clear_timers()
        else:
            self._set_timers()
        return False

    def _clear_timers(self):
        for _ts, tid in self._timers:
            GLib.source_remove(tid)
        self._timers = []
        self._start_clearing_from = 0

    def _show(self, line) -> bool:
        if self.textview:
            self.textview.get_buffer().set_text(line)

        self._start_clearing_from += 1
        print_d(f"♪ {line.strip()} ♪")
        return False

    def _set_highlight(self, offset):
        buffer = self.textview.get_buffer()
        start_hl = buffer.get_iter_at_offset(0)
        end_hl = buffer.get_iter_at_offset(offset)
        buffer.apply_tag_by_name("highlight", start_hl, end_hl)

    def plugin_on_song_started(self, song: AudioFile) -> None:
        if song:
            print_d(f"Preparing for {song.key}")
        self._clear_timers()
        self._lines = self._build_data(song)
        # delay so that current position is for current track, not previous one
        GLib.timeout_add(5, self._timer_control)

    def plugin_on_song_ended(self, song, stopped):
        self._clear_timers()

    def plugin_on_paused(self):
        self._timer_control()

    def plugin_on_unpaused(self):
        self._timer_control()

    def plugin_on_seek(self, song, msec):
        if not app.player.paused:
            self._clear_timers()
            self._set_timers()

    def _greater(self, array, probe):
        length = len(array)
        if length == 0:
            return -1
        elif probe < array[0][0]:
            return 0
        elif probe >= array[length - 1][0]:
            return length
        else:
            return self._search(array, probe, 0, length - 1)

    def _search(self, array, probe, lower, upper):
        if lower == upper:
            if array[lower][0] <= probe:
                return lower + 1
            else:
                return lower
        else:
            middle = int((lower + upper) / 2)
            if array[middle][0] <= probe:
                return self._search(array, probe, middle + 1, upper)
            else:
                return self._search(array, probe, lower, middle)
