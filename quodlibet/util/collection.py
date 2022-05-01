# Copyright 2004-2013 Joe Wreschnig, Michael Urman, Iñigo Serna,
#                     Christoph Reiter, Steven Robertson
#           2011-2021 Nick Boultbee
#           2021      Joschua Gandert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from __future__ import absolute_import
from __future__ import annotations

import os
import random
from typing import Any, Union, List, Tuple
from urllib.parse import quote

from senf import fsnative, fsn2bytes, bytes2fsn, path2fsn, _fsnative

from quodlibet import ngettext, _
from quodlibet import util
from quodlibet import config
from quodlibet.formats._audio import (TAG_TO_SORT, NUMERIC_ZERO_DEFAULT,
                                      AudioFile, HasKey)
from quodlibet.formats._audio import PEOPLE as _PEOPLE
from quodlibet.pattern import Pattern
try:
    from collections import abc
except ImportError:
    import collections as abc  # type: ignore

from quodlibet.util import is_windows
from quodlibet.util.path import escape_filename, unescape_filename, limit_path
from quodlibet.util.dprint import print_d, print_w
from quodlibet.util.misc import total_ordering, hashable
from .collections import HashedList
from datetime import datetime
from os.path import splitext, basename, dirname, exists
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import ElementTree, Element

XSPF_NS = "http://xspf.org/ns/0/"

PEOPLE = list(_PEOPLE)
# Collections value albumartist more than song artist (Issue 1034)
PEOPLE.remove("albumartist")
PEOPLE.insert(0, "albumartist")

ELPOEP = list(reversed(PEOPLE))
PEOPLE_SCORE = [100 ** i for i in range(len(PEOPLE))]


Real = Union[int, float]


def unweighted_average(nums: List[Real]) -> float:
    """:returns: the average (arithmetic mean) of a list of numbers"""
    return float(sum(nums)) / len(nums)


def unweighted_bayesian_average(nums: List[Real], c: Real = None,
                                m: Real = None) -> float:
    """
    :param nums: the values to be averaged
    :param c: constant chosen based on the typical data set size required for a robust
              estimate of the sample mean
    :param m: prior estimated mean of nums
    :returns: the Bayesian average of an iterable of numbers,
    with parameters defaulting to config specific to ~#rating.
    """
    m = m or config.RATINGS.default
    c = c or config.getfloat("settings", "bayesian_rating_factor")
    return float(m * c + sum(nums)) / (c + len(nums))


def hyperbolic_smoother(x: float, n: Real) -> float:
    """
    :param x: a float in the range [0, 1]
    :param n: how strongly smaller values will be magnified (0 = no magnification)
    :returns: a float in the range [0, 1] with smaller values magnified
    """
    return (n + 1) * x / (n * x + 1)


WeightsAndNumbers = Tuple[List[float], List[Real]]


def smoothed_length_weights_and_nums(songs: List[AudioFile], key: str,
                                     total_length: Real,
                                     smoothing_factor: float) -> WeightsAndNumbers:
    """:returns: smoothed length-based weights and the songs' associated numeric values
    """
    total_length_f = float(total_length)
    length_based_smoothed_weights = []
    values = []
    for s in songs:
        value = s(key)

        # Empty string is returned, if there's no value for key in song
        if value == "":
            continue

        length = s("~#length", 0)
        if length <= 0:
            # will have no impact on the average, so we can just skip it
            continue

        values.append(value)
        weight = hyperbolic_smoother(length / total_length_f, smoothing_factor)

        length_based_smoothed_weights.append(weight)

    return length_based_smoothed_weights, values


def weighted_average(nums: List[Real], weights: List[float]) -> float:
    """:returns: the weighted average (arithmetic mean) of a list of numbers"""
    total_weight = 0.0
    weighted_sum = 0.0
    for num, weight in zip(nums, weights):
        total_weight += weight
        weighted_sum += weight * num

    return weighted_sum / total_weight


