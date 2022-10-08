# Copyright 2017 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import platform

from gi.repository import Gtk, Pango
from senf import fsn2uri, fsn2text
import mutagen

from quodlibet import _
from quodlibet.qltk import Icons
from quodlibet.util.path import unexpand
from quodlibet.plugins.events import EventPlugin
from quodlibet import formats
from quodlibet import app, get_config_dir, get_data_dir, \
        get_runtime_dir, get_cache_dir
from quodlibet.util import fver, escape
from quodlibet.qltk import gtk_version, pygobject_version, get_backend_name, \
    get_font_backend_name
from quodlibet.qltk import show_uri


class AppInformation(EventPlugin):
    PLUGIN_ID = "AppInformation"
    PLUGIN_NAME = _("Application Information")
    PLUGIN_DESC = _("Various information about the application and its "
                    "environment.")
    PLUGIN_CAN_ENABLE = True
    PLUGIN_ICON = Icons.PREFERENCES_SYSTEM

    def PluginPreferences(self, *args):
        vb = Gtk.VBox()

        row = 0
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)

        def label_title(text):
            l = Gtk.Label(label=text, xalign=1, yalign=0, wrap=True,
                          justify=Gtk.Justification.RIGHT, selectable=True)
            l.get_style_context().add_class(Gtk.STYLE_CLASS_DIM_LABEL)
            return l

        def label_value(text):
            return Gtk.Label(label=text, wrap=True, xalign=0, yalign=0,
                             width_chars=25, selectable=True)

        def label_path(path):
            l = Gtk.Label(label="<a href='%s'>%s</a>" % (
                            fsn2uri(path), escape(fsn2text(unexpand(path)))),
                          use_markup=True,
                          ellipsize=Pango.EllipsizeMode.MIDDLE,
                          xalign=0,
                          selectable=True)

            l.connect("activate-link", show_uri)
            return l

        grid.insert_row(row)
        l = label_title(_("Supported Formats"))
        format_names = sorted([t.format for t in formats.types])
        v = label_value(", ".join(format_names))
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        grid.insert_row(row)
        l = label_title(_("Configuration Directory"))
        v = label_path(get_config_dir())
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        grid.insert_row(row)
        l = label_title(_("Data Directory"))
        v = label_path(get_data_dir())
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        grid.insert_row(row)
        l = label_title(_("Cache Directory"))
        v = label_path(get_cache_dir())
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        grid.insert_row(row)
        l = label_title(_("Runtime Directory"))
        v = label_path(get_runtime_dir())
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        grid.insert_row(row)
        l = label_title(_("Audio Backend"))
        v = label_value("%s\n%s" % (app.player.name, app.player.version_info))
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        grid.insert_row(row)
        l = label_title("Python")
        v = label_value(platform.python_version())
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        grid.insert_row(row)
        l = label_title("Mutagen")
        v = label_value(fver(mutagen.version))
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        grid.insert_row(row)
        l = label_title("Gtk+")
        v = label_value("%s (%s, %s)" % (
            fver(gtk_version), get_backend_name(), get_font_backend_name()))
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        grid.insert_row(row)
        l = label_title("PyGObject")
        v = label_value(fver(pygobject_version))
        grid.attach(l, 0, row, 1, 1)
        grid.attach(v, 1, row, 1, 1)
        row += 1

        vb.pack_start(grid, True, True, 0)
        vb.show_all()

        return vb
