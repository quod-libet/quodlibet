# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import shutil
import os
from collections import defaultdict
from os.path import exists
from xml.etree.ElementTree import ElementTree

import pytest

from quodlibet.library.playlist import PlaylistLibrary
from senf import fsnative

from quodlibet import config, app

from tests import TestCase, mkdtemp
from quodlibet.formats import AudioFile as Fakesong
from quodlibet.formats._audio import NUMERIC_ZERO_DEFAULT, PEOPLE
from quodlibet.util.collection import (Album, Playlist, unweighted_average,
                                       unweighted_bayesian_average, FileBackedPlaylist,
                                       XSPFBackedPlaylist, weighted_average,
                                       weighted_bayesian_average,
                                       smoothed_length_weights_and_nums)
from quodlibet.library.file import FileLibrary
from quodlibet.util import format_rating

config.RATINGS = config.HardCodedRatingsPrefs()

NUMERIC_SONGS = [
    Fakesong({"~filename": fsnative(u"fake1-\xf0.mp3"),
              "~#length": 4, "~#added": 5, "~#lastplayed": 1,
              "~#bitrate": 200, "date": "100", "~#rating": 0.1,
              "originaldate": "2004-01-01", "~#filesize": 101}),
    Fakesong({"~filename": fsnative(u"fake2.mp3"),
              "~#length": 7, "~#added": 7, "~#lastplayed": 88,
              "~#bitrate": 220, "date": "99", "~#rating": 0.3,
              "originaldate": "2002-01-01", "~#filesize": 202}),
    Fakesong({"~filename": fsnative(u"fake3.mp3"),
              "~#length": 1, "~#added": 3, "~#lastplayed": 43,
              "~#bitrate": 60, "date": "33", "~#rating": 0.5,
              "tracknumber": "4/6", "discnumber": "1/2"})
]
AMAZING_SONG = Fakesong({"~#length": 123, "~#rating": 1.0})


