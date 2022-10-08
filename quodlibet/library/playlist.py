# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import re
from typing import Iterable, Generator, Optional

import quodlibet
from quodlibet import print_d, print_w, print_e, ngettext, _
from quodlibet.formats import AudioFile
from quodlibet.library.base import Library
from quodlibet.util.collection import (Playlist, XSPFBackedPlaylist,
                                       FileBackedPlaylist)
from senf import text2fsn, _fsnative, fsn2text

_DEFAULT_PLAYLIST_DIR = text2fsn(os.path.join(quodlibet.get_data_dir(), "playlists"))
"""Directory for playlist files"""

HIDDEN_RE = re.compile(r'^\.\w[^.]*')
"""Hidden-like files, to ignored"""

_MIN_NON_EMPTY_PL_BYTES = 4
"""Arbitrary minimum file size for a legacy non-empty playlist file"""


class PlaylistLibrary(Library[str, Playlist]):
    """A PlaylistLibrary listens to a SongLibrary, and keeps tracks of playlists
    of these songs.

    The library behaves like a dictionary: the keys are playlist names,
    the values are Playlist objects.
    """

    def __init__(self, library: Library, pl_dir: _fsnative = _DEFAULT_PLAYLIST_DIR):
        self.librarian = None
        super().__init__(f"{type(self).__name__} for {library._name}")
        print_d(f"Initializing Playlist Library {self} to watch {library._name!r}")
        self.pl_dir = pl_dir
        if library is None:
            raise ValueError("Need a library to listen to")
        self._library = library
        self._read_playlists(library)

        self._rsig = library.connect('removed', self.__songs_removed)
        self._csig = library.connect('changed', self.__songs_changed)

    def _read_playlists(self, library) -> None:
        print_d(f"Reading playlist directory {self.pl_dir} (library: {library})")
        try:
            fns = os.listdir(self.pl_dir)
        except FileNotFoundError as e:
            print_w(f"No playlist dir found in {self.pl_dir!r}, creating. ({e})")
            os.mkdir(self.pl_dir)
            fns = []

        # Populate this library by relying on existing signal passing.
        # Weird, but allows keeping the logic in one place
        failed = []
        for fn in fns:
            full_path = os.path.join(self.pl_dir, fn)
            if os.path.isdir(full_path):
                continue
            if HIDDEN_RE.match(fsn2text(fn)):
                print_d(f"Ignoring hidden file {fn!r}")
                continue
            try:
                XSPFBackedPlaylist(self.pl_dir, fn, songs_lib=library, pl_lib=self)
            except TypeError as e:
                # Don't add to library - it's temporary
                legacy = FileBackedPlaylist(self.pl_dir, fn,
                                            songs_lib=library, pl_lib=None)
                if not len(legacy):
                    try:
                        size = os.stat(legacy._last_fn).st_size
                        if size >= _MIN_NON_EMPTY_PL_BYTES:
                            data = {"filename": fn, "size": size / 1024}
                            print_w(_("No library songs found in legacy playlist "
                                      "%(filename)r (of size %(size).1f kB).") % data +
                                    " " +
                                    _("Have you changed library root dir(s), "
                                      "but not this playlist?"))
                            continue
                    except OSError:
                        print_e(f"Problem reading {legacy._last_fn!r}")
                        continue
                    finally:
                        failed.append(fn)
                print_w(f"Converting {fn!r} to XSPF format ({e})")
                XSPFBackedPlaylist.from_playlist(legacy, songs_lib=library, pl_lib=self)
            except EnvironmentError:
                print_w(f"Invalid Playlist {fn!r}")
                failed.append(fn)
        if failed:
            total = len(failed)
            print_e(ngettext("%d playlist failed to convert",
                             "%d playlists failed to convert", total) % len(failed))

    def create(self, name_base: Optional[str] = None) -> Playlist:
        if name_base:
            return XSPFBackedPlaylist.new(self.pl_dir, name_base,
                                          songs_lib=self._library, pl_lib=self)
        return XSPFBackedPlaylist.new(self.pl_dir, songs_lib=self._library, pl_lib=self)

    def create_from_songs(self, songs: Iterable[AudioFile], title=None) -> Playlist:
        """Creates a playlist visible to this library"""
        return XSPFBackedPlaylist.from_songs(
            self.pl_dir, songs, title=title, songs_lib=self._library, pl_lib=self)

    def destroy(self):
        for sig in [self._rsig, self._csig]:
            self._library.disconnect(sig)

    def playlists_featuring(self, song: AudioFile) -> Generator[Playlist, None, None]:
        """Returns a generator yielding playlists in which this song appears"""
        return (pl for pl in self if song in pl._list)

    def __songs_removed(self, library, songs):
        print_d(f"Removing {len(songs)} song(s) "
                f"across {len(self)} playlist(s) in {self}")
        changed = {pl for pl in self if pl.remove_songs(songs)}
        if changed:
            for pl in changed:
                pl.write()
            self.changed(changed)

    def __songs_changed(self, library, songs) -> None:
        # Q: what if the changes are entirely due to changes *from* this library?
        # A: seems safest to still emit 'changed' as collections can cache metadata etc
        changed = set()
        for playlist in self:
            for song in songs:
                if song in playlist.songs:
                    changed.add(playlist)
                    # It's definitely changed now, nothing else is interesting
                    break
        if changed:
            # TODO: only write if anything *persisted* changes (#3622)
            #  i.e. not internal stuff (notably: ~playlists itself)
            for pl in changed:
                pl.finalize()
                pl.write()
            self.changed(changed)

    def recreate(self, playlist: Playlist, songs: Iterable[AudioFile]):
        """Keep a playlist but entirely replace its contents
        This is useful for applying new external sorting etc"""
        playlist._list.clear()
        playlist._list.extend(songs)
        playlist.finalize()
        playlist.write()
        self.changed([playlist])
