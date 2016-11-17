# -*- coding: utf-8 -*-
# Copyright 2012 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

from mutagen.smf import SMF

from ._audio import AudioFile, translate_errors


class MidiError(Exception):
    pass


class MidiFile(AudioFile):
    format = "MIDI"
    mimes = ["audio/midi", "audio/x-midi"]

    def __init__(self, filename):
        with translate_errors():
            audio = SMF(filename)
        self["~#length"] = audio.info.length
        self.sanitize(filename)

    def write(self):
        pass

    def reload(self, *args):
        title = self.get("title")
        super(MidiFile, self).reload(*args)
        if title is not None:
            self.setdefault("title", title)

    def can_change(self, k=None):
        if k is None:
            return ["title"]
        else:
            return k == "title"

loader = MidiFile
types = [MidiFile]
extensions = [".mid"]