class TAlbum(TestCase):
    def setUp(self):
        config.init()

    def test_people_sort(s):
        songs = [
            Fakesong({"albumartist": "aa", "artist": "b\na"}),
            Fakesong({"albumartist": "aa", "artist": "a\na"})
        ]

        album = Album(songs[0])
        album.songs = set(songs)

        s.failUnlessEqual(album.comma("~people"), "aa, a, b")

    def test_peoplesort_sort(s):
        songs = [
            Fakesong({"albumartistsort": "aa", "artist": "b\na"}),
            Fakesong({"albumartist": "aa", "artistsort": "a\na"})
        ]

        album = Album(songs[0])
        album.songs = set(songs)

        s.failUnlessEqual(album.comma("~peoplesort"), "aa, a, b")

    def test_tied_tags(s):
        songs = [
            Fakesong({"artist": "a", "title": "c"}),
            Fakesong({"artist": "a", "dummy": "d\ne"})
        ]

        album = Album(songs[0])
        album.songs = set(songs)

        s.failUnlessEqual(album.comma("~artist~dummy"), "a - d, e")

    def test_tied_num_tags(s):
        songs = [
            Fakesong({"~#length": 5, "title": "c", "~#rating": 0.4}),
            Fakesong({"~#length": 7, "dummy": "d\ne", "~#rating": 0.6}),
            Fakesong({"~#length": 0, "dummy2": 5, "~#rating": 0.5})
        ]

        album = Album(songs[0])
        album.songs = set(songs)

        s.failUnlessEqual(album.comma("~foo~~s~~~"), "")
        s.failUnlessEqual(album.comma("~#length~dummy"), "12 - d, e")
        s.failUnlessEqual(album.comma("~#rating~dummy"), "0.50 - d, e")
        s.failUnlessEqual(album.comma("~#length:sum~dummy"), "12 - d, e")

        # zero length, so the only dummy2 value will be ignored
        s.failUnlessEqual(album.comma("~#dummy2"), '')
        s.failUnlessEqual(album.comma("~#dummy2:unweighted_avg"), 5)
        s.failUnlessEqual(album.comma("~#dummy3"), "")

    def test_internal_tags(s):
        songs = [
            Fakesong({"~#length": 5, "discnumber": "1", "date": "2038"}),
            Fakesong({"~#length": 7, "dummy": "d\ne", "discnumber": "2"})
        ]

        album = Album(songs[0])
        album.songs = set(songs)

        s.failIfEqual(album.comma("~long-length"), "")
        s.failIfEqual(album.comma("~tracks"), "")
        s.failIfEqual(album.comma("~discs"), "")
        s.failUnlessEqual(album.comma("~foo"), "")

        s.failUnlessEqual(album.comma(""), "")
        s.failUnlessEqual(album.comma("~"), "")
        s.failUnlessEqual(album.get("~#"), "")

    def test_numeric_ops(s):
        songs = NUMERIC_SONGS
        album = Album(songs[0])
        album.songs = set(songs)

        s.failUnlessEqual(album.get("~#length"), 12)
        s.failUnlessEqual(album.get("~#length:sum"), 12)
        s.failUnlessEqual(album.get("~#length:max"), 7)
        s.failUnlessEqual(album.get("~#length:min"), 1)
        s.failUnlessEqual(album.get("~#length:avg"), 4)
        s.failUnlessEqual(album.get("~#length:foo"), 0)

        s.failUnlessEqual(album.get("~#added"), 7)
        s.failUnlessEqual(album.get("~#lastplayed"), 88)
        s.failUnlessEqual(album.get("~#bitrate"), 200)
        s.failUnlessEqual(album.get("~#year"), 33)
        s.failUnlessEqual(album.get("~#rating:unweighted_avg"), 0.3)
        s.failUnlessEqual(album.get("~#originalyear"), 2002)

    def test_numeric_comma(self):
        songs = [Fakesong({
            "~#added": 1,
            "~#rating": 0.5,
            "~#bitrate": 42,
            "~#length": 1,
        })]

        album = Album(songs[0])
        album.songs = set(songs)

        self.assertEqual(album.comma("~#added"), 1)
        self.assertEqual(album.comma("~#rating"), 0.5)
        self.assertEqual(album.comma("~#bitrate"), 42)

    def test_numeric_funcs_text(self):
        songs = NUMERIC_SONGS
        album = Album(songs[0])
        album.songs = set(songs)

        self.assertEqual(album("~length:sum"), "0:12")
        self.assertEqual(album("~length:min"), "0:01")
        self.assertEqual(album("~long-length:min"), "1 second")
        self.assertEqual(album("~tracks:min"), "6 tracks")
        self.assertEqual(album("~discs:min"), "2 discs")
        self.assertEqual(album("~rating:min"), format_rating(0.1))
        self.assertEqual(album("~filesize:min"), "0 B")

    def test_single_rating(s):
        songs = [Fakesong({"~#rating": 0.75})]
        album = Album(songs[0])
        album.songs = set(songs)
        # One song should average to its own rating
        s.failUnlessEqual(album.get("~#rating:avg"), songs[0]("~#rating"))
        s.failUnlessEqual(album.get("~#rating:bav"), album.get("~#rating:avg"))

        # with one song, unweighted should be equal to weighted
        s.failUnlessEqual(album.get("~#rating:avg"),
                          album.get("~#rating:unweighted_avg"))
        s.failUnlessEqual(album.get("~#rating:avg"),
                          album.get("~#rating:unweighted_bav"))

    def test_multiple_ratings(s):
        r1, r2 = 1.0, 0.5
        songs = [Fakesong({"~#rating": r1, "~#length": 5}),
                 Fakesong({"~#rating": r2, "~#length": 10})]
        album = Album(songs[0])
        album.songs = set(songs)
        # Standard averaging still available
        s.failUnlessEqual(album("~#rating:unweighted_avg"),
                          unweighted_average([r1, r2]))

        # C = 0.0 => emulate arithmetic mean
        config.set("settings", "bayesian_rating_factor", 0.0)
        s.failUnlessEqual(album("~#rating:bav"), album("~#rating:avg"))
        s.failUnlessEqual(album("~#rating:unweighted_bav"),
                          album("~#rating:unweighted_avg"))

        weights, nums = smoothed_length_weights_and_nums(songs,
            "~#rating", total_length=5 + 10, smoothing_factor=10.0)
        expected = weighted_average(nums, weights)
        s.failUnlessEqual(album("~#rating:avg"), expected)

    def test_bayesian_multiple_ratings(s):
        # separated from above to avoid caching
        c, r1, r2 = 5, 1.0, 0.5
        songs = [Fakesong({"~#rating": r1, "~#length": 5}),
                 Fakesong({"~#rating": r2, "~#length": 17})]
        album = Album(songs[0])
        album.songs = set(songs)

        config.set("settings", "bayesian_rating_factor", float(c))
        s.failUnlessEqual(
            config.getfloat("settings", "bayesian_rating_factor"), float(c))
        expected = unweighted_average(c * [config.RATINGS.default] + [r1, r2])
        s.failUnlessEqual(album("~#rating:unweighted_bav"), expected)

        weights, nums = smoothed_length_weights_and_nums(
            songs, "~#rating", total_length=5 + 17, smoothing_factor=10.0)
        expected = weighted_bayesian_average(nums, weights)

        s.failUnlessEqual(album("~#rating"), expected)

    def test_bayesian_average(s):
        bav = unweighted_bayesian_average
        l = [1, 2, 3, 4]
        a = unweighted_average(l)
        # c=0 => this becomes a mean regardless of m
        s.failUnlessEqual(bav(l, 0, 0), a)
        s.failUnlessEqual(bav(l, 0, 999), a)
        # c=1, m = a (i.e. just adding another mean score) => no effect
        s.failUnlessEqual(bav(l, 1, a), a)
        # Harder ones
        s.failUnlessEqual(bav(l, 5, 2), 20.0 / 9)
        expected = 40.0 / 14
        s.failUnlessEqual(bav(l, 10, 3), expected)
        # Also check another iterable
        s.failUnlessEqual(bav(tuple(l), 10, 3), expected)

    def test_defaults(s):
        failUnlessEq = s.failUnlessEqual
        song = Fakesong({})
        album = Album(song)

        failUnlessEq(album("foo", "x"), "x")

        album.songs.add(song)

        failUnlessEq(album("~#length", "x"), song("~#length", "x"))
        failUnlessEq(album("~#bitrate", "x"), song("~#bitrate", "x"))
        failUnlessEq(album("~#rating", "x"), song("~#rating", "x"))
        failUnlessEq(album("~#playcount", "x"), song("~#playcount", "x"))
        failUnlessEq(album("~#mtime", "x"), song("~#mtime", "x"))
        failUnlessEq(album("~#year", "x"), song("~#year", "x"))

        failUnlessEq(album("~#foo", "x"), song("~#foo", "x"))
        failUnlessEq(album("foo", "x"), song("foo", "x"))
        failUnlessEq(album("~foo", "x"), song("~foo", "x"))

        failUnlessEq(album("~people", "x"), song("~people", "x"))
        failUnlessEq(album("~peoplesort", "x"), song("~peoplesort", "x"))
        failUnlessEq(album("~performer", "x"), song("~performer", "x"))
        failUnlessEq(album("~performersort", "x"), song("~performersort", "x"))

        failUnlessEq(album("~rating", "x"), song("~rating", "x"))

        for p in PEOPLE:
            failUnlessEq(album(p, "x"), song(p, "x"))

        for p in NUMERIC_ZERO_DEFAULT:
            failUnlessEq(album(p, "x"), song(p, "x"))

    def test_methods(s):
        songs = [
            Fakesong({"b": "bb4\nbb1\nbb1",
                      "c": "cc1\ncc3\ncc3",
                      "#d": 0.1}),
            Fakesong({"b": "bb1\nbb1\nbb4",
                      "c": "cc3\ncc1\ncc3",
                      "#d": 0.2})
        ]

        album = Album(songs[0])
        album.songs = set(songs)

        s.failUnlessEqual(album.list("c"), ["cc3", "cc1"])
        s.failUnlessEqual(album.list("~c~b"), ["cc3", "cc1", "bb1", "bb4"])
        s.failUnlessEqual(album.list("#d"), ["0.1", "0.2"])

        s.failUnlessEqual(album.comma("c"), "cc3, cc1")
        s.failUnlessEqual(album.comma("~c~b"), "cc3, cc1 - bb1, bb4")

    def tearDown(self):
        config.quit()