def weighted_bayesian_average(nums: List[Real], weights: List[float], c: Real = None,
                              m: Real = None) -> float:
    """:returns: the weighted Bayesian average of an iterable of numbers,
    with parameters defaulting to config specific to ~#rating."""
    m = m or config.RATINGS.default
    c = c or config.getfloat("settings", "bayesian_rating_factor")

    total_weight = 0.0
    weighted_sum = 0.0
    for num, weight in zip(nums, weights):
        total_weight += weight
        weighted_sum += weight * num

    return float(m * c + weighted_sum) / (c + total_weight)


NUM_DEFAULT_FUNCS = {
    "length": "sum",
    "playcount": "sum",
    "added": "max",
    "lastplayed": "max",
    "laststarted": "max",
    "mtime": "max",
    "rating": "bav",
    "skipcount": "sum",
    "year": "min",
    "originalyear": "min",
    "filesize": "sum"
}

NUM_FUNCS = {
    "max": max,
    "min": min,
    "sum": sum,
    "avg": weighted_average,
    "bav": weighted_bayesian_average,
    "unweighted_avg": unweighted_average,
    "unweighted_bav": unweighted_bayesian_average
}

NUM_FUNC_WEIGHTED_TO_UNWEIGHTED = {
    weighted_average: unweighted_average,
    weighted_bayesian_average: unweighted_bayesian_average
}


