# Copyright 2015    Christoph Reiter
#           2016-23 Nick Boultbee
#           2019    Peter Strulo
#           2022-23 Jej@github
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from collections.abc import Callable

from gi.repository import Gtk

from quodlibet import _
from quodlibet import config
from quodlibet.qltk import Icons
from quodlibet.qltk.ccb import ConfigSwitch
from quodlibet.qltk.entry import UndoEntry
from quodlibet.util.string import decode


def _config(section, option, label, tooltip=None, getter=None):
    def on_changed(entry, *args):
        config.settext(section, option, entry.get_text())

    entry = UndoEntry()
    if tooltip:
        entry.set_tooltip_text(tooltip)
    entry.set_text(config.gettext(section, option))
    entry.connect("changed", on_changed)

    def on_reverted(*args):
        config.reset(section, option)
        entry.set_text(config.gettext(section, option))

    revert = revert_button(on_reverted)
    lbl = Gtk.Label(label=label, use_underline=True)
    lbl.set_mnemonic_widget(entry)
    return lbl, entry, revert


def text_config(section, option, label, tooltip=None):
    def getter(section, option):
        return decode(config.get(section, option))

    return _config(section, option, label, tooltip, getter)


def boolean_config(section, option, label, tooltip):
    def on_reverted(*args):
        config.reset(section, option)
        button.set_active(config.getboolean(section, option))

    button = ConfigSwitch(None, section, option, tooltip=tooltip, populate=True)
    button_box = Gtk.VBox(homogeneous=True)
    button_box.pack_start(button, False, False, 0)

    lbl = Gtk.Label(label=label, use_underline=True)
    lbl.set_mnemonic_widget(button.switch)
    revert = revert_button(on_reverted)
    return lbl, button_box, revert


def revert_button(on_reverted: Callable[..., None]) -> Gtk.Button:
    revert = Gtk.Button()
    revert.add(Gtk.Image.new_from_icon_name(Icons.DOCUMENT_REVERT, Gtk.IconSize.BUTTON))
    revert.connect("clicked", on_reverted)
    revert.set_tooltip_text(_("Revert to default"))
    return revert


def int_config(section, option, label, tooltip):
    def getter(section, option):
        return str(config.getint(section, option))

    return _config(section, option, label, tooltip, getter)


def slider_config(section, option, label, tooltip, lower=0, upper=1,
                  on_change_callback=None, label_value_callback=None):
    def on_reverted(*args):
        config.reset(section, option)
        scale.set_value(config.getfloat(section, option))

    def on_change(scale):
        value = scale.get_value()
        if on_change_callback:
            value = on_change_callback(value)
        scale.set_value(value)
        config.set(section, option, value)

    default = config.getfloat(section, option)

    adjustment = Gtk.Adjustment(value=default, lower=lower, upper=upper)
    scale = Gtk.HScale.new(adjustment)
    scale.set_value_pos(Gtk.PositionType.LEFT)
    scale.set_show_fill_level(True)
    scale.set_tooltip_text(_(tooltip))

    if label_value_callback:
        scale.connect("format-value", lambda _, value: label_value_callback(value))
    scale.connect("value-changed", on_change)

    revert = revert_button(on_reverted)
    lbl = Gtk.Label(label=label, use_underline=True)
    lbl.set_mnemonic_widget(scale)
    return lbl, scale, revert


