# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
from pathlib import Path

from quodlibet.library.file import FileLibrary
from quodlibet.util.path import normalize_path
from tests import mkdtemp
from tests.test_library_libraries import TLibrary, FakeSongFile, FakeAudioFile


class TFileLibrary(TLibrary):
    Fake = FakeSongFile
    Library = FileLibrary

    def test_mask_invalid_mount_point(self):
        new = self.Fake(1)
        self.library.add([new])
        self.failIf(self.library.masked_mount_points)
        self.failUnless(len(self.library))
        self.library.mask("/adsadsafaf")
        self.failIf(self.library.masked_mount_points)
        self.library.unmask("/adsadsafaf")
        self.failIf(self.library.masked_mount_points)
        self.failUnless(len(self.library))

    def test_mask_basic(self):
        new = self.Fake(1)
        self.library.add([new])
        self.failIf(self.library.masked_mount_points)
        self.library.mask(new.mountpoint)
        self.failUnlessEqual(self.library.masked_mount_points,
                             [new.mountpoint])
        self.failIf(len(self.library))
        self.failUnlessEqual(self.library.get_masked(new.mountpoint), [new])
        self.failUnless(self.library.masked(new))
        self.library.unmask(new.mountpoint)
        self.failUnless(len(self.library))
        self.failUnlessEqual(self.library.get_masked(new.mountpoint), [])

    def test_remove_masked(self):
        new = self.Fake(1)
        self.library.add([new])
        self.library.mask(new.mountpoint)
        self.failUnless(self.library.masked_mount_points)
        self.library.remove_masked(new.mountpoint)
        self.failIf(self.library.masked_mount_points)

    def test_content_masked(self):
        new = self.Fake(100)
        new._mounted = False
        self.failIf(self.library.get_content())
        self.library._load_init([new])
        self.failUnless(self.library.masked(new))
        self.failUnless(self.library.get_content())

    def test_init_masked(self):
        new = self.Fake(100)
        new._mounted = False
        self.library._load_init([new])
        self.failIf(self.library.items())
        self.failUnless(self.library.masked(new))

    def test_load_init_nonmasked(self):
        new = self.Fake(200)
        new._mounted = True
        self.library._load_init([new])
        self.failUnlessEqual(list(self.library.values()), [new])

    def test_reload(self):
        new = self.Fake(200)
        self.library.add([new])
        changed = set()
        removed = set()
        self.library.reload(new, changed=changed, removed=removed)
        self.assertTrue(new in changed)
        self.assertFalse(removed)

    def test_move_root(self):
        # TODO: mountpoint tests too
        self.library.filename = "moving"
        root = Path(normalize_path(mkdtemp(), True))
        other_root = Path(normalize_path(mkdtemp(), True))
        new_root = Path(normalize_path(mkdtemp(), True))
        in_song = FakeAudioFile(str(root / "in file.mp3"))
        in_song.sanitize()
        out_song = FakeAudioFile(str(other_root / "out file.mp3"))
        # Make sure they exists
        in_song.sanitize()
        out_song.sanitize()
        assert Path(in_song("~dirname")) == root, "test setup wrong"
        assert Path(out_song("~dirname")) == other_root, "test setup wrong"
        self.library.add([out_song, in_song])

        # Run it by draining the generator
        list(self.library.move_root(root, str(new_root)))
        msg = f"Dir wasn't updated in {root!r} -> {new_root!r} for {in_song.key}"
        assert Path(in_song("~dirname")) == new_root, msg
        assert Path(in_song("~filename")) == (new_root / "in file.mp3")
        assert Path(out_song("~dirname")) == other_root, f"{out_song} was wrongly moved"
        assert in_song._written, "Song wasn't written to disk"
        assert not out_song._written, "Excluded songs was written!"

    def test_remove_roots(self):
        self.library.filename = "removing"
        root = Path(normalize_path(mkdtemp(), True))
        other_root = Path(normalize_path(mkdtemp(), True))
        out_song = FakeAudioFile(str(other_root / "out file.mp3"))
        in_song = FakeAudioFile(str(root / "in file.mp3"))
        in_song.sanitize()
        out_song.sanitize()
        self.library.add([in_song, out_song])
        assert in_song in self.library, "test seems broken"

        # Run it by draining the generator
        list(self.library.remove_roots([root]))

        assert in_song not in self.library
        assert out_song in self.library, "removed too many files"
        assert self.removed == [in_song], "didn't signal the song removal"
        assert not self.changed, "shouldn't have changed any tracks"