class Collection:
    """A collection of songs which implements some methods similar to the
    AudioFile class.

    The content of the collection can be changed by changing the content of
    the songs attribute.
    """

    _cache_size = 6
    songs = ()

    def __init__(self):
        """Cache in _cache, LRU key order in _used, keys that return default
        are in _default"""
        self.__cache = {}
        self.__default = set()
        self.__used = []

    def finalize(self):
        """Finalize the collection.
        Call this after songs get added or removed"""
        self.__cache.clear()
        self.__default.clear()
        self.__used = []

    def get(self, key, default=u"", connector=u" - "):
        if not self.songs:
            return default
        if key[:1] == "~" and "~" in key[1:]:
            if not isinstance(default, str):
                return default
            keys = util.tagsplit(key)
            v = map(self.__get_cached_value, keys)

            def default_funct(x):
                if x is None:
                    return default
                return x

            v = map(default_funct, v)
            v = map(lambda x: (isinstance(x, float) and "%.2f" % x) or x, v)
            v = map(
                lambda x: isinstance(x, str) and x or str(x), v)
            return connector.join(filter(None, v)) or default
        else:
            value = self.__get_cached_value(key)
            if value is None:
                return default
            return value

    __call__ = get

    def comma(self, key):
        value = self.get(key)
        return (value if isinstance(value, (int, float))
                else value.replace("\n", ", "))

    def list(self, key):
        v = self.get(key, connector=u"\n") if "~" in key[1:] else self.get(key)
        if isinstance(v, float):
            # Ignore insignificant differences in numeric tags caused
            # by floating point imprecision when converting them to strings
            v = round(v, 8)
        return [] if v == "" else str(v).split("\n")

    def __get_cached_value(self, key):
        if key in self.__cache:
            self.__used.remove(key)
            self.__used.insert(0, key)
            return self.__cache[key]
        elif key in self.__default:
            return None
        else:
            val = self.__get_value(key)
            if val is None:
                self.__default.add(key)
            else:
                self.__used.insert(0, key)
                self.__cache[key] = val
            # Remove the oldest if the cache is full
            if len(self.__used) > self._cache_size:
                self.__cache.pop(self.__used.pop(-1))
        return val

    def __get_value(self, key):
        """This is similar to __call__ in the AudioFile class.
        All internal tags are changed to represent a collection of songs.
        """

        # Using key:<func> runs the resulting list of values
        # through the function before returning it.
        # Numeric keys without a func will default to a reasonable function
        if key.startswith("~#"):
            key = key[2:]

            if key[-4:-3] == ":":
                func_key = key[-3:]
                key = key[:-4]
            elif key[-15:-14] == ":":  # unweighted
                func_key = key[-14:]
                key = key[:-15]
            elif key == "tracks":
                return len(self.songs)
            elif key == "discs":
                return len({song("~#disc", 1) for song in self.songs})
            elif key == "bitrate":
                length = self.__get_value("~#length")
                if not length:
                    return 0
                w = lambda s: s("~#bitrate", 0) * s("~#length", 0)
                return sum(w(song) for song in self.songs) / length
            else:
                # Standard or unknown numeric key.
                # AudioFile will try to cast the values to int,
                # default to avg
                func_key = NUM_DEFAULT_FUNCS.get(key, "avg")

            key = "~#" + key
            func = NUM_FUNCS.get(func_key)
            if func:
                unweighted_func = NUM_FUNC_WEIGHTED_TO_UNWEIGHTED.get(func)

                # we want no length-weighted average of length
                if key == '~#length' and unweighted_func is not None:
                    func = unweighted_func
                elif unweighted_func is not None:
                    smoothing_factor = float(
                        config.getint("settings", "weight_smoothing_factor"))

                    if smoothing_factor >= 0:
                        total_length = self.__get_value("~#length")

                        if total_length:
                            weights, nums = smoothed_length_weights_and_nums(
                                self.songs, key, total_length, smoothing_factor)

                            if not nums:
                                return None

                            return func(nums, weights)

                    # fall back on unweighted version
                    func = unweighted_func

                # If none of the songs can return a numeric key,
                # the album returns default
                values = (song(key) for song in self.songs)
                values = [v for v in values if v != ""]
                return func(values) if values else None
            elif key in NUMERIC_ZERO_DEFAULT:
                return 0
            return None
        elif key[:1] == "~":
            key = key[1:]
            numkey = key.split(":")[0]
            keys = {"people": {}, "peoplesort": {}}
            if key in keys:
                people = keys["people"]
                peoplesort = keys["peoplesort"]
                for song in self.songs:
                    # Rank people by "relevance" -- artists before composers
                    # before performers, then by number of appearances.
                    for w, k in enumerate(ELPOEP):
                        persons = song.list(k)
                        for person in persons:
                            people[person] = (people.get(person, 0) -
                                              PEOPLE_SCORE[w])
                        if k in TAG_TO_SORT:
                            persons = song.list(TAG_TO_SORT[k]) or persons
                        for person in persons:
                            peoplesort[person] = (peoplesort.get(person, 0) -
                                                  PEOPLE_SCORE[w])
                # It's cheaper to get people and peoplesort in one go
                keys["people"] = sorted(people.keys(),
                                        key=people.__getitem__)[:100]
                keys["peoplesort"] = sorted(peoplesort.keys(),
                                            key=peoplesort.__getitem__)[:100]

                ret = keys.pop(key)
                ret = (ret and "\n".join(ret)) or None

                other, values = keys.popitem()
                other = "~" + other
                if not values:
                    self.__default.add(other)
                else:
                    if other in self.__used:
                        self.__used.remove(other)
                    self.__used.append(other)
                    self.__cache[other] = "\n".join(values)
                return ret
            elif numkey == "length":
                length = self.__get_value("~#" + key)
                return None if length is None else util.format_time(length)
            elif numkey == "long-length":
                length = self.__get_value("~#" + key[5:])
                return (None if length is None
                        else util.format_time_long(length))
            elif numkey == "tracks":
                tracks = self.__get_value("~#" + key)
                return (None if tracks is None else
                        ngettext("%d track", "%d tracks", tracks) % tracks)
            elif numkey == "discs":
                discs = self.__get_value("~#" + key)
                if discs > 1:
                    return ngettext("%d disc", "%d discs", discs) % discs
                else:
                    # TODO: check this is correct for discs == 1
                    return None
            elif numkey == "rating":
                rating = self.__get_value("~#" + key)
                if rating is None:
                    return None
                return util.format_rating(rating)
            elif numkey == "filesize":
                size = self.__get_value("~#" + key)
                return None if size is None else util.format_size(size)
            key = "~" + key

        # Nothing special was found, so just take all values of the songs
        # and sort them by their number of appearance
        result = {}
        for song in self.songs:
            for value in song.list(key):
                result[value] = result.get(value, 0) - 1

        values = list(map(lambda x: x[0],
                          sorted(result.items(), key=lambda x: (x[1], x[0]))))
        return "\n".join(values) if values else None