class AdvancedPreferencesPane(Gtk.VBox):
    MARGIN = 12

    def __init__(self, *args):
        super().__init__()

        # We don't use translations as these things are internal
        # and don't want to burden the translators...
        # TODO: rethink translation here? (#3494)
        rows = [
            text_config(
                "editing", "id3encoding",
                "ID3 encodings",
                ("ID3 encodings separated by spaces. "
                 "UTF-8 is always tried first, and Latin-1 is always tried last.")),
            text_config(
                "settings", "search_tags",
                "Extra search tags",
                ("Tags that get searched in addition to "
                 'the ones present in the song list. Separate with ","')),
            text_config("editing", "multi_line_tags",
                        "Multi-line tags",
                        ("Tags to consider as multi-line (delimited by \\n) "
                         "rather than multi-valued (comma-separated)")),
            text_config("settings", "rating_symbol_full", "Rating symbol (full)"),
            text_config("settings", "rating_symbol_blank", "Rating symbol (blank)"),
            text_config(
                "player", "backend",
                "Backend",
                "Identifier of the playback backend to use"),
            boolean_config(
                "settings", "disable_hints",
                "Disable hints",
                "Disable popup windows (treeview hints)"),
            int_config(
                "browsers", "cover_size",
                "Album cover size",
                ("Size of the album cover images in the album list browser "
                 "(restart required)")),
            boolean_config(
                "settings", "disable_cover",
                "Disable cover in the main player controls",
                "(restart required)"),
            text_config(
                "albumart", "search_filenames",
                "Album art search filenames",
                "Which specific files are (also) tried for album art"),
            boolean_config(
                "settings", "disable_mmkeys",
                "Disable multimedia keys",
                "(restart required)"),
            text_config(
                "settings", "window_title_pattern",
                "Main window title",
                ("A (tied) tag for the main window title, e.g. ~title~~people "
                 "(restart required)")),
            text_config(
                "settings", "datecolumn_timestamp_format",
                "Timestamp date format",
                "A timestamp format for dates, e.g. %Y%m%d %X (restart required)"),
            boolean_config(
                "settings", "scrollbar_always_visible",
                "Scrollbars always visible",
                ("Toggles whether the scrollbars on the bottom and side of "
                 "the window always are visible or get hidden when not in use "
                 "(restart required)")),
            boolean_config(
                "settings", "monospace_query",
                "Use monospace font for search input",
                "Helps readability of code-like queries, but looks less consistent "
                "(restart required)"),
            text_config(
                "settings", "query_font_size",
                "Search input font size",
                "Size to apply to the search query entry, "
                "in any Pango CSS units, e.g. '100%', '1rem'. (restart required)"),
            boolean_config(
                "settings", "pangocairo_force_fontconfig",
                "Force Use Fontconfig Backend",
                "It's not the default on win/macOS (restart required)"),
            text_config(
                "browsers", "ignored_characters",
                "Ignored characters",
                "Characters to ignore in queries"),
            boolean_config(
                "settings", "plugins_window_on_top",
                "Plugin window on top",
                "Toggles whether the plugin window appears on top of others"),
            int_config(
                "autosave", "queue_interval",
                "Queue autosave interval",
                ("Longest time between play queue auto-saves, or 0 for disabled. "
                 "(restart required)")),
            int_config(
                "browsers", "searchbar_historic_entries",
                "Number of history entries in the search bar",
                "8 by default (restart advised)"),
            int_config(
                "browsers", "searchbar_enqueue_limit",
                "Search bar confirmation limit for enqueue",
                ("Maximal size of the song list that can be enqueued from "
                 "the search bar without confirmation.")),
            slider_config(
                "player", "playcount_minimum_length_proportion",
                "Minimum length to consider a track as played",
                ("Consider a track played after listening to this proportion of "
                 "its total duration"),
                label_value_callback=lambda value: f"{int(value * 100)}%"),
            text_config(
                "browsers", "missing_title_template",
                "Missing title template string",
                ("Template for building title of tracks when title tag is missing. "
                 "Tags are allowed, like <~basename> <~dirname> <~format> <~length> "
                 "<~#bitrate>, etc. See tags documentation for details.")
            )
        ]

        # Tabulate all settings for neatness
        table = Gtk.Table(n_rows=len(rows), n_columns=4)
        table.set_col_spacings(12)
        table.set_row_spacings(8)
        table.set_no_show_all(True)

        for row, (label, widget, button) in enumerate(rows):
            label.set_alignment(0.0, 0.5)
            table.attach(label, 0, 1, row, row + 1, xoptions=Gtk.AttachOptions.FILL)
            if isinstance(widget, Gtk.VBox):
                # This stops checkbox from expanding too big, or shrinking text entries
                blank = Gtk.Label()
                table.attach(blank, 1, 2, row, row + 1,
                             xoptions=Gtk.AttachOptions.EXPAND)
                table.attach(widget, 2, 3, row, row + 1,
                             xoptions=Gtk.AttachOptions.SHRINK)
            else:
                xoptions = Gtk.AttachOptions.FILL
                table.attach(widget, 1, 3, row, row + 1, xoptions=xoptions)
            table.attach(button, 3, 4, row, row + 1,
                         xoptions=Gtk.AttachOptions.SHRINK)

        def on_click(button):
            button.hide()
            table.set_no_show_all(False)
            table.show_all()

        button = Gtk.Button(label=_("I know what I'm doing!"), use_underline=True)
        button.connect("clicked", on_click)

        self.pack_start(button, False, True, 0)
        self.pack_start(table, True, True, 0)
