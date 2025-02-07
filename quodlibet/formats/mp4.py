# Copyright 2005 Alexey Bobyakov <claymore.ws@gmail.com>, Joe Wreschnig
# Copyright 2006 Lukas Lalinsky
#      2020,2025 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from mutagen.mp4 import MP4, MP4Cover

from quodlibet.util.path import get_temp_cover_file
from quodlibet.util.string import decode
from quodlibet import config

from ._audio import AudioFile
from ._misc import AudioFileError, translate_errors
from ._image import EmbeddedImage

ITUNES: str = "----:com.apple.iTunes"


class MP4File(AudioFile):
    format = "MPEG-4"
    mimes = ["audio/mp4", "audio/x-m4a", "audio/mpeg4", "audio/aac"]

    __translate = {
        "\xa9nam": "title",
        "\xa9alb": "album",
        "\xa9ART": "artist",
        "aART": "albumartist",
        "\xa9wrt": "composer",
        "\xa9day": "date",
        "\xa9cmt": "comment",
        "\xa9grp": "grouping",
        "\xa9gen": "genre",
        "tmpo": "bpm",
        "\xa9too": "encodedby",  # FIXME: \xa9enc should be encodedby
        "desc": "description",  # (usually used in podcasts)
        "cprt": "copyright",
        "soal": "albumsort",
        "soaa": "albumartistsort",
        "soar": "artistsort",
        "sonm": "titlesort",
        "soco": "composersort",
        f"{ITUNES}:CONDUCTOR": "conductor",
        f"{ITUNES}:DISCSUBTITLE": "discsubtitle",
        f"{ITUNES}:LANGUAGE": "language",
        f"{ITUNES}:MOOD": "mood",
        f"{ITUNES}:MusicBrainz Artist Id": "musicbrainz_artistid",
        f"{ITUNES}:MusicBrainz Track Id": "musicbrainz_trackid",
        f"{ITUNES}:MusicBrainz Release Track Id": "musicbrainz_releasetrackid",
        f"{ITUNES}:MusicBrainz Album Id": "musicbrainz_albumid",
        f"{ITUNES}:MusicBrainz Album Artist Id": "musicbrainz_albumartistid",
        f"{ITUNES}:MusicIP PUID": "musicip_puid",
        f"{ITUNES}:MusicBrainz Album Status": "musicbrainz_albumstatus",
        f"{ITUNES}:MusicBrainz Album Type": "musicbrainz_albumtype",
        f"{ITUNES}:MusicBrainz Album Release Country": "releasecountry",
        f"{ITUNES}:MusicBrainz Release Group Id": "musicbrainz_releasegroupid",
        f"{ITUNES}:replaygain_album_gain": "replaygain_album_gain",
        f"{ITUNES}:replaygain_album_peak": "replaygain_album_peak",
        f"{ITUNES}:replaygain_track_gain": "replaygain_track_gain",
        f"{ITUNES}:replaygain_track_peak": "replaygain_track_peak",
        f"{ITUNES}:replaygain_reference_loudness": "replaygain_reference_loudness",
    }
    __rtranslate = {v: k for k, v in __translate.items()}

    __tupletranslate = {
        "disk": "discnumber",
        "trkn": "tracknumber",
    }
    __rtupletranslate = {v: k for k, v in __tupletranslate.items()}

    def __init__(self, filename):
        with translate_errors():
            audio = MP4(filename)
        self["~codec"] = audio.info.codec_description
        self["~#length"] = audio.info.length
        self["~#bitrate"] = int(audio.info.bitrate / 1000)
        if audio.info.channels:
            self["~#channels"] = audio.info.channels
        self["~#samplerate"] = audio.info.sample_rate
        self["~#bitdepth"] = audio.info.bits_per_sample

        for key, values in audio.items():
            if key in self.__tupletranslate:
                if values:
                    name = self.__tupletranslate[key]
                    cur, total = values[0]
                    if total:
                        self[name] = f"{cur:d}/{total:d}"
                    else:
                        self[name] = str(cur)
            elif key in self.__translate:
                name = self.__translate[key]
                if key == "tmpo":
                    self[name] = "\n".join(map(str, values))
                elif key.startswith("----"):
                    self[name] = "\n".join(decode(v).strip("\x00") for v in values)
                else:
                    self[name] = "\n".join(values)
            elif key == "covr":
                self.has_images = True
        self.sanitize(filename)

    def write(self):
        with translate_errors():
            audio = MP4(self["~filename"])

        for key in list(self.__translate.keys()) + list(self.__tupletranslate.keys()):
            try:
                del audio[key]
            except KeyError:
                pass

        for key in self.realkeys():
            try:
                name = self.__rtranslate[key]
            except KeyError:
                continue
            values = self.list(key)
            if name == "tmpo":
                values = [int(round(float(v))) for v in values]
            elif name.startswith("----"):
                values = [v.encode("utf-8") for v in values]
            audio[name] = values
        track, tracks = self("~#track"), self("~#tracks", 0)
        if track:
            audio["trkn"] = [(track, tracks)]
        disc, discs = self("~#disc"), self("~#discs", 0)
        if disc:
            audio["disk"] = [(disc, discs)]
        with translate_errors():
            audio.save(preserve_mtime=config.getboolean("editing", "preserve_mtime"))
        self.sanitize()

    def can_multiple_values(self, key=None):
        if key is None:
            return []
        return False

    def can_change(self, key=None):
        ok = list(self.__rtranslate.keys()) + list(self.__rtupletranslate.keys())
        if key is None:
            return ok
        else:
            return super().can_change(key) and (key in ok)

    def get_images(self):
        images = []

        try:
            tag = MP4(self["~filename"])
        except Exception:
            return []

        for cover in tag.get("covr", []):
            if cover.imageformat == MP4Cover.FORMAT_JPEG:
                mime = "image/jpeg"
            elif cover.imageformat == MP4Cover.FORMAT_PNG:
                mime = "image/png"
            else:
                mime = "image/"

            f = get_temp_cover_file(cover, mime)
            images.append(EmbeddedImage(f, mime))

        return images

    def get_primary_image(self):
        try:
            tag = MP4(self["~filename"])
        except Exception:
            return

        for cover in tag.get("covr", []):
            if cover.imageformat == MP4Cover.FORMAT_JPEG:
                mime = "image/jpeg"
            elif cover.imageformat == MP4Cover.FORMAT_PNG:
                mime = "image/png"
            else:
                mime = "image/"

            f = get_temp_cover_file(cover, mime)
            return EmbeddedImage(f, mime)

    can_change_images = True

    def clear_images(self):
        """Delete all embedded images"""

        with translate_errors():
            tag = MP4(self["~filename"])
            tag.pop("covr", None)
            tag.save(preserve_mtime=config.getboolean("editing", "preserve_mtime"))

        self.has_images = False

    def set_image(self, image):
        """Replaces all embedded images by the passed image"""

        if image.mime_type == "image/jpeg":
            image_format = MP4Cover.FORMAT_JPEG
        elif image.mime_type == "image/png":
            image_format = MP4Cover.FORMAT_PNG
        else:
            raise AudioFileError(f"mp4: Unsupported image format {image.mime_type!r}")

        with translate_errors():
            tag = MP4(self["~filename"])

        try:
            data = image.read()
        except OSError:
            return

        cover = MP4Cover(data, image_format)
        tag["covr"] = [cover]

        with translate_errors():
            tag.save(preserve_mtime=config.getboolean("editing", "preserve_mtime"))

        self.has_images = True


loader = MP4File
types = [MP4File]
extensions = [".mp4", ".m4a", ".m4b", ".m4v", ".3gp", ".3g2", ".3gp2"]
