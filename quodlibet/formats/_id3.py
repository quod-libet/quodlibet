# Copyright 2004-2020 Joe Wreschnig, Michael Urman, Niklas Janlert,
#                     Steven Robertson, Nick Boultbee, h88e22dgpeps56sg
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.


import mutagen.id3

from quodlibet import config, const, print_w
from quodlibet import util
from quodlibet.util.iso639 import ISO_639_2
from quodlibet.util.path import get_temp_cover_file
from quodlibet.util.string import isascii
from ._audio import AudioFile, translate_errors, AudioFileError
from ._image import EmbeddedImage, APICType


def encoding_for(s):
    """Returns ID3 encoding ID best for string `s`"""
    return 3 if isascii(s) else 1


RG_KEYS = {
    "replaygain_track_peak", "replaygain_track_gain",
    "replaygain_album_peak", "replaygain_album_gain",
}


# ID3 is absolutely the worst thing ever.
class ID3File(AudioFile):
    supports_rating_and_play_count_in_file = True

    # http://www.unixgods.org/~tilo/ID3/docs/ID3_comparison.html
    # http://www.id3.org/id3v2.4.0-frames.txt
    IDS = {"TIT1": "grouping",
           "TIT2": "title",
           "TIT3": "version",
           "TPE1": "artist",
           "TPE2": "performer",
           "TPE3": "conductor",
           "TPE4": "arranger",
           "TEXT": "lyricist",
           "TCOM": "composer",
           "TENC": "encodedby",
           "TALB": "album",
           "TRCK": "tracknumber",
           "TPOS": "discnumber",
           "TSRC": "isrc",
           "TCOP": "copyright",
           "TPUB": "organization",
           "TSST": "discsubtitle",
           "TOLY": "author",
           "TMOO": "mood",
           "TBPM": "bpm",
           "TDRC": "date",
           "TDOR": "originaldate",
           "TDRL": "releasedate",
           "TOAL": "originalalbum",
           "TOPE": "originalartist",
           "WOAR": "website",
           "TSOP": "artistsort",
           "TSOA": "albumsort",
           "TSOT": "titlesort",
           "TSO2": "albumartistsort",
           "TSOC": "composersort",
           "TMED": "media",
           "TCMP": "compilation",
           "TKEY": "initialkey",
           # TLAN requires an ISO 639-2 language code, check manually
           #"TLAN": "language"
    }
    SDI = {v: k for k, v in IDS.items()}

    # At various times, information for this came from
    # http://musicbrainz.org/docs/specs/metadata_tags.html
    # http://bugs.musicbrainz.org/ticket/1383
    # http://musicbrainz.org/doc/MusicBrainzTag
    TXXX_MAP = {
        "MusicBrainz Release Group Id": "musicbrainz_releasegroupid",
        "MusicBrainz Release Track Id": "musicbrainz_releasetrackid",
        "MusicBrainz Artist Id": "musicbrainz_artistid",
        "MusicBrainz Album Id": "musicbrainz_albumid",
        "MusicBrainz Album Artist Id": "musicbrainz_albumartistid",
        "MusicBrainz TRM Id": "musicbrainz_trmid",
        "MusicIP PUID": "musicip_puid",
        "MusicMagic Fingerprint": "musicip_fingerprint",
        "MusicBrainz Album Status": "musicbrainz_albumstatus",
        "MusicBrainz Album Type": "musicbrainz_albumtype",
        "MusicBrainz Album Release Country": "releasecountry",
        "MusicBrainz Disc Id": "musicbrainz_discid",
        "ASIN": "asin",
        "ALBUMARTISTSORT": "albumartistsort",
        "BARCODE": "barcode",
        }
    PAM_XXXT = {v: k for k, v in TXXX_MAP.items()}

    Kind: type[mutagen.FileType] | None = None

    def __init__(self, filename):
        with translate_errors():
            audio = self.Kind(filename)
        if audio.tags is None:
            audio.add_tags()
        tag = audio.tags

        self._parse_info(audio.info)

        for frame in tag.values():
            if frame.FrameID == "APIC" and len(frame.data):
                self.has_images = True
                continue
            elif frame.FrameID == "TCON":
                self["genre"] = "\n".join(frame.genres)
                continue
            elif (frame.FrameID == "UFID" and
                  frame.owner == "http://musicbrainz.org"):
                self["musicbrainz_trackid"] = frame.data.decode("utf-8", "replace")
                continue
            elif frame.FrameID == "POPM":
                rating = frame.rating / 255.0
                if frame.email == const.EMAIL:
                    try:
                        self.setdefault("~#playcount", frame.count)
                    except AttributeError:
                        pass
                    self.setdefault("~#rating", rating)
                elif frame.email == config.get("editing", "save_email"):
                    try:
                        self["~#playcount"] = frame.count
                    except AttributeError:
                        pass
                    self["~#rating"] = rating
                continue
            elif frame.FrameID == "COMM" and frame.desc == "":
                name = "comment"
            elif frame.FrameID in ["COMM", "TXXX"]:
                if frame.desc.startswith("QuodLibet::"):
                    name = frame.desc[11:]
                elif frame.desc in self.TXXX_MAP:
                    name = self.TXXX_MAP[frame.desc]
                else:
                    continue
            elif frame.FrameID == "RVA2":
                self.__process_rg(frame)
                continue
            elif frame.FrameID == "TMCL":
                for role, name in frame.people:
                    key = self.__validate_name("performer:" + role)
                    if key:
                        self.add(key, name)
                continue
            elif frame.FrameID == "TLAN":
                self["language"] = "\n".join(frame.text)
                continue
            elif frame.FrameID == "USLT":
                name = "lyrics"
            else:
                name = self.IDS.get(frame.FrameID, "").lower()

            name = self.__validate_name(name)
            if not name:
                continue
            name = name.lower()

            id3id = frame.FrameID
            if id3id.startswith("T"):
                text = "\n".join(map(str, frame.text))
            elif id3id == "COMM":
                text = "\n".join(frame.text)
            elif id3id == "USLT":
                # lyrics are single string, not list
                text = frame.text
                self["~lyricsdescription"] = frame.desc
                self["~lyricslanguage"] = frame.lang
            elif id3id.startswith("W"):
                text = frame.url
                frame.encoding = 0
            else:
                continue

            if not text:
                continue
            text = self.__distrust_latin1(text, frame.encoding)
            if text is None:
                continue

            if name in self:
                self[name] += "\n" + text
            else:
                self[name] = text
            self[name] = self[name].strip()

            # to catch a missing continue above
            del name

        # foobar2000 writes long dates in a TXXX DATE tag, leaving the TDRC
        # tag out. Read the TXXX DATE, but only if the TDRC tag doesn't exist
        # to avoid reverting or duplicating tags in existing libraries.
        if audio.tags and "date" not in self:
            for frame in tag.getall("TXXX:DATE"):
                self["date"] = "\n".join(map(str, frame.text))

        # Read TXXX replaygain and replace previously read values from RVA2
        for frame in tag.getall("TXXX"):
            k = frame.desc.lower()
            if k in RG_KEYS:
                self[str(k)] = "\n".join(map(str, frame.text))

        self.sanitize(filename)

    def _parse_info(self, info):
        """Optionally implement in subclasses"""

        pass

    def __validate_name(self, k):
        """Returns an ascii string or None if the key isn't supported"""

        if not k or "=" in k or "~" in k:
            return

        if not (k and "=" not in k and "~" not in k
                and k.encode("ascii", "replace").decode("ascii") == k):
            return

        return k

    def __process_rg(self, frame):
        if frame.channel == 1:
            if frame.desc == "album":
                k = "album"
            elif frame.desc == "track":
                k = "track"
            elif "replaygain_track_gain" not in self:
                k = "track"  # fallback
            else:
                return
            self["replaygain_%s_gain" % k] = "%+f dB" % frame.gain
            self["replaygain_%s_peak" % k] = str(frame.peak)

    @util.cached_property
    def CODECS(self):  # noqa
        codecs = ["utf-8"]
        codecs_conf = config.get("editing", "id3encoding")
        codecs.extend(codecs_conf.strip().split())
        codecs.append("iso-8859-1")
        return codecs

    def __distrust_latin1(self, text, encoding):
        assert isinstance(text, str)
        if encoding == 0:
            try:
                text = text.encode("iso-8859-1")
            except UnicodeEncodeError:
                # mutagen might give us text not matching the encoding
                # https://github.com/quodlibet/mutagen/issues/307
                return text
            for codec in self.CODECS:
                try:
                    text = text.decode(codec)
                except (UnicodeError, LookupError):
                    pass
                else:
                    break
            else:
                return None
        return text

    def has_rating_and_playcount_in_file(self, email):
        with translate_errors():
            audio = self.Kind(self["~filename"])
        if audio.tags is None:
            return False
        return ("POPM:" + email) in audio.tags

    def write(self):
        with translate_errors():
            audio = self.Kind(self["~filename"])

        if audio.tags is None:
            audio.add_tags()
        tag = audio.tags

        # prefill TMCL with the ones we can't read
        mcl = tag.get("TMCL", mutagen.id3.TMCL(encoding=3, people=[]))
        mcl.people = [(r, n) for (r, n) in mcl.people
                      if not self.__validate_name(r)]

        # delete all TXXX/COMM we can read except empty COMM
        for frame in ["COMM:", "TXXX:"]:
            for t in tag.getall(frame + "QuodLibet:"):
                if t.desc and self.__validate_name(t.desc):
                    del tag[t.HashKey]

        for key in ["UFID:http://musicbrainz.org",
                    "TMCL",
                    "POPM:%s" % const.EMAIL,
                    "POPM:%s" % config.get("editing", "save_email")]:
            if key in tag:
                del(tag[key])

        for key, id3name in self.SDI.items():
            tag.delall(id3name)
            if key not in self:
                continue
            enc = encoding_for(self[key])
            frame_cls = mutagen.id3.Frames[id3name]
            text = self[key].split("\n")
            if id3name == "WOAR":
                for t in text:
                    tag.add(frame_cls(url=t))
            else:
                tag.add(frame_cls(encoding=enc, text=text))

        dont_write = (RG_KEYS
                      | set(self.TXXX_MAP.values())
                      | {"genre", "comment", "musicbrainz_trackid", "lyrics"})

        if "musicbrainz_trackid" in self.realkeys():
            f = mutagen.id3.UFID(
                owner="http://musicbrainz.org",
                data=self["musicbrainz_trackid"].encode("utf-8"))
            tag.add(f)

        # Issue 439 - Only write valid ISO 639-2 codes to TLAN (else TXXX)
        tag.delall("TLAN")
        if "language" in self:
            langs = self["language"].split("\n")
            if all(lang in ISO_639_2 for lang in langs):
                # Save value(s) to TLAN tag. Guaranteed to be ASCII here
                tag.add(mutagen.id3.TLAN(encoding=3, text=langs))
                dont_write.add("language")
            else:
                print_w(
                    f"Not using invalid language {self['language']!r} in TLAN")

        # Filter out known keys, and ones set not to write [generically].
        dont_write |= self.SDI.keys()
        keys_to_write = (k for k in self.realkeys()
                         if k not in dont_write)
        for key in keys_to_write:
            enc = encoding_for(self[key])
            if key.startswith("performer:"):
                mcl.people.append((key.split(":", 1)[1], self[key]))
                continue

            f = mutagen.id3.TXXX(
                encoding=enc, text=self[key].split("\n"),
                desc="QuodLibet::%s" % key)
            tag.add(f)

        if mcl.people:
            tag.add(mcl)

        if "genre" in self:
            enc = encoding_for(self["genre"])
            t = self["genre"].split("\n")
            tag.add(mutagen.id3.TCON(encoding=enc, text=t))
        else:
            try:
                del(tag["TCON"])
            except KeyError:
                pass

        tag.delall("COMM:")
        if "comment" in self:
            enc = encoding_for(self["comment"])
            t = self["comment"].split("\n")
            tag.add(mutagen.id3.COMM(encoding=enc, text=t, desc="",
                                     lang="\x00\x00\x00"))

        tag.delall("USLT")
        if "lyrics" in self:
            enc = encoding_for(self["lyrics"])
            if not ("~lyricslanguage" in self and
                    # language has to be a 3 byte ISO 639-2 code
                    self["~lyricslanguage"] in ISO_639_2):
                self["~lyricslanguage"] = "und" # undefined
            # lyrics are single string, not array
            tag.add(mutagen.id3.USLT(encoding=enc, text=self["lyrics"],
                                     desc=self.get("~lyricsdescription", ""),
                                     lang=self["~lyricslanguage"]))

        # Delete old foobar replaygain ..
        for frame in tag.getall("TXXX"):
            if frame.desc.lower() in RG_KEYS:
                del tag[frame.HashKey]

        # .. write new one
        for k in RG_KEYS:
            # Add new ones
            if k in self:
                value = self[k]
                tag.add(mutagen.id3.TXXX(encoding=encoding_for(value),
                                         text=value.split("\n"),
                                         desc=k.upper()))

        # we shouldn't delete all, but we use unknown ones as fallback, so make
        # sure they don't come back after reloading
        for t in tag.getall("RVA2"):
            if t.channel == 1:
                del tag[t.HashKey]

        for k in ["track", "album"]:
            if ("replaygain_%s_gain" % k) in self:
                try:
                    gain = float(self["replaygain_%s_gain" % k].split()[0])
                except (ValueError, IndexError):
                    gain = 0
                try:
                    peak = float(self["replaygain_%s_peak" % k])
                except (ValueError, KeyError):
                    peak = 0
                # https://github.com/quodlibet/quodlibet/issues/1027
                peak = max(min(1.9, peak), 0)
                gain = max(min(63.9, gain), -64)
                f = mutagen.id3.RVA2(desc=k, channel=1, gain=gain, peak=peak)
                tag.add(f)

        for key in self.TXXX_MAP:
            try:
                del(tag["TXXX:" + key])
            except KeyError:
                pass
        for key in self.PAM_XXXT:
            if key in self.SDI:
                # we already write it back using non-TXXX frames
                continue
            if key in self:
                value = self[key]
                f = mutagen.id3.TXXX(encoding=encoding_for(value),
                                     text=value.split("\n"),
                                     desc=self.PAM_XXXT[key])
                tag.add(f)

        if (config.getboolean("editing", "save_to_songs") and
                (self.has_rating or self.get("~#playcount", 0) != 0)):
            email = config.get("editing", "save_email").strip()
            email = email or const.EMAIL
            t = mutagen.id3.POPM(email=email,
                                 rating=int(255 * self("~#rating")),
                                 count=self.get("~#playcount", 0))
            tag.add(t)

        with translate_errors():
            audio.save()
        self.sanitize()

    can_change_images = True

    def clear_images(self):
        """Delete all embedded images"""

        with translate_errors():
            audio = self.Kind(self["~filename"])

            if audio.tags is not None:
                audio.tags.delall("APIC")
                audio.save()

        self.has_images = False

    def get_images(self):
        """Returns a list of embedded images"""

        images = []

        try:
            with translate_errors():
                audio = self.Kind(self["~filename"])
        except AudioFileError:
            return images

        tag = audio.tags
        if tag is None:
            return images

        for frame in tag.getall("APIC"):
            f = get_temp_cover_file(frame.data, frame.mime)
            images.append(EmbeddedImage(f, frame.mime, type_=frame.type))

        images.sort(key=lambda c: c.sort_key)
        return images

    def get_primary_image(self):
        """Returns the primary embedded image"""

        try:
            with translate_errors():
                audio = self.Kind(self["~filename"])
        except AudioFileError:
            return

        tag = audio.tags
        if tag is None:
            return

        # get the APIC frame with type == 3 (cover) or the first one
        cover = None
        for frame in tag.getall("APIC"):
            cover = cover or frame
            if frame.type == APICType.COVER_FRONT:
                cover = frame
                break

        if cover:
            f = get_temp_cover_file(cover.data, cover.mime)
            return EmbeddedImage(f, cover.mime, type_=cover.type)

    def set_image(self, image):
        """Replaces all embedded images by the passed image"""

        with translate_errors():
            audio = self.Kind(self["~filename"])

        if audio.tags is None:
            audio.add_tags()

        tag = audio.tags

        try:
            data = image.read()
        except OSError as e:
            raise AudioFileError(e) from e

        tag.delall("APIC")
        frame = mutagen.id3.APIC(
            encoding=3, mime=image.mime_type, type=APICType.COVER_FRONT,
            desc="", data=data)
        tag.add(frame)

        with translate_errors():
            audio.save()

        self.has_images = True