class Album(Collection, HasKey):
    """Like a `Collection` but adds cover scanning, some attributes for sorting
    and uses a set for the songs."""

    @util.cached_property
    def peoplesort(self):
        return util.human_sort_key(self.get("~peoplesort").split("\n")[0])

    @util.cached_property
    def genre(self):
        return util.human_sort_key(self.get("genre").split("\n")[0])

    @property
    def date(self):
        return self.get("date")

    @property
    def title(self):
        return self.get("album")

    def __init__(self, song):
        super().__init__()
        self.songs = set()
        # albumsort is part of the album_key, so every song has the same
        self.sort = util.human_sort_key(song("albumsort"))
        self.key = song.album_key

    @property
    def str_key(self):
        return str(self.key)

    def finalize(self):
        """Finalize this album. Call after songs get added or removed"""
        super().finalize()
        self.__dict__.pop("peoplesort", None)
        self.__dict__.pop("genre", None)

    def __repr__(self):
        return "Album(%s)" % repr(self.key)


@hashable
@total_ordering
class Playlist(Collection, abc.Iterable, HasKey):
    """A Playlist is a `Collection` that has list-like features
    Songs can appear more than once.
    """

    @staticmethod
    def suggested_name_for(songs):
        if len(songs) == 0:
            return _("Empty Playlist")
        elif len(songs) == 1:
            return songs[0].comma("title")
        else:
            return ngettext(
                "%(title)s and %(count)d more",
                "%(title)s and %(count)d more",
                len(songs) - 1) % ({'title': songs[0].comma("title"),
                                    'count': len(songs) - 1})

    def __init__(self, name: str, songs_lib=None, pl_lib=None):
        super().__init__()
        self._list: HashedList = HashedList()
        # we require a file library here with masking
        assert songs_lib is None or hasattr(songs_lib, "masked")
        self.songs_lib = songs_lib

        name = str(name)
        if not name:
            raise ValueError("Playlists must have a name")
        self.name = name

        self.pl_lib = pl_lib
        # Libraries are dict-like so falsey if empty
        if self.pl_lib is None:
            print_w("Playlist initialised without library")
        else:
            self.pl_lib.add([self])
        self.__inhibit_library_signals = False

    @property
    def key(self) -> str:  # type: ignore  # (Note: we want no setter)
        return self.name

    def get(self, key, default=u"", connector=u" - "):
        if key == "~name":
            return self.name
        return super().get(key, default, connector)

    __call__ = get

    # List-like methods, for compatibility with original Playlist class.
    def extend(self, songs: abc.Iterable[AudioFile]):
        self._list.extend(songs)
        self.finalize()
        self._emit_changed(songs, msg="extend")

    def append(self, song):
        ret = self._list.append(song)
        self._emit_changed([song], msg="append")
        self.finalize()
        return ret

    def clear(self):
        self._emit_changed(self._list, msg="clear")
        del self._list[:]
        self.finalize()

    def __iter__(self):
        return iter(self._list)

    def __len__(self):
        return len(self._list)

    def __getitem__(self, index):
        return self._list[index]

    def index(self, value):
        return self._list.index(value)

    def __setitem__(self, key, value):
        self._list[key] = value
        if isinstance(key, slice):
            # TODO: more reliable slice support
            self._emit_changed(value, msg="direct slice set")
        else:
            self._emit_changed([value], msg="direct set")
        self.finalize()

    @property
    def songs(self):
        return [s for s in self._list if not isinstance(s, str)]

    def rename(self, new_name):
        """Changes this playlist's name and re-saves, or raises an `ValueError`
        if the name is not allowed"""
        if new_name == self.name:
            return
        self.name = self._validated_name(new_name)
        self.write()

    def _validated_name(self, new_name):
        """Returns a transformed (or not) name, or raises a `ValueError`
        if the name is not allowed
        """

        new_name = str(new_name)
        if not new_name:
            raise ValueError(_("Playlists must have a name"))
        return new_name

    def add_songs(self, filenames, library):
        # TODO: use Set here to avoid duplicate signals
        changed = []
        for i in range(len(self)):
            if isinstance(self[i], str) and self._list[i] in filenames:
                song = library[self._list[i]]
                self._list[i] = song
                changed.append(song)
        if changed:
            self._emit_changed(changed, msg="add")
        return bool(changed)

    def remove_songs(self,
                     songs: abc.Iterable[AudioFile],
                     leave_dupes: bool = False) -> bool:
        """Removes `songs` from this playlist if they are there,
         removing only the first reference if `leave_dupes` is True
         :returns True if anything was removed
        """
        changed = False
        for song in songs:
            # TODO: document the "library.masked" business
            if self.songs_lib is not None and self.songs_lib.masked(song):
                while True:
                    try:
                        self._list[self.index(song)] = song("~filename")
                    except ValueError:
                        break
                    else:
                        changed = True
            else:
                while song in self._list:
                    self._list.remove(song)
                    changed = True
                    if leave_dupes:
                        break

        def songs_gone():
            return set(songs) - set(self._list)

        if changed:
            self.finalize()
            # Short-circuit logic will avoid the calculation
            if not leave_dupes or songs_gone():
                self._emit_changed(songs, "remove_songs")
        return changed

    @property
    def inhibit(self):
        return self.__inhibit_library_signals

    @inhibit.setter
    def inhibit(self, value):
        self.__inhibit_library_signals = value

    def _emit_changed(self, songs, msg=""):
        if self.pl_lib is not None and not self.inhibit:
            print_d(f"Changed playlist {self!r} ({msg})")
            self.inhibit = True
            # Awkward, but we don't want to make Collection a GObject really
            self.pl_lib.emit('changed', [self])
            self.inhibit = False
        if self.songs_lib and not self.inhibit and songs:
            # See above re: emitting
            self.songs_lib.emit('changed', songs)

    def has_songs(self, songs):
        # TODO(rm): consider the "library.masked" business
        some, all = False, True
        for song in songs:
            found = song in self._list
            some = some or found
            all = all and found
            if some and not all:
                break
        return some, all

    def delete(self):
        if self.pl_lib is not None and not self.inhibit:
            self.pl_lib.remove([self])

    def write(self):
        pass

    @property
    def has_duplicates(self):
        """Returns True if there are any duplicated files in this playlist"""
        return self._list.has_duplicates()

    def shuffle(self):
        """Randomly shuffles this playlist, without weighting"""
        random.shuffle(self._list)
        if self.pl_lib is not None:
            self.pl_lib.changed([self])
        self.write()

    def __eq__(self, other):
        try:
            return self.name == other.name
        except AttributeError:
            return False

    def __lt__(self, other):
        try:
            return self.name < other.name
        except AttributeError:
            return False

    def __hash__(self):
        return id(self)

    def __str__(self):
        songs_text = (ngettext("%d song", "%d songs", len(self.songs))
                      % len(self.songs))
        return u"\"%s\" (%s)" % (self.name, songs_text)


