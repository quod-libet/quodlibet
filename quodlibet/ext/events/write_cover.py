# Copyright 2005 Joe Wreschnig
#           2018 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import shutil

from gi.repository import Gtk

import quodlibet
from quodlibet import _, print_w
from quodlibet import app
from quodlibet import config
from quodlibet.plugins.events import EventPlugin
from quodlibet.qltk import Icons


def get_path():
    default = os.path.join(quodlibet.get_runtime_dir(), "current.cover")
    return config.get("plugins", __name__, default=default)


def set_path(value):
    config.set("plugins", __name__, value)


class PictureSaver(EventPlugin):
    PLUGIN_ID = "Picture Saver"
    PLUGIN_NAME = _("Picture Saver")
    PLUGIN_DESC = _("Saves the cover image of the current song to a file.")
    PLUGIN_ICON = Icons.DOCUMENT_SAVE

    def plugin_on_song_started(self, song):
        def delete(outfile):
            try:
                os.unlink(outfile)
            except EnvironmentError as e:
                print_w("Couldn't delete '%s' (%s)" % (outfile, e))

        outfile = get_path()
        if song is None:
            delete(outfile)
        else:
            cover = app.cover_manager.get_cover(song)
            if cover is None:
                delete(outfile)
            else:
                with open(outfile, "wb") as f:
                    f.write(cover.read())

    def PluginPreferences(self, parent):
        def changed(entry):
            fn = entry.get_text()
            try:
                shutil.move(get_path(), fn)
            except EnvironmentError:
                pass
            else:
                set_path(fn)

        hb = Gtk.HBox(spacing=6)
        hb.set_border_width(6)
        hb.pack_start(Gtk.Label(label=_("File:")), False, True, 0)
        e = Gtk.Entry()
        e.set_text(get_path())
        e.connect('changed', changed)
        hb.pack_start(e, True, True, 0)
        return hb
