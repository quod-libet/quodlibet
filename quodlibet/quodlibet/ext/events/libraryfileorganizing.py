# -*- coding: utf-8 -*-
# Copyright 2022 Jonathan Schmeling
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import re
import shutil
from gi.repository import Gtk
from quodlibet import _
from quodlibet import app
from quodlibet import util
from quodlibet import qltk
from quodlibet.qltk.entry import UndoEntry
from quodlibet.pattern import Pattern
from quodlibet.plugins import PluginConfigMixin
from quodlibet.plugins.events import EventPlugin
from quodlibet.util.library import get_scan_dirs
from quodlibet.util.library import scan_library


class MyPlugin(EventPlugin, PluginConfigMixin):
    PLUGIN_ID = "libraryfileorganizing"
    PLUGIN_NAME = _("Auto-Update File Locations")
    PLUGIN_DESC = _("After making any update (namely, to tags), update "
                    "the file to a new location/name based on its tags in "
                    "a form that you specify. This is most useful for "
                    "automatically keeping organized a central music "
                    "directory.")

    DEFAULT_PAT = "~/Music/<albumartist|<albumartist>|" + \
                  "<artist|<artist>|Unknown Artist>>/" + \
                  "<album|<album>|Unknown Album>/" + \
                  "<albumartist|<albumartist>|" + \
                  "<artist|<artist>|Unknown Artist>> " + \
                  "(<album|<album>|Unknown Album>) - " + \
                  "<~#track|<~#track><~#disc| (<~#disc>)> - >" + \
                  "<title|<title>|Unknown Title>>"
    CFG_PAT_PLAYING = "naming_pattern"

    def plugin_on_changed(self, songs):
        song = songs[0]
        current_location = song("~filename")
        file_split = os.path.splitext(current_location)
        file_extension = file_split[1]
        psbl_new_location = os.path.abspath(
            os.path.expanduser((Pattern(self.config_get(self.CFG_PAT_PLAYING,
                                                        self.DEFAULT_PAT)) %
                                song) + file_extension))

        if(not(psbl_new_location == re.sub(" \\([0-9]+\\)$",
                                           "",
                                           file_split[0]) + file_extension)):
            dirname = os.path.dirname(psbl_new_location)
            if not os.path.exists(dirname):
                os.makedirs(dirname)

            if os.path.isfile(psbl_new_location):
                num_to_use = 1
                start_with = os.path.splitext(
                    os.path.basename(psbl_new_location))[0]
                length_chk = len(start_with) + 2

                for f in sorted(os.listdir(dirname)):
                    if f.startswith(start_with):
                        temporary = f[length_chk:]

                        if temporary[:temporary.find(")")] == str(num_to_use):
                            num_to_use += 1

                psbl_new_location = os.path.join(dirname,
                                                 start_with +
                                                 " (" + str(num_to_use) + ")" +
                                                 file_extension)

            try:
	            shutil.move(current_location, os.path.normpath(psbl_new_location))

	            self.removeEmptyDirs(os.path.dirname(current_location))

	            scan_library(app.library, False)
            except Exception:

    def PluginPreferences(self, parent):
        outer_vb = Gtk.VBox(spacing=12)
        vb = Gtk.VBox(spacing=12)

        # Naming Pattern
        hb = Gtk.HBox(spacing=6)
        entry = UndoEntry()
        entry.set_text(self.config_get(self.CFG_PAT_PLAYING,
                                       self.DEFAULT_PAT))
        entry.connect('changed',
                      self.config_entry_changed,
                      self.CFG_PAT_PLAYING)
        lbl = Gtk.Label(label=_("Naming pattern:"))
        entry.set_tooltip_markup(_("File path based off of tags "
                                   "to move a file after update. "
                                   "Accepts QL Patterns e.g. %s") %
                                 util.monospace(util.escape("<~artist" +
                                                            "~title>")))
        lbl.set_mnemonic_widget(entry)
        hb.pack_start(lbl, False, True, 0)
        hb.pack_start(entry, True, True, 0)
        vb.pack_start(hb, True, True, 0)

        # Frame
        frame = qltk.Frame(_("New File Location/Name"), child=vb)
        outer_vb.pack_start(frame, False, True, 0)

        return outer_vb

    def removeEmptyDirs(self, directory):
        if directory in get_scan_dirs():
            return
        elif not os.listdir(directory):
            os.rmdir(directory)
            self.removeEmptyDirs(os.path.abspath(os.path.join(directory,
                                                              os.pardir)))