class FileBackedPlaylist(Playlist):
    """A `Playlist` that is stored as a UTF-8 text file of paths"""

    def __init__(self, dir_: _fsnative, filename: _fsnative,
                 songs_lib=None, pl_lib=None, validate: bool = False):
        assert isinstance(dir_, fsnative)
        self.dir = dir_
        name = self.name_for(filename)
        super().__init__(name, songs_lib=songs_lib, pl_lib=pl_lib)
        if validate:
            self.name = self._validated_name(name)
        # Store the actual filename used, not sanitised and validated name
        # This means we can delete imported things properly, etc...
        self._last_fn = os.path.join(dir_, filename)
        try:
            self._populate_from_file()
        except IOError:
            if self.name:
                print_d("Playlist '%s' not found, creating new." % self.name)
                self.write()

    @classmethod
    def name_for(cls, filename: _fsnative) -> str:
        return unescape_filename(filename)

    @classmethod
    def filename_for(cls, filename: str):
        return escape_filename(filename)

    def _populate_from_file(self):
        """Populates, or raises IOError if no file found"""
        library = self.songs_lib
        with open(self.path, "rb") as h:
            for line in h:
                assert library is not None
                try:
                    line = bytes2fsn(line.rstrip(), "utf-8")
                except ValueError:
                    # decoding failed
                    continue
                if line in library:
                    self._list.append(library[line])
                elif library and library.masked(line):
                    self._list.append(line)

    @classmethod
    def new(cls, dir_, base=_("New Playlist"), songs_lib=None, pl_lib=None):
        assert isinstance(dir_, fsnative)

        if not (dir_ and os.path.realpath(dir_)):
            raise ValueError("Invalid playlist directory %r" % (dir_,))

        last_error = None
        for i in range(1000):
            name = "%s %d" % (base, i) if i else base
            fn = cls.filename_for(name)
            try:
                return cls(dir_, fn, songs_lib=songs_lib, pl_lib=pl_lib, validate=True)
            except ValueError as e:
                last_error = e
        raise ValueError("Couldn't create playlist of name '%s' (e.g. %s)"
                         % (base, last_error))

    @classmethod
    def from_songs(cls, dir_, songs, title=None, songs_lib=None, pl_lib=None):
        assert isinstance(dir_, fsnative)
        title = title or cls.suggested_name_for(songs)
        playlist = cls.new(dir_, title, songs_lib=songs_lib, pl_lib=pl_lib)
        playlist.extend(songs)
        return playlist

    @property
    def path(self):
        return os.path.join(self.dir, self.filename_for(self.name))

    @property
    def key(self) -> str:  # type: ignore  # (Note: we want no setter)
        return self.name

    def _validated_name(self, new_name: str) -> str:
        new_name = super()._validated_name(new_name)
        path = os.path.join(self.dir, self.filename_for(new_name))
        if os.path.exists(path):
            raise ValueError(
                _("A playlist named %(name)s already exists at %(path)s")
                % {"name": new_name, "path": path})
        return new_name

    def delete(self):
        self._delete_file(self._last_fn)
        super().delete()

    @classmethod
    def _delete_file(cls, fn):
        print_d(f"Deleting playlist file: {fn!r}")
        try:
            os.unlink(fn)
        except OSError as e:
            print_w(f"Couldn't delete {fn!r} ({e})")

    def write(self):
        fn = self.path
        with open(fn, "wb") as f:
            for song in self._list:
                if isinstance(song, str):
                    f.write(fsn2bytes(song, "utf-8") + b"\n")
                else:
                    f.write(fsn2bytes(song("~filename"), "utf-8") + b"\n")
        if self._last_fn != fn:
            self._delete_file(self._last_fn)
            self._last_fn = fn

    def __str__(self):
        return f"<{type(self).__name__} at {self.path!r}>"


