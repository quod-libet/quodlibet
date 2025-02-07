# Copyright 2023 Dmitry Savosh
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from quodlibet import _
from quodlibet.qltk import Icons
from quodlibet.plugins.songsmenu import SongsMenuPlugin
from quodlibet.plugins.songshelpers import any_song, is_writable


class MusicBeeRatingExport(SongsMenuPlugin):
    PLUGIN_ID = "MusicBee Rating Export"
    PLUGIN_NAME = _("MusicBee Rating Export")
    PLUGIN_DESC = _("This plugin exports rating in MusicBee tags. "
                    "New tags are written in each selected file. "
                    "The original rating tags remain unchanged.")
    PLUGIN_ICON = Icons.DOCUMENT_SAVE

    plugin_handles = any_song(is_writable)

    def plugin_song(self, song):
        try:
            song["rating"] = int(song["~#rating"]*100)
            song._needs_write = True
        except Exception as e:
            print(e)
            return

class MusicBeeRatingImport(SongsMenuPlugin):
    PLUGIN_ID = "MusicBee Rating Import"
    PLUGIN_NAME = _("MusicBee Rating Import")
    PLUGIN_DESC = _("This plugin imports rating from MusicBee tags. "
                    "Rating tags will be updated in each selected file.")
    PLUGIN_ICON = Icons.DOCUMENT_SAVE

    plugin_handles = any_song(is_writable)

    def plugin_song(self, song):
        audio = song.MutagenType(song["~filename"])
        tags = audio.tags

        try:
            song["~#rating"] = int(tags["rating"][0])/100
            song._needs_write = True
        except Exception as e:
            print(e)
            return
        