class PlaylistResource:
    def __init__(self, pl: Playlist):
        self.pl = pl

    def __enter__(self):
        return self.pl

    def __exit__(self, *exc_info):
        self.pl.delete()


class TPlaylist(TestCase):
    TWO_SONGS = [
        Fakesong({"~#length": 5, "discnumber": "1", "date": "2038"}),
        Fakesong({"~#length": 7, "dummy": "d\ne", "discnumber": "2"})
    ]

    class FakeLib:

        def __init__(self):
            self.reset()

        def emit(self, name, songs):
            self.emitted[name].extend(songs)

        def masked(self, songs):
            return False

        def reset(self):
            self.emitted = defaultdict(list)

        @property
        def changed(self):
            return self.emitted.get('changed', [])

        @property
        def playlists(self):
            return PlaylistLibrary(self)

    FAKE_LIB = FakeLib()

    def setUp(self):
        self.FAKE_LIB.reset()
        app.library = self.FAKE_LIB

    def pl(self, name, lib=None) -> Playlist:
        return Playlist(name, lib)

    def wrap(self, name, lib=FAKE_LIB):
        return PlaylistResource(self.pl(name, lib))

    def test_equality(s):
        pl = s.pl("playlist")
        pl2 = s.pl("playlist")
        pl3 = s.pl("playlist")
        s.failUnlessEqual(pl, pl2)
        # Debatable
        s.failUnlessEqual(pl, pl3)
        pl4 = s.pl("foobar")
        s.failIfEqual(pl, pl4)
        pl.delete()
        pl2.delete()
        pl3.delete()
        pl4.delete()

    def test_index(s):
        with s.wrap("playlist") as pl:
            songs = s.TWO_SONGS
            pl.extend(songs)
            # Just a sanity check...
            s.failUnlessEqual(songs.index(songs[1]), 1)
            # And now the happy paths..
            s.failUnlessEqual(pl.index(songs[0]), 0)
            s.failUnlessEqual(pl.index(songs[1]), 1)
            # ValueError is what we want here
            try:
                pl.index(Fakesong({}))
                s.fail()
            except ValueError:
                pass

    def test_name_tag(s):
        with s.wrap("a playlist") as pl:
            s.failUnlessEqual(pl("~name"), "a playlist")
            s.failUnlessEqual(pl.get("~name"), "a playlist")

    def test_internal_tags(s):
        with s.wrap("playlist") as pl:
            pl.extend(s.TWO_SONGS)

            s.failIfEqual(pl.comma("~long-length"), "")
            s.failIfEqual(pl.comma("~tracks"), "")
            s.failIfEqual(pl.comma("~discs"), "")
            s.failUnlessEqual(pl.comma("~foo"), "")

            s.failUnlessEqual(pl.comma(""), "")
            s.failUnlessEqual(pl.comma("~"), "")
            s.failUnlessEqual(pl.get("~#"), "")

    def test_numeric_ops(s):
        songs = NUMERIC_SONGS
        with s.wrap("playlist") as pl:
            pl.extend(songs)

            s.failUnlessEqual(pl.get("~#length"), 12)
            s.failUnlessEqual(pl.get("~#length:sum"), 12)
            s.failUnlessEqual(pl.get("~#length:max"), 7)
            s.failUnlessEqual(pl.get("~#length:min"), 1)
            s.failUnlessEqual(pl.get("~#length:unweighted_avg"), 4)
            s.failUnlessEqual(pl.get("~#length:foo"), 0)

            s.failUnlessEqual(pl.get("~#rating:unweighted_avg"),
                              unweighted_average([0.1, 0.3, 0.5]))

            weights, nums = smoothed_length_weights_and_nums(
                songs, "~#rating", total_length=12, smoothing_factor=10.0)
            expected = weighted_average(nums, weights)
            s.failUnlessEqual(pl.get("~#rating"), expected)

            s.failUnlessEqual(pl.get("~#filesize"), 303)

            s.failUnlessEqual(pl.get("~#added"), 7)
            s.failUnlessEqual(pl.get("~#lastplayed"), 88)
            s.failUnlessEqual(pl.get("~#bitrate"), 200)
            s.failUnlessEqual(pl.get("~#year"), 33)
            s.failUnlessEqual(pl.get("~#originalyear"), 2002)

    def test_updating_aggregates_extend(s):
        with s.wrap("playlist") as pl:
            pl.extend(NUMERIC_SONGS)
            old_length = pl.get("~#length")
            old_size = pl.get("~#filesize")

            # Double the playlist
            pl.extend(NUMERIC_SONGS)

            new_length = pl.get("~#length")
            new_size = pl.get("~#filesize")
            s.failUnless(new_length > old_length,
                         msg="Ooops, %d <= %d" % (new_length, old_length))

            s.failUnless(new_size > old_size,
                         msg="Ooops, %d <= %d" % (new_size, old_size))

    def test_updating_aggregates_append(s):
        with s.wrap("playlist") as pl:
            pl.extend(NUMERIC_SONGS)
            old_rating = pl.get("~#rating")

            pl.append(AMAZING_SONG)

            new_rating = pl.get("~#filesize")
            s.failUnless(new_rating > old_rating)

    def test_updating_aggregates_clear(s):
        with s.wrap("playlist") as pl:
            pl.extend(NUMERIC_SONGS)
            s.failUnless(pl.get("~#length"))

            pl.clear()
            s.failIf(pl.get("~#length"))

    def test_updating_aggregates_remove_songs(s):
        with s.wrap("playlist") as pl:
            pl.extend(NUMERIC_SONGS)
            s.failUnless(pl.get("~#length"))

            pl.remove_songs(NUMERIC_SONGS)
            s.failIf(pl.get("~#length"))

    def test_listlike(s):
        with s.wrap("playlist") as pl:
            pl.extend(NUMERIC_SONGS)
            s.failUnlessEqual(NUMERIC_SONGS[0], pl[0])
            s.failUnlessEqual(NUMERIC_SONGS[1:2], pl[1:2])
            s.failUnless(NUMERIC_SONGS[1] in pl)

    def test_extend_signals(s):
        with s.wrap("playlist") as pl:
            pl.extend(NUMERIC_SONGS)
            s.failUnlessEqual(s.FAKE_LIB.changed, NUMERIC_SONGS)

    def test_append_signals(s):
        with s.wrap("playlist") as pl:
            song = NUMERIC_SONGS[0]
            pl.append(song)
            s.failUnlessEqual(s.FAKE_LIB.changed, [song])

    def test_clear_signals(s):
        with s.wrap("playlist") as pl:
            pl.extend(NUMERIC_SONGS)
            pl.clear()
            s.failUnlessEqual(s.FAKE_LIB.changed, NUMERIC_SONGS * 2)

    def test_make(self):
        with self.wrap("Does not exist") as pl:
            self.failIf(len(pl))
            self.failUnlessEqual(pl.name, "Does not exist")

    def test_rename_working(self):
        with self.wrap("Foobar") as pl:
            assert pl.name == "Foobar"
            pl.rename("Foo Quuxly")
            assert pl.name == "Foo Quuxly"
            # Rename should not fire signals
            self.failIf(self.FAKE_LIB.changed)

    def test_rename_nothing(self):
        with self.wrap("Foobar") as pl:
            self.failUnlessRaises(ValueError, pl.rename, "")

    def test_no_op_rename(self):
        with self.wrap("playlist") as pl:
            pl.rename("playlist")
            self.failUnlessEqual(pl.name, "playlist")

    def test_duplicates_single_item(self):
        with self.wrap("playlist") as pl:
            pl.append(self.TWO_SONGS[0])
            self.failIf(pl.has_duplicates)
            pl.append(self.TWO_SONGS[0])
            self.failUnless(pl.has_duplicates)

    def test_duplicates(self):
        with self.wrap("playlist") as pl:
            pl.extend(self.TWO_SONGS)
            pl.extend(self.TWO_SONGS)
            self.failUnlessEqual(len(pl), 4)
            self.failUnless(pl.has_duplicates,
                            ("Playlist has un-detected duplicates: %s "
                             % "\n".join([str(s) for s in pl._list])))

    def test_remove_leaving_duplicates(self):
        with self.wrap("playlist") as pl:
            pl.extend(self.TWO_SONGS)
            [first, second] = self.TWO_SONGS
            pl.extend(NUMERIC_SONGS + self.TWO_SONGS)
            self.failUnlessEqual(len(self.FAKE_LIB.changed), 7)
            self.FAKE_LIB.reset()
            pl.remove_songs(self.TWO_SONGS, leave_dupes=True)
            self.failUnless(first in pl)
            self.failUnless(second in pl)
            self.failIf(len(self.FAKE_LIB.changed))

    def test_remove_fully(self):
        with self.wrap("playlist") as pl:
            pl.extend(self.TWO_SONGS * 2)
            self.FAKE_LIB.reset()
            pl.remove_songs(self.TWO_SONGS, leave_dupes=False)
            self.failIf(len(pl))
            self.failUnlessEqual(self.FAKE_LIB.changed, self.TWO_SONGS)


