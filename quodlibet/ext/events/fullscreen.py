# Copyright 2023 Ivan Kuchin
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from quodlibet import _
from quodlibet import app
from quodlibet.plugins.events import EventPlugin
from quodlibet.qltk import Icons

class FullscreenPlugin(EventPlugin):
    PLUGIN_ID = "Fullscreen"
    PLUGIN_NAME = _("Fullscreen")
    PLUGIN_DESC = _("Make the window fullscreen")
    PLUGIN_ICON = Icons.EDIT

    def enabled(self):
        app.window.fullscreen()

    def disabled(self):
        app.window.unfullscreen()
