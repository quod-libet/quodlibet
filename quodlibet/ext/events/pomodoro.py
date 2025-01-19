# Copyright (C) 2023 Azhar Madar Shaik (azarmadr@pm.me)
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from quodlibet.util.dprint import print_d
from gi.repository import Gtk, GLib
from quodlibet import _, app
from quodlibet.plugins.events import EventPlugin
from quodlibet.plugins import PluginConfigMixin
from quodlibet.plugins.gui import UserInterfacePlugin
from quodlibet.qltk import Icons


class PomodoroBox(Gtk.VBox):
    def __init__(self):
        super().__init__(self)

        hbox = Gtk.HBox()
        self.title = Gtk.Label("")
        self.switch = Gtk.Switch()
        self.ticks = Gtk.ProgressBar()
        self.long_break = Gtk.ProgressBar()
        self.long_break.set_text("Long Break Progress")
        self.long_break.set_show_text(1)

        hbox.pack_start(self.title, True, True, 5)
        hbox.pack_start(self.switch, True, True, 5)

        self.pack_start(hbox, False, False, 5)
        self.pack_start(self.ticks, True, True, 5)
        self.pack_start(self.long_break, True, True, 5)


class Pomodoro(EventPlugin, UserInterfacePlugin, PluginConfigMixin):
    """Plugin for pomodoro within quodlibet to help you focus"""

    PLUGIN_ID = "Pomodoro"
    PLUGIN_NAME = _("Pomodoro Clock")
    PLUGIN_DESC = _(
        "Pomodoro Clock for Quod Libet. "
        "Never loose focus when listening to music. "
        "Take breaks for a while after your focus session."
    )
    PLUGIN_ICON = Icons.APPOINTMENT_NEW

    long_break_gap = 2
    durations = [25, 5, 15]
    _unit = 60000

    def __init__(self):
        self.ticks = 1
        self.on_break = 0   # focus => 0, break => 1, long_break => 2
        for k, v in enumerate(
            ("Focus Duration", "Break Duration", "Long Break Duration")
        ):
            self.durations[k] = int(self.config_get(v, self.durations[k]))
        self.long_break_gap = int(
            self.config_get("Long Break Gap", self.long_break_gap)
        )
        self.till_long_break = 0
        print_d(
            f"long_break_gap: {self.long_break_gap}, durations: {self.durations}"
        )
        self.timer = None

    @classmethod
    def _set(cls, name, value):
        print_d(f"name:{name} name:{name}")
        cls.config_set(name, value)
        if name == "Focus Duration":
            cls.durations[0] = value
        elif name == "Break Duration":
            cls.durations[1] = value
        elif name == "Long Break Duration":
            cls.durations[2] = value
        elif name == "Long Break Gap":
            cls.long_break_gap = value
        else:
            print_d(f"Wrong config name {name} value({value})")

    @classmethod
    def _get(cls, name, default=""):
        print_d(f"name:{name} default:{default}")
        if name == "Focus Duration":
            default = cls.durations[0]
        elif name == "Break Duration":
            default = cls.durations[1]
        elif name == "Long Break Duration":
            default = cls.durations[2]
        elif name == "Long Break Gap":
            default = cls.long_break_gap
        else:
            print_d(f"Wrong config name {name} default({default})")
        return int(cls.config_get(name, default))

    def _dbug(self):
        mode = (
            "Focus"
            if not self.on_break
            else "Break"
            if self.on_break == 1
            else "Long Break"
        )
        title_color = "green" if self.timer else "red"
        self.box.title.set_markup(
            f'<b><span size="large" foreground="{title_color}">{mode}</span></b>'
        )
        self.box.ticks.set_fraction(self.ticks / self.durations[self.on_break])
        if not self.on_break:
            self.box.long_break.hide()
        return (
            f"{'ON' if self.timer else 'OFF'}({mode}): "
            f"ticks: {self.ticks}/{self.durations[self.on_break]},"
            f"timer-gid: {self.timer}, "
            f"tlb: {self.till_long_break}/{self.long_break_gap}"
        )

    def _run(self):
        self.ticks += 1
        if self.ticks > self.durations[self.on_break]:
            self.ticks = 1
            if self.on_break == 0:
                self.on_break = (
                    2 if self.till_long_break > self.long_break_gap else 1
                )
                self.box.long_break.show()
                self.box.long_break.set_fraction(
                    self.till_long_break / self.long_break_gap
                )
            else:
                if self.on_break == 2 or self.long_break_gap == 0:
                    self.till_long_break = 0
                else:
                    self.till_long_break += 1
                self.on_break = 0
                self.box.long_break.hide()

            app.player.paused = bool(self.on_break)
        print_d(self._dbug())
        return True

    def create_sidebar(self):
        vbox = Gtk.VBox()
        vbox.pack_start(self.box, False, False, 0)
        self.box.switch.connect("notify::active", self.toggle)
        vbox.show_all()
        return vbox

    def toggle(self, button, gparam):
        if button.get_active():
            self.set_timer()
        else:
            self.kill_timer()

    def set_timer(self):
        if not self.timer:
            self.timer = GLib.timeout_add(self._unit, self._run)
        self.box.switch.set_active(True)
        print_d(self._dbug())

    def enabled(self):
        self.box = PomodoroBox()
        if not app.player.paused:
            self.set_timer()

    def kill_timer(self):
        self.box.switch.set_active(False)
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        print_d(self._dbug())

    def disabled(self):
        self.box.destroy()
        self.kill_timer()

    def plugin_on_unpaused(self):
        if self.on_break:
            self.on_break = 0
            self.ticks = 1
        self.set_timer()

    def plugin_on_paused(self):
        if not self.on_break:
            self.kill_timer()

    @classmethod
    def PluginPreferences(cls, parent):
        t = Gtk.Table(n_rows=2, n_columns=4)
        t.set_col_spacings(6)

        def attach_spin(config, min, max, step, i):
            def update(spin):
                cls._set(config, int(spin.get_value()))

            val = cls._get(config)
            spin = Gtk.SpinButton(
                adjustment=Gtk.Adjustment.new(
                    float(val), min, max, step, step * 2, 0
                )
            )
            lbl = Gtk.Label(label=_(config) + ":")
            lbl.set_mnemonic_widget(spin)
            lbl.set_use_underline(True)
            lbl.set_alignment(0.0, 0.5)
            spin.connect("value-changed", update)
            t.attach(lbl, 0, 1, i, i + 1, xoptions=Gtk.AttachOptions.FILL)
            t.attach(spin, 1, 2, i, i + 1, xoptions=Gtk.AttachOptions.FILL)

        attach_spin("Focus Duration", 5, 50, 5, 0)
        attach_spin("Break Duration", 1, 20, 1, 1)
        attach_spin("Long Break Duration", 5, 50, 5, 2)
        attach_spin("Long Break Gap", 0, 5, 1, 3)

        return t
