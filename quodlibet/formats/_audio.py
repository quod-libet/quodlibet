# Copyright 2004-2005 Joe Wreschnig, Michael Urman
#           2012-2023 Nick Boultbee
#                2022 Jej@github
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# Much of this code is highly optimized, because many of the functions
# are called in tight loops. Don't change things just to make them
# more readable, unless they're also faster.

import json
import os
import re
import shutil
import time
from typing import Any, Generic, TypeVar
from collections import OrderedDict
from itertools import zip_longest

from senf import fsn2uri, fsnative, fsn2text, bytes2fsn, path2fsn

from quodlibet import _, print_d
from quodlibet import util
from quodlibet import config
from quodlibet.util.path import mkdir, mtime, normalize_path, \
                                ismount, get_home_dir, RootPathFile
from quodlibet.util.string import encode, decode, isascii
from quodlibet.util.environment import is_windows

from quodlibet.util import iso639, cached_property
from quodlibet.util import human_sort_key as human, capitalize

from quodlibet.util.string.date import format_date
from quodlibet.util.tags import TAG_ROLES, TAG_TO_SORT

from ._image import ImageContainer
from ._misc import AudioFileError, translate_errors


translate_errors  # noqa

AlbumKey = tuple[str, str, str]
"""An album key is (currently) a tuple"""

MIGRATE = {"~#playcount", "~#laststarted", "~#lastplayed", "~#added",
           "~#skipcount", "~#rating", "~bookmark"}
"""These get migrated if a song gets reloaded"""

PEOPLE = ["artist", "albumartist", "author", "composer", "~performers",
          "originalartist", "producer", "lyricist", "arranger", "conductor"]
"""Sources of the ~people tag, most important first"""

TIME_TAGS = {"~#lastplayed", "~#laststarted", "~#added", "~#mtime"}
"""Time in seconds since epoch, defaults to 0"""

HUMAN_TO_NUMERIC_TIME_TAGS = {t.replace("~#", "~"): t for t in TIME_TAGS}

DURATION_TAGS = {"~#length"}
"""Duration in seconds"""

SIZE_TAGS = {"~#filesize"}
"""Size in bytes, defaults to 0"""

NUMERIC_ZERO_DEFAULT = {"~#skipcount", "~#playcount", "~#length", "~#bitrate"}
"""Defaults to 0"""

NUMERIC_ZERO_DEFAULT.update(TIME_TAGS)
NUMERIC_ZERO_DEFAULT.update(SIZE_TAGS)

FILESYSTEM_TAGS = {"~filename", "~basename", "~dirname", "~mountpoint"}
"""Values are bytes in Linux instead of unicode"""

SORT_TO_TAG = {v: k for (k, v) in TAG_TO_SORT.items()}
"""Reverse map, so sort tags can fall back to the normal ones"""

PEOPLE_SORT = [TAG_TO_SORT.get(k, k) for k in PEOPLE]
"""Sources for ~peoplesort, most important first"""

VARIOUS_ARTISTS_VALUES = "V.A.", "various artists", "Various Artists"
"""Values for ~people representing lots of people, most important last"""


def decode_value(tag, value):
    """Returns a unicode representation of the passed value, based on
    the type and the tag it originated from.

    Not reversible.
    """

    if tag in FILESYSTEM_TAGS:
        return fsn2text(value)
    elif tag[:2] == "~#":
        if isinstance(value, float):
            return "%.2f" % value
        else:
            return str(value)
    return str(value)


K = TypeVar("K")


class HasKey(Generic[K]):
    """Many things can be keyed"""
    key: K


