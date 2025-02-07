# Copyright 2006 Lukas Lalinsky
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import struct

import mutagen.asf

from quodlibet.util.path import get_temp_cover_file
from quodlibet import config

from ._audio import AudioFile
from ._image import EmbeddedImage, APICType
from ._misc import AudioFileError, translate_errors


class WMAFile(AudioFile):
    mimes = [
        "audio/x-ms-wma",
        "audio/x-ms-wmv",
        "video/x-ms-asf",
        "audio/x-wma",
        "video/x-wmv",
    ]
    format = "ASF"

    # http://msdn.microsoft.com/en-us/library/dd743066%28VS.85%29.aspx
    # http://msdn.microsoft.com/en-us/library/dd743063%28VS.85%29.aspx
    # http://msdn.microsoft.com/en-us/library/dd743220%28VS.85%29.aspx
    __translate = {
        "WM/AlbumTitle": "album",
        "Title": "title",
        "Author": "artist",
        "WM/AlbumArtist": "albumartist",
        "WM/Composer": "composer",
        "WM/Writer": "lyricist",
        "WM/Conductor": "conductor",
        "WM/ModifiedBy": "remixer",
        "WM/Producer": "producer",
        "WM/ContentGroupDescription": "grouping",
        "WM/SubTitle": "discsubtitle",
        "WM/TrackNumber": "tracknumber",
        "WM/PartOfSet": "discnumber",
        "WM/BeatsPerMinute": "bpm",
        "Copyright": "copyright",
        "WM/ISRC": "isrc",
        "WM/Mood": "mood",
        "WM/EncodedBy": "encodedby",
        "MusicBrainz/Track Id": "musicbrainz_trackid",
        "MusicBrainz/Release Track Id": "musicbrainz_releasetrackid",
        "MusicBrainz/Album Id": "musicbrainz_albumid",
        "MusicBrainz/Artist Id": "musicbrainz_artistid",
        "MusicBrainz/Album Artist Id": "musicbrainz_albumartistid",
        "MusicBrainz/TRM Id": "musicbrainz_trmid",
        "MusicIP/PUID": "musicip_puid",
        "MusicBrainz/Release Group Id": "musicbrainz_releasegroupid",
        "WM/Year": "date",
        "WM/OriginalArtist": "originalartist",
        "WM/OriginalAlbumTitle": "originalalbum",
        "WM/AlbumSortOrder": "albumsort",
        "WM/ArtistSortOrder": "artistsort",
        "WM/AlbumArtistSortOrder": "albumartistsort",
        "WM/Genre": "genre",
        "WM/Publisher": "publisher",
        "WM/AuthorURL": "website",
        "Description": "comment",
    }
    __rtranslate = {v: k for k, v in __translate.items()}

    # http://msdn.microsoft.com/en-us/library/dd743065.aspx
    # note: not all names here are used by QL
    __multi_value_attr = {
        "Author",
        "WM/AlbumArtist",
        "WM/AlbumCoverURL",
        "WM/Category",
        "WM/Composer",
        "WM/Conductor",
        "WM/Director",
        "WM/Genre",
        "WM/GenreID",
        "WM/Language",
        "WM/Lyrics_Synchronised",
        "WM/Mood",
        "WM/Picture",
        "WM/Producer",
        "WM/PromotionURL",
        "WM/UserWebURL",
        "WM/Writer",
    }

    __multi_value_keys = set()
    for k, v in __translate.items():
        if k in __multi_value_attr:
            __multi_value_keys.add(v)

    def __init__(self, filename, audio=None):
        if audio is None:
            with translate_errors():
                audio = mutagen.asf.ASF(filename)
        info = audio.info

        self["~#length"] = info.length
        self["~#bitrate"] = int(info.bitrate / 1000)
        if info.channels:
            self["~#channels"] = info.channels
        self["~#samplerate"] = info.sample_rate

        type_, name, desc = info.codec_type, info.codec_name, info.codec_description

        if type_:
            self["~codec"] = type_
        encoding = "\n".join(filter(None, [name, desc]))
        if encoding:
            self["~encoding"] = encoding

        for name, values in audio.tags.items():
            if name == "WM/Picture":
                self.has_images = True
            try:
                name = self.__translate[name]
            except KeyError:
                continue
            self[name] = "\n".join(map(str, values))
        self.sanitize(filename)

    def write(self):
        with translate_errors():
            audio = mutagen.asf.ASF(self["~filename"])
        for key in self.__translate.keys():
            try:
                del audio[key]
            except KeyError:
                pass

        for key in self.realkeys():
            try:
                name = self.__rtranslate[key]
            except KeyError:
                continue
            audio.tags[name] = self.list(key)
        with translate_errors():
            audio.save(preserve_mtime=config.getboolean("editing", "preserve_mtime"))
        self.sanitize()

    def can_multiple_values(self, key=None):
        if key is None:
            return self.__multi_value_keys
        return key in self.__multi_value_keys

    def can_change(self, key=None):
        ok = self.__rtranslate.keys()
        if key is None:
            return ok
        else:
            return super().can_change(key) and (key in ok)

    def get_images(self):
        images = []

        try:
            tag = mutagen.asf.ASF(self["~filename"])
        except Exception:
            return images

        for image in tag.get("WM/Picture", []):
            try:
                (mime, desc, data, type_) = unpack_image(image.value)
            except ValueError:
                continue
            f = get_temp_cover_file(data, mime)
            images.append(EmbeddedImage(f, mime, type_=type_))

        images.sort(key=lambda c: c.sort_key)
        return images

    def get_primary_image(self):
        """Returns the primary embedded image or None"""

        try:
            tag = mutagen.asf.ASF(self["~filename"])
        except Exception:
            return

        for image in tag.get("WM/Picture", []):
            try:
                (mime, desc, data, type_) = unpack_image(image.value)
            except ValueError:
                continue
            if type_ == APICType.COVER_FRONT:  # Only cover images
                f = get_temp_cover_file(data, mime)
                return EmbeddedImage(f, mime, type_=type_)

    can_change_images = True

    def clear_images(self):
        """Delete all embedded images"""

        with translate_errors():
            tag = mutagen.asf.ASF(self["~filename"])
            tag.pop("WM/Picture", None)
            tag.save(preserve_mtime=config.getboolean("editing", "preserve_mtime"))

        self.has_images = False

    def set_image(self, image):
        """Replaces all embedded images by the passed image"""

        with translate_errors():
            tag = mutagen.asf.ASF(self["~filename"])

        try:
            imagedata = image.read()
        except OSError as e:
            raise AudioFileError(e) from e

        # thumbnail gets used in WMP..
        data = pack_image(image.mime_type, "thumbnail", imagedata, APICType.COVER_FRONT)

        value = mutagen.asf.ASFValue(data, mutagen.asf.BYTEARRAY)
        tag["WM/Picture"] = [value]

        with translate_errors():
            tag.save(preserve_mtime=config.getboolean("editing", "preserve_mtime"))

        self.has_images = True