class XSPFBackedPlaylist(FileBackedPlaylist):
    EXT = "xspf"
    CREATOR_PATTERN = Pattern("<artist|<artist>|<~people>>")
    _SAFER = {c: quote(c, safe='')
              for c in ("\\/:*?\"<>|" if is_windows() else "\0/")}

    @classmethod
    def from_playlist(cls, old_pl: FileBackedPlaylist, songs_lib, pl_lib):
        """Migrate from an existing file-based playlist"""

        def backup_for(path: str) -> str:
            base = os.path.join(dirname(path), ".backup")
            if not exists(base):
                print_d("Creating playlist backup directory %s" % base)
                os.mkdir(base)
            return os.path.join(base, basename(path))

        name = old_pl.name
        new = XSPFBackedPlaylist.new(old_pl.dir, name,
                                     songs_lib=songs_lib, pl_lib=pl_lib)
        new.extend(old_pl)
        new.write()
        os.rename(old_pl.path, backup_for(old_pl.path))
        old_pl.delete()
        return new

    def _populate_from_file(self):
        library = self.songs_lib
        try:
            tree = ET.parse(self.path)
            # TODO: validate some top-level tag data
            node = tree.find("title")
            if self.name != node.text:
                print_w("Playlist was named %r in XML instead of %r at %r"
                        % (node.text, self.name, self.path))
            for node in tree.iterfind('.//track'):
                location = node.findtext('location').strip()
                path = location.replace('\n', '').replace('\r', '')
                if path in library:
                    self._list.append(library[path])
                elif library and library.masked(path):
                    self._list.append(path)
                else:
                    # TODO: handle missing playlist items (#3105, #729, #3131)
                    node_dump = ET.tostring(node, method="xml").decode("utf-8")
                    print_w("Couldn't find %r in playlist at %r. "
                            "Perhaps its metadata will help: %r"
                            % (path, self.path, node_dump))
                    self._list.append(path)
                    library.mask(path)
        except ET.ParseError as e:
            print_w("Couldn't load %r (%s)" % (self.path, e))

    @classmethod
    def filename_for(cls, name: str):
        # Manually do *minimal* escaping, to allow near-readable filenames
        for bad, good in cls._SAFER.items():
            name = name.replace(bad, good)
        return path2fsn("%s.%s" % (limit_path(name), cls.EXT))

    @classmethod
    def name_for(cls, file_path: _fsnative) -> str:
        filename, ext = splitext(unescape_filename(file_path))
        if not ext or ext.lower() != (".%s" % cls.EXT):
            raise TypeError("XSPFs should end in '.%s', not '%s'"
                            % (cls.EXT, ext))
        return filename

    def write(self):
        track_list = Element("trackList")
        for song in self._list:
            if isinstance(song, str):
                track = {"location": song}
            else:
                creator = self.CREATOR_PATTERN.format(song)
                track = {
                    "location": song("~filename"),
                    "title": song("title"),
                    "creator": creator,
                    "album": song("album"),
                    "trackNum": song("~#track"),
                    "duration": int(song("~#length") * 1000.)
                }
            track_list.append(self._element_from("track", track))
        playlist = Element("playlist", attrib={"version": "1"})
        playlist.append(self._text_element("title", self.name))
        playlist.append(self._text_element("date", datetime.now().isoformat()))
        playlist.append(track_list)
        tree = ElementTree(playlist)
        ET.register_namespace('', XSPF_NS)
        path = self.path
        print_d(f"Writing {path !r}")
        tree.write(path, encoding="utf-8", xml_declaration=True)
        if self._last_fn != path:
            self._delete_file(self._last_fn)
            self._last_fn = path

    @classmethod
    def _text_element(cls, name: str, value: Any) -> Element:
        el = Element("%s" % name)
        el.text = str(value)
        return el

    @classmethod
    def _element_from(cls, name: str, d: dict) -> Element:
        """Converts a dict to XML etree. Removes falsey nodes"""
        out = Element(name)
        for k, v in d.items():
            if k and v:
                element = (cls._element_from(k, v)
                           if isinstance(v, dict)
                           else cls._text_element(k, v))
                out.append(element)
        return out