class TFileBackedPlaylist(TPlaylist):
    Playlist = FileBackedPlaylist

    def setUp(self):
        super().setUp()
        self.temp = mkdtemp()
        self.temp2 = mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp)
        shutil.rmtree(self.temp2)

    def pl(self, name, lib=None):
        fn = self.Playlist.filename_for(name)
        return self.Playlist(self.temp, fn, lib)

    def new_pl(self, name, lib=None):
        return self.Playlist.new(self.temp, name, lib)

    def test_from_songs(self):
        pl = self.Playlist.from_songs(self.temp, NUMERIC_SONGS)
        self.failUnlessEqual(pl.songs, NUMERIC_SONGS)
        pl.delete()

    def test_read(self):
        lib = FileLibrary("foobar")
        lib.add(NUMERIC_SONGS)
        with self.wrap("playlist", lib) as pl:
            pl.extend(NUMERIC_SONGS)
            pl.write()
            self.assertEqual(len(pl), len(NUMERIC_SONGS))

    def test_write(self):
        with self.wrap("playlist") as pl:
            pl.extend(NUMERIC_SONGS)
            pl.extend([fsnative(u"xf0xf0")])
            pl.write()

            with open(pl.path, "rb") as h:
                self.assertEqual(len(h.read().splitlines()),
                                 len(NUMERIC_SONGS) + 1)

    def test_difficult_names(self):
        lib = FileLibrary("foobar")
        lib.add(NUMERIC_SONGS)
        name = "c:?\"problem?\" / foo* / 100% ə! COM"
        with self.wrap(name, lib) as pl:
            pl.extend(NUMERIC_SONGS)
            pl.write()
            assert pl.songs == NUMERIC_SONGS
            with self.wrap(name, lib) as pl2:
                assert pl2.songs == NUMERIC_SONGS

    def test_symmetric(self):
        P = self.Playlist
        for name in ("bar & foo?", "100% cool.now 😀", "COM:", "a/b"):
            new_name = P.name_for(P.filename_for(name))
            assert new_name == name

    def test_make_dup(self):
        p1 = self.new_pl("Does not exist")
        p2 = self.new_pl("Does not exist")
        self.failUnlessEqual(p1.name, "Does not exist")
        self.failUnless(p2.name.startswith("Does not exist"))
        self.failIfEqual(p1.name, p2.name)
        p1.delete()
        p2.delete()

    def test_rename_removes(self):
        with self.wrap("foo") as pl:
            pl.rename("bar")
            self.failUnless(exists(self.path_for('bar')))
            self.failIf(exists(self.path_for('foo')))

    def path_for(self, name: str):
        return os.path.join(self.temp, self.Playlist.filename_for(name))

    def test_rename_fails_if_file_exists(self):
        with self.wrap("foo") as foo:
            with self.wrap("bar"):
                with pytest.raises(ValueError):
                    foo.rename("bar")

    def test_masked_handling(self):
        if os.name == "nt":
            # FIXME: masking isn't properly implemented on Windows
            return
        # playlists can contain songs and paths for masked handling..
        lib = FileLibrary("foobar")
        with self.wrap("playlist", lib) as pl:
            song = Fakesong({"date": "2038", "~filename": fsnative(u"/fake")})
            song.sanitize()
            lib.add([song])

            # mask and update
            lib.mask("/")
            pl.append(song)
            pl.remove_songs([song])
            self.failUnless("/fake" in pl)

            pl.extend(self.TWO_SONGS)

            # check if collections can handle the mix
            self.failUnlessEqual(pl("date"), "2038")

            # unmask and update
            lib.unmask("/")
            pl.add_songs(["/fake"], lib)
            self.failUnless(song in pl)

            lib.destroy()

    def test_delete_emits_no_signals(self):
        lib = self.FakeLib()
        with self.wrap("playlist", lib=lib) as pl:
            pl.extend(self.TWO_SONGS)
            # We don't care about changed signals on extend...
            lib.reset()
            pl.delete()
            assert not lib.emitted, "Deleting caused library signals"
        # Second time, just in case
        assert not lib.emitted, "Deleting again caused library signals"


class TXPSFBackedPlaylist(TFileBackedPlaylist):
    Playlist = XSPFBackedPlaylist

    def setUp(self):
        super().setUp()
        self.temp = mkdtemp()
        self.temp2 = mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp)
        shutil.rmtree(self.temp2)

    def path_for(self, name: str):
        return os.path.join(self.temp, "%s.xspf" % (name,))

    def test_write(self):
        with self.wrap("playlist") as pl:
            pl.extend(NUMERIC_SONGS)
            pl.extend([fsnative(u"xf0xf0")])
            pl.write()

            assert exists(pl.path), "File doesn't exist"
            root = ElementTree().parse(pl.path)
            assert root.tag == 'playlist'
            tracks = root.findall(".//track")
            assert len(tracks) == 4, "Hmm found %s" % tracks
            assert tracks[-1].find('location').text == "xf0xf0"
