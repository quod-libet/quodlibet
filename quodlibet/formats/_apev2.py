# Copyright 2004-2005 Joe Wreschnig, Michael Urman
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import mutagen.apev2

from quodlibet.util.path import get_temp_cover_file
from quodlibet import config

from ._audio import AudioFile
from ._image import APICType, EmbeddedImage
from ._misc import AudioFileError, translate_errors


def get_cover_type(key, value):
    """Returns an APICType or None if the tag isn't an image"""

    if value.kind != mutagen.apev2.BINARY:
        return

    type_map = {
        "cover art (front)": APICType.COVER_FRONT,
        "cover art (back)": APICType.COVER_BACK,
    }

    return type_map.get(key.lower())


def parse_cover(key, value):
    """Returns a EmbeddedImage or None"""

    # http://www.hydrogenaud.io/forums/index.php?showtopic=40603

    cover_type = get_cover_type(key, value)
    if cover_type is None:
        return

    parts = value.value.split(b"\x00", 1)
    if len(parts) != 2:
        return

    f = get_temp_cover_file(parts[-1])
    if not f:
        return

    return EmbeddedImage(f, "image/", type_=cover_type)


def write_cover(image):
    """Takes EmbeddedImage and returns a (key, value) tuple

    Raises:
        AudioFileError
    """

    if image.type == APICType.COVER_FRONT:
        key = "Cover Art (Front)"
    else:
        key = "Cover Art (Back)"

    try:
        data = image.read()
    except OSError as e:
        raise AudioFileError(e) from e

    ext = (image.extensions and image.extensions[0]) or "jpg"
    data = f"hello.{ext}\x00".encode("ascii") + data

    value = mutagen.apev2.APEValue(data, mutagen.apev2.BINARY)

    return (key, value)


class APEv2File(AudioFile):
    # Map APE names to QL names. APE tags are also usually capitalized.
    # Also blacklist a number of tags.
    IGNORE = ["file", "index", "introplay", "dummy"]
    TRANS = {
        "subtitle": "version",
        "track": "tracknumber",
        "disc": "discnumber",
        "catalog": "labelid",
        "year": "date",
        "record location": "location",
        "album artist": "albumartist",
        "debut album": "originalalbum",
        "record date": "recordingdate",
        "original artist": "originalartist",
        "mixartist": "remixer",
    }
    SNART = {v: k for k, v in TRANS.items()}

    can_change_images = True

    def __init__(self, filename, audio=None):
        if audio:
            tag = audio.tags or {}
        else:
            with translate_errors():
                try:
                    tag = mutagen.apev2.APEv2(filename)
                except mutagen.apev2.APENoHeaderError:
                    tag = {}

        for key, value in tag.items():
            if get_cover_type(key, value) is not None:
                self.has_images = True

            key = self.TRANS.get(key.lower(), key.lower())
            if value.kind == mutagen.apev2.TEXT and key not in self.IGNORE:
                self[key] = "\n".join(list(value))

        self.sanitize(filename)

    @staticmethod
    def __titlecase(key):
        if key.lower() in ["isrc", "isbn", "ean/upc"]:
            return key.upper()
        else:
            return key.title()

    def can_change(self, key=None):
        if key is None:
            return True
        else:
            return (
                super().can_change(key)
                and key.lower() not in self.IGNORE
                and key.lower() not in self.TRANS
                and mutagen.apev2.is_valid_apev2_key(self.__titlecase(key))
            )

    def write(self):
        with translate_errors():
            try:
                tag = mutagen.apev2.APEv2(self["~filename"])
            except mutagen.apev2.APENoHeaderError:
                tag = mutagen.apev2.APEv2()

        # Remove any text keys we read in
        for key in list(tag.keys()):
            value = tag[key]
            if value.kind == mutagen.apev2.TEXT and key.lower() not in self.IGNORE:
                del tag[key]

        # Write all tags we have
        lower = self.as_lowercased()
        for key in lower.realkeys():
            new_key = self.SNART.get(key, key)
            if new_key in self.IGNORE:
                continue
            new_key = self.__titlecase(new_key)
            tag[new_key] = lower.list(key)

        with translate_errors():
            tag.save(self["~filename"])
        self.sanitize()

    def get_primary_image(self):
        try:
            tag = mutagen.apev2.APEv2(self["~filename"])
        except Exception:
            return

        primary = None
        for key, value in tag.items():
            primary = (key, value)
            cover_type = get_cover_type(key, value)
            if cover_type == APICType.COVER_FRONT:
                break

        if primary is not None:
            return parse_cover(*primary)

    def get_images(self):
        try:
            tag = mutagen.apev2.APEv2(self["~filename"])
        except Exception:
            return []

        images = []
        for key, value in tag.items():
            image = parse_cover(key, value)
            if image is not None:
                images.append(image)

        images.sort(key=lambda c: c.sort_key)
        return images

    def clear_images(self):
        with translate_errors():
            try:
                tag = mutagen.apev2.APEv2(self["~filename"])
            except mutagen.apev2.APENoHeaderError:
                return

            for key, value in tag.items():
                cover_type = get_cover_type(key, value)
                if cover_type is not None:
                    del tag[key]

            tag.save(preserve_mtime=config.getboolean("editing", "preserve_mtime"))

        self.has_images = False

    def set_image(self, image):
        with translate_errors():
            try:
                tag = mutagen.apev2.APEv2(self["~filename"])
            except mutagen.apev2.APENoHeaderError:
                tag = mutagen.apev2.APEv2()

        for key, value in tag.items():
            cover_type = get_cover_type(key, value)
            if cover_type is not None:
                del tag[key]

        key, value = write_cover(image)
        tag[key] = value
        with translate_errors():
            tag.save(self["~filename"])

        self.has_images = True