def unpack_image(data):
    """
    Helper function to unpack image data from a WM/Picture tag.

    The data has the following format:
    1 byte: Picture type (0-20), see ID3 APIC frame specification at
    http://www.id3.org/id3v2.4.0-frames
    4 bytes: Picture data length in LE format
    MIME type, null terminated UTF-16-LE string
    Description, null terminated UTF-16-LE string
    The image data in the given length
    """

    try:
        (type_, size) = struct.unpack_from("<bi", data)
    except struct.error as e:
        raise ValueError(e) from e
    data = data[5:]

    mime = b""
    while data:
        char, data = data[:2], data[2:]
        if char == b"\x00\x00":
            break
        mime += char
    else:
        raise ValueError("mime: missing data")

    mime = mime.decode("utf-16-le")

    description = b""
    while data:
        char, data = data[:2], data[2:]
        if char == b"\x00\x00":
            break
        description += char
    else:
        raise ValueError("desc: missing data")

    description = description.decode("utf-16-le")

    if size != len(data):
        raise ValueError("image data size mismatch")

    return (mime, description, data, type_)


def pack_image(mime, description, imagedata, type_):
    assert APICType.is_valid(type_)

    size = len(imagedata)
    data = struct.pack("<bi", type_, size)
    data += mime.encode("utf-16-le") + b"\x00\x00"
    data += description.encode("utf-16-le") + b"\x00\x00"
    data += imagedata

    return data


loader = WMAFile
types = [WMAFile]
extensions = [".wma", ".asf", ".wmv"]