class AudioFile(dict, ImageContainer, HasKey):
    """An audio file. It looks like a dict, but implements synthetic
    and tied tags via __call__ rather than __getitem__. This means
    __getitem__, get, and so on can be used for efficiency.

    If you need to sort many AudioFiles, you can use their sort_key
    attribute as a decoration.

    Keys are either ASCII str or unicode.
    Values are always unicode except if the tag is part of FILESYSTEM_TAGS,
    then the value is of the path type (str on UNIX, unicode on Windows)

    Some methods will make sure the returned values are always unicode, see
    their description.
    """

    fill_metadata = False
    """New tags received from the backend will update the song"""

    fill_length = False
    """New song duration from the backend will update the song"""

    multisong = False
    """Is a container for multiple songs
     i.e. while played new songs can start / end"""

    streamsong = False
    """Is part of a multisong"""

    can_add = True
    """Can be added to the queue, playlists"""

    is_file = True
    """Is a real (local) file"""

    format = "Unknown Audio File"
    """The underlying file format"""

    supports_rating_and_play_count_in_file = False
    """Does this format support storing ratings and play counts in the file"""

    mimes: list[str] = []
    """MIME types this class can represent"""

    @cached_property
    def _date_format(self) -> str:
        return config.gettext("settings", "datecolumn_timestamp_format")

    def __init__(self, default=(), **kwargs):
        for key, value in dict(default).items():
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def __song_key(self):
        return (self("~#disc", 0), self("~#track", 0),
            human(self("artistsort")),
            self.get("musicbrainz_artistid", ""),
            human(self.get("title", "")),
            self.get("~filename"))

    @util.cached_property
    def album_key(self) -> AlbumKey:
        id_val: str = (self.get("album_grouping_key")
                       or self.get("labelid")
                       or self.get("musicbrainz_albumid", ""))  # type: ignore
        return id_val, human(self("albumsort", "")), human(self("albumartistsort", ""))

    @util.cached_property
    def sort_key(self):
        return [self.album_key, self.__song_key()]

    @staticmethod
    def sort_by_func(tag):
        """Returns a fast sort function for a specific tag (or pattern).
        Some keys are already in the sort cache, so we can use them."""
        def artist_sort(song):
            return song.sort_key[1][2]

        if callable(tag):
            return lambda song: human(tag(song))
        elif tag == "artistsort":
            return artist_sort
        elif tag in FILESYSTEM_TAGS:
            return lambda song: fsn2text(song(tag))
        elif tag.startswith("~#") and "~" not in tag[2:]:
            return lambda song: song(tag, 0)
        return lambda song: human(song(tag))

    def __getstate__(self):
        """Don't pickle anything from __dict__"""
        pass

    def __setstate__(self, state):
        """Needed because we have defined getstate"""
        pass

    def __setitem__(self, key, value):
        # validate key
        if not isinstance(key, str):
            raise TypeError("key has to be str")

        # validate value
        if key.startswith("~#"):
            if not isinstance(value, int | float):
                raise TypeError
        elif key in FILESYSTEM_TAGS:
            if not isinstance(value, fsnative):
                value = path2fsn(value)
        else:
            value = str(value)

        dict.__setitem__(self, key, value)

        pop = self.__dict__.pop
        pop("album_key", None)
        pop("sort_key", None)

    def __delitem__(self, key):
        dict.__delitem__(self, key)

        pop = self.__dict__.pop
        pop("album_key", None)
        pop("sort_key", None)

    @property
    def key(self) -> K:  # type: ignore
        return self["~filename"]

    @property
    def mountpoint(self):
        return self["~mountpoint"]

    def __hash__(self):
        # Dicts aren't hashable by default, so we need a hash
        # function. Previously this used ~filename. That created a
        # situation when an object could end up in two buckets by
        # renaming files. So now it uses identity.
        return hash(id(self))

    def __eq__(self, other):
        # And to preserve Python hash rules, we need a strict __eq__.
        return self is other

    def __lt__(self, other):
        return self.sort_key < other.sort_key

    def __ne__(self, other):
        return self is not other

    def has_rating_and_playcount_in_file(self, email):
        """Returns True if the audio file has rating and play counts for the
        provided email. Returns False otherwise, or if storing ratings and play
        counts in the file is not supported for this audio format.
        """
        return False

    def reload(self):
        """Reload an audio file from disk. If reloading fails nothing will
        change.

        Raises:
            AudioFileError: if the file fails to load
        """

        backup = dict(self)
        fn = self["~filename"]
        saved = {}
        persisted = config.getboolean("editing", "save_to_songs")
        persisted_keys = ({"~#rating", "~#playcount"}
                          if self.supports_rating_and_play_count_in_file and persisted
                          else set())
        for key in self:
            # Only migrate keys that aren't (probably) persisted to file (#3569)
            if key in MIGRATE - persisted_keys:
                saved[key] = self[key]
        self.clear()
        self["~filename"] = fn
        try:
            self.__init__(fn)
        except AudioFileError:
            self.update(backup)
            raise
        else:
            self.update(saved)

    def realkeys(self):
        """Returns a list of keys that are not internal, i.e. they don't
        have '~' in them."""

        return [s for s in self.keys() if s[:1] != "~"]

    def prefixkeys(self, prefix):
        """Returns a list of dict keys that either match prefix or start
        with prefix + ':'.
        """

        l = []
        for k in self:
            if k.startswith(prefix):
                if k == prefix or k.startswith(prefix + ":"):
                    l.append(k)
        return l

    def _prefixvalue(self, tag):
        return "\n".join(self.list_unique(sorted(self.prefixkeys(tag))))

    def iterrealitems(self):
        return ((k, v) for (k, v) in self.items() if k[:1] != "~")

    def __call__(self, key, default: Any = "", connector=" - ", joiner=", "):
        """Return the value(s) for a key, synthesizing if necessary.
        Multiple values for a key are delimited by newlines.

        A default value may be given (like `dict.get`);
        the default default is an empty unicode string
        (even if the tag is numeric).

        If a tied tag ('a~b') is requested, the `connector` keyword
        argument may be used to specify what it is tied with.
        In case the tied tag contains numeric and file path tags, the result
        will still be a unicode string.
        The `joiner` keyword specifies how multiple *values* will be joined
        within that tied tag output, e.g.
            ~people~title = "Kanye West, Jay Z - New Day"

        For details on tied tags, see the documentation for `util.tagsplit`.
        """
        real_key = key
        if key[:1] == "~":
            key = key[1:]
            if "~" in key:
                values = []
                sub_tags = util.tagsplit(real_key)
                # If it's genuinely a tied tag (not ~~people etc.), we want
                # to delimit the multi-values separately from the tying
                j = joiner if len(sub_tags) > 1 else "\n"
                for t in sub_tags:
                    vs = [decode_value(real_key, v) for v in (self.list(t))]
                    v = j.join(vs)
                    if v:
                        values.append(v)
                return connector.join(values) or default
            elif key == "#track":
                try:
                    return int(self["tracknumber"].split("/")[0])
                except (ValueError, TypeError, KeyError):
                    return default
            elif key == "#disc":
                try:
                    return int(self["discnumber"].split("/")[0])
                except (ValueError, TypeError, KeyError):
                    return default
            elif key == "length":
                length = self.get("~#length")
                if length is None:
                    return default
                else:
                    return util.format_time_display(length)
            elif key == "#rating":
                return dict.get(self, real_key, config.RATINGS.default)
            elif key == "rating":
                return util.format_rating(self("~#rating"))
            elif key == "people":
                return "\n".join(self.list_unique(PEOPLE)) or default
            elif key == "people:real":
                # Issue 1034: Allow removal of V.A. if others exist.
                unique = self.list_unique(PEOPLE)
                # Order is important, for (unlikely case): multiple removals
                for val in VARIOUS_ARTISTS_VALUES:
                    if len(unique) > 1 and val in unique:
                        unique.remove(val)
                return "\n".join(unique) or default
            elif key == "people:roles":
                return (self._role_call("performer", PEOPLE)
                        or default)
            elif key == "peoplesort":
                return ("\n".join(self.list_unique(PEOPLE_SORT)) or
                        self("~people", default, connector))
            elif key == "peoplesort:roles":
                # Ignores non-sort tags if there are any sort tags (e.g. just
                # returns "B" for {artist=A, performersort=B}).
                # TODO: figure out the "correct" behavior for mixed sort tags
                return (self._role_call("performersort", PEOPLE_SORT)
                        or self("~peoplesort", default, connector))
            elif key in ("performers", "performer"):
                return self._prefixvalue("performer") or default
            elif key in ("performerssort", "performersort"):
                return (self._prefixvalue("performersort") or
                        self(real_key[-4:], default, connector))
            elif key in ("performers:roles", "performer:roles"):
                return (self._role_call("performer") or default)
            elif key in ("performerssort:roles", "performersort:roles"):
                return (self._role_call("performersort")
                        or self(real_key.replace("sort", ""), default,
                                connector))
            elif key == "basename":
                return os.path.basename(self["~filename"]) or self["~filename"]
            elif key == "dirname":
                return os.path.dirname(self["~filename"]) or self["~filename"]
            elif key == "uri":
                try:
                    return self["~uri"]
                except KeyError:
                    return fsn2uri(self["~filename"])
            elif key == "format":
                return self.get("~format", str(self.format))
            elif key == "codec":
                codec = self.get("~codec")
                if codec is None:
                    return self("~format")
                return codec
            elif key == "encoding":
                encoding = "\n".join(
                    part
                    for part in [self.get("~encoding"), self.get("encodedby")]
                    if part
                )
                return encoding or default
            elif key == "language":
                codes = self.list("language")
                if not codes:
                    return default
                return "\n".join(iso639.translate(c) or c for c in codes)
            elif key == "bitrate":
                return util.format_bitrate(self("~#bitrate"))
            elif key == "#date":
                date = self.get("date")
                if date is None:
                    return default
                return util.date_key(date)
            elif key == "year":
                return util.parse_year(self.get("date", default))
            elif key == "#year":
                try:
                    return int(util.parse_year(self.get("date", default)))
                except (ValueError, TypeError, KeyError):
                    return default
            elif key == "originalyear":
                return util.parse_year(self.get("originaldate", default))
            elif key == "#originalyear":
                try:
                    return int(util.parse_year(self.get("originaldate", default)))
                except (ValueError, TypeError, KeyError):
                    return default
            elif key == "#releasedate":
                releasedate = self.get("releasedate")
                if releasedate is None:
                    return default
                return util.date_key(releasedate)
            elif key == "#tracks":
                try:
                    return int(self["tracknumber"].split("/")[1])
                except (ValueError, IndexError, TypeError, KeyError):
                    return default
            elif key == "#discs":
                try:
                    return int(self["discnumber"].split("/")[1])
                except (ValueError, IndexError, TypeError, KeyError):
                    return default
            elif key == "lyrics":
                # First, try the embedded lyrics.
                try:
                    return self["lyrics"]
                except KeyError:
                    pass

                try:
                    return self["unsyncedlyrics"]
                except KeyError:
                    pass

                # If there are no embedded lyrics, try to read them from
                # the external file.
                lyric_filename = self.lyric_filename
                if not lyric_filename:
                    return default
                try:
                    with open(lyric_filename, "rb") as fileobj:
                        print_d(f"Reading lyrics from {lyric_filename!r}")
                        text = fileobj.read().decode("utf-8", "replace")
                        # try to skip binary files
                        if "\0" in text:
                            return default
                        return text
                except (OSError, UnicodeDecodeError):
                    return default
            elif key == "filesize":
                return util.format_size(self("~#filesize", 0))
            elif key == "playlists":
                # TODO: avoid static dependency here... somehow
                from quodlibet import app
                lib = app.library
                if not lib:
                    return ""
                playlists = lib.playlists.playlists_featuring(self)
                return "\n".join(s.name for s in playlists) or default
            elif key.startswith("#replaygain_"):
                try:
                    val = self.get(key[1:], default)
                    return round(float(val.split(" ")[0]), 2)
                except (ValueError, TypeError, AttributeError):
                    return default
            elif real_key in HUMAN_TO_NUMERIC_TIME_TAGS:
                time_value = float(self.get(HUMAN_TO_NUMERIC_TIME_TAGS[real_key], 0))
                return format_date(time_value, self._date_format)
            elif key[:1] == "#":
                if real_key in self:
                    return self[real_key]
                elif real_key in NUMERIC_ZERO_DEFAULT:
                    return 0
                else:
                    try:
                        val = self[real_key[2:]]
                    except KeyError:
                        return default
                    try:
                        return int(val)
                    except ValueError:
                        try:
                            return float(val)
                        except ValueError:
                            return default
            elif key == "json":
                # Help the testing by being deterministic with sort_keys.
                return json.dumps(self, sort_keys=True)
            else:
                return dict.get(self, real_key, default)

        elif key == "title":
            title = dict.get(self, "title")
            if title is None:
                # build a title with missing_title_template option
                unknown_track_template = _(config.gettext(
                    "browsers", "missing_title_template"))

                from quodlibet.pattern import Pattern
                try:
                    pattern = Pattern(unknown_track_template)
                except ValueError:
                    title = decode_value("~basename", self("~basename"))
                else:
                    title = pattern % self

            return title
        elif key in SORT_TO_TAG:
            try:
                return self[key]
            except KeyError:
                key = SORT_TO_TAG[key]
        return dict.get(self, key, default)

    def _role_call(self, role_tag, sub_keys=None):
        role_tag_keys = self.prefixkeys(role_tag)

        role_map = {}
        for key in role_tag_keys:
            if key == role_tag:
                # #2986: don't add a role description for the bare tag, unless
                # this is a composite tag (e.g. only show "(Performance)" for
                # ~people:roles and not ~performer:roles).
                if sub_keys is None:
                    continue
                else:
                    role = TAG_ROLES.get(role_tag, role_tag)
            else:
                role = key.split(":", 1)[-1]
            for name in self.list(key):
                role_map.setdefault(name, []).append(role)

        if sub_keys is None:
            names = self.list_unique(role_tag_keys)
        else:
            names = self.list_unique(sub_keys)
            for tag in sub_keys:
                if tag in TAG_ROLES:
                    for name in self.list(tag):
                        role_map.setdefault(name, []).append(TAG_ROLES[tag])

        descs = []
        for name in names:
            roles = role_map.get(name, [])
            if not roles:
                descs.append(name)
            else:
                roles = sorted(map(capitalize, roles))
                descs.append(f"{name} ({', '.join(roles)})")

        return "\n".join(descs)

    @property
    def lyric_filename(self) -> str | None:
        """Returns the validated, or default, lyrics filename for this
        file. User defined '[memory] lyric_rootpaths' and
        '[memory] lyric_filenames' matches take precedence"""

        from quodlibet.pattern import ArbitraryExtensionFileFromPattern

        rx_params = re.compile(r"[^\\]<[^" + re.escape(os.sep) + r"]*[^\\]>")

        def expand_pathfile(rpf):
            """Return the expanded RootPathFile"""
            expanded = []
            root = os.path.expanduser(rpf.root)
            pathfile = os.path.expanduser(rpf.pathfile)
            if rx_params.search(pathfile):
                root = ArbitraryExtensionFileFromPattern(root).format(self)
                pathfile = ArbitraryExtensionFileFromPattern(pathfile).format(self)
            rpf = RootPathFile(root, pathfile)
            expanded.append(rpf)
            if not os.path.exists(pathfile) and is_windows():
                # prioritise a special character encoded version
                #
                # most 'alien' chars are supported for 'nix fs paths, and we
                # only pass the proposed path through 'escape_filename' (which
                # apparently doesn't respect case) if we don't care about case!
                #
                # FIX: assumes 'nix build used on a case-sensitive fs, nt case
                # insensitive. clearly this is not biting anyone though (yet!)
                pathfile = os.path.sep.join([rpf.root, rpf.end_escaped])
                rpf = RootPathFile(rpf.root, pathfile)
                expanded.insert(len(expanded) - 1, rpf)
            return expanded

        def sanitise(sep, parts):
            """Return a sanitised version of a path's parts"""
            return sep.join(part.replace(os.path.sep, "")[:128] for part in parts)

        # setup defaults (user-defined take precedence)
        # root search paths
        lyric_paths = config.getstringlist("memory", "lyric_rootpaths", [])
        # ensure default paths
        lyric_paths.append(os.path.join(get_home_dir(), ".lyrics"))
        lyric_paths.append(os.path.join(os.path.dirname(self.comma("~filename"))))
        # search pathfile names
        lyric_filenames = config.getstringlist("memory", "lyric_filenames", [])
        # ensure some default pathfile names
        lyric_filenames.append(
            sanitise(os.sep, [(self.comma("lyricist") or
                              self.comma("artist")),
                              self.comma("title")]) + ".lyric")
        lyric_filenames.append(
            sanitise(" - ", [(self.comma("lyricist") or
                             self.comma("artist")),
                             self.comma("title")]) + ".lyric")

        # generate all potential paths (unresolved/unexpanded)
        pathfiles = OrderedDict()
        for r in lyric_paths:
            for f in lyric_filenames:
                pathfile = os.path.join(r, os.path.dirname(f),
                                        fsnative(os.path.basename(f)))
                rpf = RootPathFile(r, pathfile)
                if pathfile not in pathfiles:
                    pathfiles[pathfile] = rpf

        #print_d("searching for lyrics in:\n%s" % '\n'.join(pathfiles.keys()))

        # expand each raw pathfile in turn and test for existence
        match_ = None
        pathfiles_expanded = OrderedDict()
        for _pf, rpf in pathfiles.items():
            for rpf in expand_pathfile(rpf):  # resolved as late as possible  # noqa
                pathfile = rpf.pathfile
                pathfiles_expanded[pathfile] = rpf
                if os.path.exists(pathfile):
                    match_ = pathfile
                    break
            if match_:
                break

        if not match_:
            # search even harder!
            lyric_extensions = ["lyric", "lyrics", "", "txt"]
            #print_d("extending search to extensions: %s" % lyric_extensions)

            def generate_mod_ext_paths(pathfile):
                # separate pathfile's extension (if any)
                ext = os.path.splitext(pathfile)[1][1:]
                path = pathfile[:-1 * len(ext)].strip(".") if ext else pathfile
                # skip the proposed lyric extension if it is the same as
                # the original for a given search pathfile stub - it has
                # already been tested without success!
                extra_extensions = [x for x in lyric_extensions if x != ext]

                # join valid new extensions to pathfile stub and return
                return [".".join([path, ext]) if ext else path
                           for ext in extra_extensions]

            # look for a match by modifying the extension for each of the
            # (now fully resolved) 'pathfiles_expanded' search items
            for pathfile in pathfiles_expanded.keys():
                # get alternatives for existence testing
                paths_mod_ext = generate_mod_ext_paths(pathfile)
                for path_ext in paths_mod_ext:
                    if os.path.exists(path_ext):
                        # persistence has paid off!
                        #print_d("extended search match!")
                        match_ = path_ext
                        break
                if match_:
                    break

        if not match_:
            # default
            match_ = list(pathfiles_expanded.keys())[0]

        return match_

    @property
    def has_rating(self):
        """True if the song has a rating set.

        In case this is False song('~#rating') would return the default value
        """

        return self.get("~#rating") is not None

    def remove_rating(self):
        """Removes the set rating so the default will be returned"""

        self.pop("~#rating", None)

    def comma(self, key):
        """Get all values of a tag, separated by commas. Synthetic
        tags are supported, but will be slower. All list items
        will be unicode.

        If the value is numeric, that is returned rather than a list.

        Because this function is used to format values for display,
        blank tags are removed from the results.
        """

        if "~" in key or key == "title":
            if key in FILESYSTEM_TAGS:
                v = fsn2text(self(key, fsnative()))
            else:
                v = self(key, "")
        else:
            v = self.get(key, "")

        if isinstance(v, int | float):
            return v
        else:
            return re.sub("\n+", ", ", v.strip())

    def list(self, key) -> list[Any]:
        """Get all values of a tag, as a list. Synthetic tags are supported,
        but will be slower. Numeric tags will give their one value.

        For file path keys the returned list might contain path items
        (non-unicode).

        An empty tag cannot be distinguished from a non-existent tag; both
        result in []. If a file contains multiple values of a tag, empty
        instances of the tag will not be included in the list.
        """

        if "~" in key or key == "title":
            v = self(key)
            if v == "":
                return []
            else:
                v = v.split("\n") if isinstance(v, str) else [v]
                return [x for x in v if x]
        else:
            v = self.get(key, "")
            return [x for x in v.split("\n") if x]

    def list_sort(self, key):
        """Like list but return display,sort pairs when appropriate
        and work on all tags.

        In case no sort value exists the display one is returned. If a
        display tag is empty, the tag is removed from the returned list
        and the corresponding tag in the sort tag list is ignored as
        well.

        The assumption being made here is that if the ordering between
        display and sort tags doesn't match exactly, that's a problem
        with the user's file.
        """

        display = decode_value(key, self(key))
        display = display.split("\n") if display else []
        sort = []
        if key in TAG_TO_SORT:
            sort = decode_value(TAG_TO_SORT[key],
                                self(TAG_TO_SORT[key]))
            # it would be better to use something that doesn't fall back
            # to the key itself, but what?
            sort = sort.split("\n") if sort else []

        result = []
        for d, s in zip_longest(display, sort):
            if d:
                result.append((d, (s if s is not None and s != "" else d)))
        return result

    def list_separate(self, key):
        """For tied tags return the list union of the display,sort values
           otherwise just do list_sort
        """
        if key[:1] == "~" and "~" in key[1:]: # tied tag
            vals = [self.list_sort(tag) for tag in util.tagsplit(key)]
            r = [j for i in vals for j in i]
            return r
        else:
            return self.list_sort(key)

    def list_unique(self, keys):
        """Returns a combined value of all values in keys; duplicate values
        will be ignored.

        Returns the same as list().
        """

        l = []
        seen = set()
        for k in keys:
            for v in self.list(k):
                if v not in seen:
                    l.append(v)
                    seen.add(v)
        return l

    def as_lowercased(self):
        """Returns a new AudioFile with all keys lowercased / values merged.

        Useful for tag writing for case insensitive tagging formats like
        APEv2 or VorbisComment.
        """

        merged = AudioFile()
        text = {}
        for key, value in self.items():
            lower = key.lower()
            if key.startswith("~#"):
                merged[lower] = value
            else:
                text.setdefault(lower, []).extend(value.split("\n"))
        for key, values in text.items():
            merged[key] = "\n".join(values)
        return merged

    def exists(self):
        """Return true if the file still exists (or we can't tell)."""

        return os.path.exists(self["~filename"])

    def valid(self):
        """Return true if the file cache is up-to-date (checked via
        mtime), or we can't tell."""
        return (bool(self.get("~#mtime", 0)) and
                self["~#mtime"] == mtime(self["~filename"]))

    def mounted(self):
        """Return true if the disk the file is on is mounted, or
        the file is not on a disk."""
        return ismount(self.get("~mountpoint", "/"))

    def can_multiple_values(self, key=None):
        """If no arguments are given, return a list of tags that can
        have multiple values, or True if 'any' tags can.
        """

        return True

    def can_change(self, k=None):
        """See if this file supports changing the given tag. This may
        be a limitation of the file type or QL's design.

        The writing code should handle all kinds of keys, so this is
        just a suggestion.

        If no arguments are given, return a list of tags that can be
        changed, or True if 'any' tags can be changed (specific tags
        should be checked before adding)."""

        if k is None:
            return True

        if not isascii(k):
            return False

        if not k or "=" in k or "~" in k:
            return False

        return True

    def is_writable(self):
        return os.access(self["~filename"], os.W_OK)

    def rename(self, newname):
        """Rename a file. Errors are not handled. This shouldn't be used
        directly; use library.rename instead."""

        if os.path.isabs(newname):
            mkdir(os.path.dirname(newname))
        else:
            newname = os.path.join(self("~dirname"), newname)

        if not os.path.exists(newname):
            shutil.move(self["~filename"], newname)
        elif normalize_path(newname, canonicalise=True) != self["~filename"]:
            raise ValueError

        self.sanitize(newname)

    def sanitize(self, filename=None):
        """Fill in metadata defaults. Find ~mountpoint, ~#mtime, ~#filesize
        and ~#added. Check for null bytes in tags.

        Does not raise.
        """

        # Replace nulls with newlines, trimming zero-length segments
        for key, val in list(self.items()):
            self[key] = val
            if isinstance(val, str) and "\0" in val:
                self[key] = "\n".join(filter(lambda s: s, val.split("\0")))
            # Remove unnecessary defaults
            if key in NUMERIC_ZERO_DEFAULT and val == 0:
                del self[key]

        if filename:
            self["~filename"] = filename
        elif "~filename" not in self:
            raise ValueError("Unknown filename!")

        assert isinstance(self["~filename"], fsnative)

        if self.is_file:
            self["~filename"] = normalize_path(
                self["~filename"], canonicalise=True)
            # Find mount point (terminating at "/" if necessary)
            head = self["~filename"]
            while "~mountpoint" not in self:
                head, tail = os.path.split(head)
                # Prevent infinite loop without a fully-qualified filename
                # (the unit tests use these).
                head = head or fsnative("/")
                if ismount(head):
                    self["~mountpoint"] = head
        else:
            self["~mountpoint"] = fsnative("/")

        # Fill in necessary values.
        self.setdefault("~#added", int(time.time()))

        # For efficiency, do a single stat here. See Issue 504
        try:
            stat = os.stat(self["~filename"])
            self["~#mtime"] = stat.st_mtime
            self["~#filesize"] = stat.st_size

            # Issue 342. This is a horrible approximation (due to headers) but
            # on FLACs, the most common case, this should be close enough
            if "~#bitrate" not in self:
                try:
                    # kbps = bytes * 8 / seconds / 1000
                    self["~#bitrate"] = int(stat.st_size /
                                            (self["~#length"] * (1000 / 8)))
                except (KeyError, ZeroDivisionError):
                    pass
        except OSError:
            self["~#mtime"] = 0

    def to_dump(self):
        """A string of 'key=value' lines, similar to vorbiscomment output.

        Returns:
            bytes
        """

        def encode_key(k):
            return encode(k) if isinstance(k, str) else k

        s = []
        for k in self.keys():
            enc_key = encode_key(k)
            assert isinstance(enc_key, bytes)

            if isinstance(self[k], int):
                l = enc_key + encode("=%d" % self[k])
                s.append(l)
            elif isinstance(self[k], float):
                l = enc_key + encode("=%f" % self[k])
                s.append(l)
            else:
                for v2 in self.list(k):
                    if not isinstance(v2, bytes):
                        v2 = encode(v2)
                    s.append(enc_key + b"=" + v2)
        for k in (NUMERIC_ZERO_DEFAULT - set(self.keys())):
            enc_key = encode_key(k)
            l = enc_key + encode("=%d" % self.get(k, 0))
            s.append(l)
        if "~#rating" not in self:
            s.append(encode("~#rating=%f" % self("~#rating")))
        s.append(encode("~format=%s" % self.format))
        s.append(b"")
        return b"\n".join(s)

    def from_dump(self, text):
        """Parses the text created with to_dump and adds the found tags.

        Args:
            text (bytes)
        """

        for line in text.split(b"\n"):
            if not line:
                continue
            parts = line.split(b"=")
            key = decode(parts[0])
            val = b"=".join(parts[1:])
            if key == "~format":
                pass
            elif key in FILESYSTEM_TAGS:
                self.add(key, bytes2fsn(val, "utf-8"))
            elif key.startswith("~#"):
                try:
                    self.add(key, int(val))
                except ValueError:
                    try:
                        self.add(key, float(val))
                    except ValueError:
                        pass
            else:
                self.add(key, decode(val))

    def change(self, key, old_value, new_value):
        """Change 'old_value' to 'new_value' for the given metadata key.
        If the old value is not found, set the key to the new value."""
        try:
            parts = self.list(key)
            try:
                parts[parts.index(old_value)] = new_value
            except ValueError:
                self[key] = new_value
            else:
                self[key] = "\n".join(parts)
        except KeyError:
            self[key] = new_value

    def add(self, key, value):
        """Add a value for the given metadata key."""
        if key not in self:
            self[key] = value
        else:
            self[key] += "\n" + value

    def remove(self, key, value=None):
        """Remove a value from the given key.

        If value is None remove all values for that key, if it exists.
        If the key or value is not found, do nothing.
        """

        if key not in self:
            return
        elif value is None or self[key] == value:
            del self[key]
        else:
            try:
                parts = self.list(key)
                parts.remove(value)
                self[key] = "\n".join(parts)
            except ValueError:
                pass

    def replay_gain(self, profiles, pre_amp_gain=0, fallback_gain=0):
        """Return the computed Replay Gain scale factor.

        profiles is a list of Replay Gain profile names ('album',
        'track') to try before giving up. The special profile name
        'none' will cause no scaling to occur. pre_amp_gain will be
        applied before checking for clipping. fallback_gain will be
        used when the song does not have replaygain information.
        """
        for profile in profiles:
            if profile == "none":
                return 1.0
            try:
                db = float(self["replaygain_%s_gain" % profile].split()[0])
                peak = float(self.get("replaygain_%s_peak" % profile, 1))
            except (KeyError, ValueError, IndexError):
                continue
            else:
                db += pre_amp_gain
                try:
                    scale = 10. ** (db / 20)
                except OverflowError:
                    scale = 1.0 / peak
                else:
                    if scale * peak > 1:
                        scale = 1.0 / peak  # don't clip
                return min(15, scale)
        else:
            try:
                scale = 10. ** ((fallback_gain + pre_amp_gain) / 20)
            except OverflowError:
                scale = 1.0
            else:
                scale = min(scale, 1.0)
            return min(15, scale)

    def write(self):
        """Write metadata back to the file.

        Raises:
            AudioFileError: in case writing fails
        """

        raise NotImplementedError

    @property
    def bookmarks(self):
        """Parse and return song position bookmarks, or set them.
        Accessing this returns a copy, so song.bookmarks.append(...)
        will not work; you need to do

            marks = song.bookmarks
            marks.append(...)
            song.bookmarks = marks
        """

        marks = []
        invalid = []
        for line in self.list("~bookmark"):
            try:
                time, mark = line.split(" ", 1)
            except Exception:
                invalid.append((-1, line))
            else:
                try:
                    time = util.parse_time(time, False)
                except Exception:
                    invalid.append((-1, line))
                else:
                    if time >= 0:
                        marks.append((time, mark))
                    else:
                        invalid.append((-1, line))
        marks.sort()
        marks.extend(invalid)
        return marks

    @bookmarks.setter
    def bookmarks(self, marks):
        result = []
        for time_, mark in marks:
            if time_ < 0:
                raise ValueError("mark times must be positive")
            result.append(f"{util.format_time(time_)} {mark}")
        result = "\n".join(result)
        if result:
            self["~bookmark"] = result
        elif "~bookmark" in self:
            del(self["~bookmark"])


# Looks like the real thing.
DUMMY_SONG = AudioFile({
    "~#length": 234, "~filename": os.devnull,
    "artist": "The Artist", "album": "An Example Album",
    "title": "First Track", "tracknumber": 1,
    "date": "2010-12-31",
})
