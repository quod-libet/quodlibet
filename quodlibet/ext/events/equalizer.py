# Copyright 2010 Steven Robertson
#           2012 Christoph Reiter
#           2017 Nick Boultbee
#           2018 Olli Helin
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from gi.repository import Gtk, Gdk

from quodlibet import _
from quodlibet import app
from quodlibet import config
from quodlibet.qltk import Button, Icons
from quodlibet.plugins.events import EventPlugin
from quodlibet.util import print_e, print_w

import ast

# Presets (roughly) taken from Pulseaudio equalizer
PRESET_BANDS = [
    50,
    100,
    156,
    220,
    311,
    440,
    622,
    880,
    1250,
    1750,
    2500,
    3500,
    5000,
    10000,
    20000,
]
PRESETS = {
    "flat": (_("Flat"), [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
    "live": (
        _("Live"),
        [-9.0, -5.5, 0.0, 1.5, 2.0, 3.5, 3.5, 3.5, 3.5, 3.5, 3.5, 3.5, 3.0, 1.5, 2.0],
    ),
    "full_bass_treble": (
        _("Full Bass & Treble"),
        [
            5.0,
            5.0,
            3.5,
            2.5,
            0.0,
            -7.0,
            -14.0,
            -10.0,
            -10.0,
            -8.0,
            1.0,
            1.0,
            5.0,
            7.5,
            9.5,
        ],
    ),
    "club": (
        _("Club"),
        [0.0, 0.0, 0.0, 0.0, 3.5, 3.5, 3.5, 3.5, 3.5, 3.5, 3.5, 2.5, 2.5, 0.0, 0.0],
    ),
    "large_hall": (
        _("Large Hall"),
        [
            7.0,
            7.0,
            7.0,
            3.5,
            3.0,
            3.0,
            3.0,
            1.5,
            0.0,
            -2.0,
            -3.5,
            -6.0,
            -9.0,
            -1.0,
            0.0,
        ],
    ),
    "party": (
        _("Party"),
        [5.0, 5.0, 5.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.5, 5.0],
    ),
    "rock": (
        _("Rock"),
        [
            5.5,
            2.5,
            2.5,
            -8.5,
            -10.5,
            -11.0,
            -16.0,
            -14.5,
            -6.5,
            -5.5,
            -3.0,
            3.0,
            6.5,
            7.0,
            7.0,
        ],
    ),
    "soft": (
        _("Soft"),
        [3.0, 3.0, 1.0, 1.0, 0.0, -2.5, -5.0, 1.5, 0.0, 1.0, 3.0, 3.0, 6.0, 8.0, 8.0],
    ),
    "full_bass": (
        _("Full Bass"),
        [
            -16.0,
            -16.0,
            6.5,
            6.5,
            6.0,
            5.5,
            4.5,
            1.0,
            1.0,
            1.0,
            -8.0,
            -10.0,
            -16.0,
            -16.0,
            -20.5,
        ],
    ),
    "classical": (
        _("Classical"),
        [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -21.0, -21.0, -27.0],
    ),
    "reggae": (
        _("Reggae"),
        [0.0, 0.0, 0.0, 0.0, 0.0, -4.5, -10.0, -6.0, 0.5, 1.0, 2.0, 4.0, 4.0, 0.0, 0.0],
    ),
    "headphones": (
        _("Headphones"),
        [
            3.0,
            3.0,
            7.0,
            7.0,
            3.0,
            -1.0,
            -6.5,
            -6.0,
            -4.5,
            -4.0,
            1.0,
            1.0,
            6.0,
            8.0,
            9.0,
        ],
    ),
    "soft_rock": (
        _("Soft Rock"),
        [
            3.0,
            3.0,
            3.0,
            1.5,
            1.5,
            1.5,
            0.0,
            -3.5,
            -8.0,
            -7.0,
            -10,
            -9.0,
            -6.5,
            1.5,
            6.0,
        ],
    ),
    "full_treble": (
        _("Full Treble"),
        [
            5.0,
            -18.5,
            -18.5,
            -18.5,
            -18.5,
            -10.0,
            -8.0,
            -6.5,
            1.5,
            1.5,
            1.5,
            8.5,
            10.5,
            10.5,
            10.5,
        ],
    ),
    "dance": (
        _("Dance"),
        [
            6.0,
            4.0,
            4.0,
            1.5,
            1.5,
            1.5,
            0.0,
            0.0,
            0.0,
            1.0,
            -10.5,
            -14.0,
            -15.0,
            -7.0,
            0.0,
        ],
    ),
    "pop": (
        _("Pop"),
        [
            -3.5,
            1.0,
            2.0,
            3.0,
            5.0,
            5.5,
            6.5,
            5.0,
            3.0,
            1.5,
            0.0,
            -2.5,
            -5.0,
            -5.0,
            -3.0,
        ],
    ),
    "techno": (
        _("Techno"),
        [
            5.0,
            4.0,
            4.0,
            3.0,
            0.0,
            -4.5,
            -10.0,
            -9.0,
            -8.0,
            -5.5,
            -1.5,
            3.0,
            6.0,
            6.0,
            6.0,
        ],
    ),
    "ska": (
        _("Ska"),
        [
            -4.5,
            -8.0,
            -9.0,
            -8.5,
            -8.0,
            -6.0,
            0.0,
            1.5,
            2.5,
            2.5,
            3.0,
            3.0,
            6.0,
            6.0,
            6.0,
        ],
    ),
    "laptop": (
        _("Laptop"),
        [-1, -1, -1, -1, -5, -10, -18, -15, -10, -5, -5, -5, -5, 0, 0],
    ),
}


def interp_bands(src_band, target_band, src_gain):
    """Linear interp from one band to another. All must be sorted."""
    gain = []
    for i, b in enumerate(target_band):
        if b in src_band:
            gain.append(src_gain[i])
            continue
        idx = sorted(src_band + [b]).index(b)
        idx = min(max(idx, 1), len(src_band) - 1)
        x1, x2 = src_band[idx - 1 : idx + 1]
        y1, y2 = src_gain[idx - 1 : idx + 1]
        g = y1 + ((y2 - y1) * (b - x1)) / float(x2 - x1)
        gain.append(min(12.0, g))
    return gain


def get_config():
    try:
        config_str = config.get("plugins", "equalizer_levels", "[]")
        config_dict = ast.literal_eval(config_str)

        if isinstance(config_dict, list):
            print_w("Converting old EQ config to new format.")
            config_dict = {"Current": config_dict}
        if not isinstance(config_dict, dict):
            raise ValueError("Saved config is of wrong type.")
        if "Current" not in config_dict.keys():
            raise ValueError("Saved config was malformed.")

        # Run through the values to check everything is of correct type.
        for key in config_dict.keys():
            [float(s) for s in config_dict[key]]

        return config_dict
    except (config.Error, ValueError) as e:
        print_e(str(e))
        return {"Current": []}


class Equalizer(EventPlugin):
    PLUGIN_ID = "Equalizer"
    PLUGIN_NAME = _("Equalizer")
    PLUGIN_DESC = _(
        "Controls the tone of your music with an equalizer.\n"
        "Click or use keys to customise levels "
        "(right-click resets the band)."
    )
    PLUGIN_ICON = Icons.AUDIO_CARD

    @property
    def player_has_eq(self):
        return hasattr(app.player, "eq_bands") and app.player.eq_bands

    def __init__(self):
        super().__init__()
        self._enabled = False
        self._config = {}

    def apply(self):
        if not self.player_has_eq:
            return
        levels = self._enabled and get_config()["Current"] or []
        lbands = len(app.player.eq_bands)
        if len(levels) != lbands:
            print_w("Number of bands didn't match current. Using flat EQ.")
            levels = [0.0] * lbands
        app.player.eq_values = levels

    def enabled(self):
        self._enabled = True
        self.apply()

    def disabled(self):
        self._enabled = False
        self.apply()

    def PluginPreferences(self, win):
        main_vbox = Gtk.VBox(spacing=12)
        if not self.player_has_eq:
            l = Gtk.Label()
            l.set_markup(_("The current backend does not support equalization."))
            main_vbox.pack_start(l, False, True, 0)
            return main_vbox

        def format_hertz(band):
            if band >= 1000:
                return _("%.1f kHz") % (band / 1000.0)
            return _("%d Hz") % band

        bands = [format_hertz(band) for band in app.player.eq_bands]
        self._config = get_config()
        levels = self._config["Current"]

        # This fixes possible old corrupt config files with extra level values.
        if len(levels) != len(bands):
            print_w("Number of bands didn't match current. Using flat EQ.")
            levels = [0.0] * len(bands)

        table = Gtk.Table(rows=len(bands), columns=3)
        table.set_col_spacings(6)

        def set_band(adj, idx):
            rounded = int(adj.get_value() * 2) / 2.0
            adj.set_value(rounded)
            levels[idx] = rounded

            self._config["Current"] = levels
            config.set("plugins", "equalizer_levels", str(self._config))
            self.apply()

        adjustments = []

        for i, band in enumerate(bands):
            # align numbers and suffixes in separate rows for great justice
            lbl = Gtk.Label(label=band.split()[0])
            lbl.set_alignment(1, 0.5)
            lbl.set_padding(0, 4)
            table.attach(lbl, 0, 1, i, i + 1, xoptions=Gtk.AttachOptions.FILL)
            lbl = Gtk.Label(label=band.split()[1])
            lbl.set_alignment(1, 0.5)
            table.attach(lbl, 1, 2, i, i + 1, xoptions=Gtk.AttachOptions.FILL)
            adj = Gtk.Adjustment.new(levels[i], -24.0, 12.0, 0.5, 3, 0)
            adj.connect("value-changed", set_band, i)
            adjustments.append(adj)
            hs = Gtk.HScale(adjustment=adj)
            hs.connect("button-press-event", self.__rightclick)
            hs.set_draw_value(True)
            hs.set_value_pos(Gtk.PositionType.RIGHT)
            hs.connect("format-value", lambda s, v: _("%.1f dB") % v)
            table.attach(hs, 2, 3, i, i + 1)
        main_vbox.pack_start(table, True, True, 0)

        # Reset EQ button
        def clicked_rb(button):
            [adj.set_value(0) for adj in adjustments]
            self._combo_default.set_active(0)
            self._combo_custom.set_active(0)

        # Delete custom preset button
        def clicked_db(button):
            selected_index = self._combo_custom.get_active()
            if selected_index < 1:
                return  # Select…
            selected = self._combo_custom.get_active_text()
            self._combo_custom.set_active(0)
            self._combo_custom.remove(selected_index)
            del self._config[selected]
            config.set("plugins", "equalizer_levels", str(self._config))

        # Save custom preset button
        def clicked_sb(button):
            name = self._preset_name_entry.get_text()
            is_new = name not in self._config.keys()

            levels = [adj.get_value() for adj in adjustments]
            self._config[name] = levels
            config.set("plugins", "equalizer_levels", str(self._config))

            self._preset_name_entry.set_text("")
            if is_new:
                self._combo_custom.append_text(name)

            def find_iter(list_store, text):
                i = list_store.get_iter_first()
                while i is not None:
                    if list_store.get_value(i, 0) == text:
                        return i
                    i = list_store.iter_next(i)
                return None

            itr = find_iter(self._combo_custom.get_model(), name)
            self._combo_custom.set_active_iter(itr)

        sorted_presets = sorted(PRESETS.items())

        def default_combo_changed(combo):
            if combo.get_active() < 1:
                return  # Select…
            self._combo_custom.set_active(0)
            gain = sorted_presets[combo.get_active() - 1][1][1]
            gain = interp_bands(PRESET_BANDS, app.player.eq_bands, gain)
            for g, a in zip(gain, adjustments, strict=False):
                a.set_value(g)

        def custom_combo_changed(combo):
            if combo.get_active() < 1:
                # Case: Select…
                self._delete_button.set_sensitive(False)
                return
            self._combo_default.set_active(0)
            self._delete_button.set_sensitive(True)
            gain = self._config[combo.get_active_text()]
            for g, a in zip(gain, adjustments, strict=False):
                a.set_value(g)

        def save_name_changed(entry):
            name = entry.get_text()
            if not name or name == "Current" or name.isspace():
                self._save_button.set_sensitive(False)
            else:
                self._save_button.set_sensitive(True)

        frame = Gtk.Frame(label=_("Default presets"), label_xalign=0.5)
        main_middle_hbox = Gtk.HBox(spacing=6)

        # Default presets
        combo = Gtk.ComboBoxText()
        self._combo_default = combo
        combo.append_text(_("Select…"))
        combo.set_active(0)
        for _key, (name, _gain) in sorted_presets:
            combo.append_text(name)
        combo.connect("changed", default_combo_changed)

        # This block is just for padding.
        padboxv = Gtk.VBox()
        padboxv.pack_start(combo, True, True, 6)
        padboxh = Gtk.HBox()
        padboxh.pack_start(padboxv, True, True, 6)
        frame.add(padboxh)

        main_middle_hbox.pack_start(frame, True, True, 0)

        reset = Button(_("_Reset EQ"), Icons.EDIT_UNDO)
        reset.connect("clicked", clicked_rb)
        main_middle_hbox.pack_start(reset, False, False, 0)

        main_vbox.pack_start(main_middle_hbox, False, False, 0)

        frame = Gtk.Frame(label=_("Custom presets"), label_xalign=0.5)
        main_bottom_vbox = Gtk.VBox()

        # Custom presets
        combo = Gtk.ComboBoxText()
        self._combo_custom = combo
        combo.append_text(_("Select…"))
        combo.set_active(0)

        custom_presets = self._config.keys() - {"Current"}
        for key in custom_presets:
            combo.append_text(key)
        combo.connect("changed", custom_combo_changed)
        hb = Gtk.HBox(spacing=6)
        hb.pack_start(combo, True, True, 0)

        delete = Button(_("_Delete selected"), Icons.EDIT_DELETE)
        delete.connect("clicked", clicked_db)
        delete.set_sensitive(False)
        self._delete_button = delete
        hb.pack_start(delete, False, False, 0)

        main_bottom_vbox.pack_start(hb, True, True, 6)
        hs = Gtk.HSeparator()
        main_bottom_vbox.pack_start(hs, True, True, 6)

        hb = Gtk.HBox()
        l = Gtk.Label(label=_("Preset name for saving:"))
        hb.pack_start(l, False, False, 0)
        main_bottom_vbox.pack_start(hb, False, False, 0)

        e = Gtk.Entry()
        e.connect("changed", save_name_changed)
        self._preset_name_entry = e
        hb = Gtk.HBox(spacing=6)
        hb.pack_start(e, True, True, 0)

        save = Button(_("_Save"), Icons.DOCUMENT_SAVE)
        save.connect("clicked", clicked_sb)
        save.set_sensitive(False)
        self._save_button = save
        hb.pack_start(save, False, False, 0)

        main_bottom_vbox.pack_start(hb, True, True, 6)

        # This block is just for padding.
        padboxh = Gtk.HBox()
        padboxh.pack_start(main_bottom_vbox, True, True, 6)
        frame.add(padboxh)

        main_vbox.pack_start(frame, True, True, 0)
        return main_vbox

    def __rightclick(self, hs, event):
        if event.triggers_context_menu():
            hs.set_value(0)
