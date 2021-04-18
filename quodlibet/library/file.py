# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import time
from pathlib import Path
from typing import Generator, Set, Iterable

from quodlibet import print_d, print_w, _, formats
from quodlibet.formats import AudioFileError, AudioFile
from quodlibet.library.base import PicklingLibrary, iter_paths
from quodlibet.qltk.notif import Task
from quodlibet.util.path import ismount, unexpand, normalize_path
from senf import fsn2text, fsnative


class FileLibrary(PicklingLibrary[fsnative, AudioFile]):
    """A library containing items on a local(-ish) filesystem.

    These must support the valid, exists, mounted, and reload methods,
    and have a mountpoint attribute.
    """

    def __init__(self, name=None):
        super().__init__(name)
        self._masked = {}

    def _load_init(self, items):
        """Add many items to the library, check if the
        mountpoints are available and mark items as masked if not.

        Does not check if items are valid.
        """

        mounts = {}
        contents = self._contents
        masked = self._masked

        for item in items:
            mountpoint = item.mountpoint

            if mountpoint not in mounts:
                is_mounted = ismount(mountpoint)

                # In case mountpoint is mounted through autofs we need to
                # access a sub path for it to mount
                # https://github.com/quodlibet/quodlibet/issues/2146
                if not is_mounted:
                    item.exists()
                    is_mounted = ismount(mountpoint)

                mounts[mountpoint] = is_mounted
                # at least one not mounted, make sure masked has an entry
                if not is_mounted:
                    masked.setdefault(mountpoint, {})

            if mounts[mountpoint]:
                contents[item.key] = item
            else:
                masked[mountpoint][item.key] = item

    def _load_item(self, item, force=False):
        """Add an item, or refresh it if it's already in the library.
        No signals will be fired.
        Return a tuple of booleans: (changed, removed)
        """
        print_d(f"Loading {item.key!r}", self._name)
        valid = item.valid()

        # The item is fine; add it if it's not present.
        if not force and valid:
            print_d(f"{item.key!r} is valid.", self._name)
            self._contents[item.key] = item
            return False, False
        else:
            # Either we should force a load, or the item is not okay.
            # We're going to reload; this could change the key.  So
            # remove the item if it's currently in.
            try:
                del self._contents[item.key]
            except KeyError:
                present = False
            else:
                present = True
            # If the item still exists, reload it.
            if item.exists():
                try:
                    item.reload()
                except AudioFileError:
                    print_w(f"Error reloading {item.key!r}", self._name)
                    return False, True
                else:
                    print_d(f"Reloaded {item.key!r}.", self._name)
                    self._contents[item.key] = item
                    return True, False
            elif not item.mounted():
                # We don't know if the item is okay or not, since
                # it's not not mounted. If the item was present
                # we need to mark it as removed.
                print_d(f"Masking {item.key!r}", self._name)
                self._masked.setdefault(item.mountpoint, {})
                self._masked[item.mountpoint][item.key] = item
                return False, present
            else:
                # The item doesn't exist at all anymore. Mark it as
                # removed if it was present, otherwise nothing.
                print_d(f"Ignoring (so removing) {item.key!r}.", self._name)
                return False, present

    def reload(self, item, changed=None, removed=None):
        """Reload a song, possibly noting its status.

        If sets are given, it assumes the caller will handle signals,
        and only updates the sets. Otherwise, it handles signals
        itself. It *always* handles library contents, so do not
        try to remove (again) a song that appears in the removed set.
        """

        was_changed, was_removed = self._load_item(item, force=True)
        assert not (was_changed and was_removed)

        if was_changed:
            if changed is None:
                self.emit('changed', {item})
            else:
                changed.add(item)
        elif was_removed:
            if removed is None:
                self.emit('removed', {item})
            else:
                removed.add(item)

    def rebuild(self, paths, force=False, exclude=[], cofuncid=None):
        """Reload or remove songs if they have changed or been deleted.

        This generator rebuilds the library over the course of iteration.

        Any paths given will be scanned for new files, using the 'scan'
        method.

        Only items present in the library when the rebuild is started
        will be checked.

        If this function is copooled, set "cofuncid" to enable pause/stop
        buttons in the UI.
        """

        print_d(f"Rebuilding, force is {force}", self._name)

        task = Task(_("Library"), _("Checking mount points"))
        if cofuncid:
            task.copool(cofuncid)
        for i, (point, items) in task.list(enumerate(self._masked.items())):
            if ismount(point):
                self._contents.update(items)
                del self._masked[point]
                self.emit('added', list(items.values()))
                yield True

        task = Task(_("Library"), _("Scanning library"))
        if cofuncid:
            task.copool(cofuncid)
        changed, removed = set(), set()
        for i, (key, item) in task.list(enumerate(sorted(self.items()))):
            if key in self._contents and force or not item.valid():
                self.reload(item, changed, removed)
                # These numbers are pretty empirical. We should yield more
            # often than we emit signals; that way the main loop stays
            # interactive and doesn't get bogged down in updates.
            if len(changed) >= 200:
                self.emit('changed', changed)
                changed = set()
            if len(removed) >= 200:
                self.emit('removed', removed)
                removed = set()
            if len(changed) > 20 or i % 200 == 0:
                yield True
        print_d(f"Removing {len(removed)}, changing {len(changed)}).", self._name)
        if removed:
            self.emit('removed', removed)
        if changed:
            self.emit('changed', changed)

        for value in self.scan(paths, exclude, cofuncid):
            yield value

    def add_filename(self, filename, add=True):
        """Add a file based on its filename.

        Subclasses must override this to open the file correctly.
        """

        raise NotImplementedError

    def contains_filename(self, filename):
        """Returns if a song for the passed filename is in the library.

        Returns:
            bool
        """

        raise NotImplementedError

    def scan(self, paths, exclude=[], cofuncid=None):

        def need_yield(last_yield=[0]):
            current = time.time()
            if abs(current - last_yield[0]) > 0.015:
                last_yield[0] = current
                return True
            return False

        def need_added(last_added=[0]):
            current = time.time()
            if abs(current - last_added[0]) > 1.0:
                last_added[0] = current
                return True
            return False

        # first scan each path for new files
        paths_to_load = []
        for scan_path in paths:
            print_d(f"Scanning {scan_path}", self._name)
            desc = _("Scanning %s") % (fsn2text(unexpand(scan_path)))
            with Task(_("Library"), desc) as task:
                if cofuncid:
                    task.copool(cofuncid)

                for real_path in iter_paths(scan_path, exclude=exclude):
                    if need_yield():
                        task.pulse()
                        yield
                    # skip unknown file extensions
                    if not formats.filter(real_path):
                        continue
                    # already loaded
                    if self.contains_filename(real_path):
                        continue
                    paths_to_load.append(real_path)

        yield

        # then (try to) load all new files
        with Task(_("Library"), _("Loading files")) as task:
            if cofuncid:
                task.copool(cofuncid)

            added = []
            for real_path in task.gen(paths_to_load):
                item = self.add_filename(real_path, False)
                if item is not None:
                    added.append(item)
                    if len(added) > 100 or need_added():
                        self.add(added)
                        added = []
                        yield
                if added and need_yield():
                    yield
            if added:
                self.add(added)
                added = []
                yield True

    def get_content(self):
        """Return visible and masked items"""

        items = list(self.values())
        for masked in self._masked.values():
            items.extend(masked.values())

        # Item keys are often based on filenames, in which case
        # sorting takes advantage of the filesystem cache when we
        # reload/rescan the files.
        items.sort(key=lambda item: item.key)

        return items

    def masked(self, item):
        """Return true if the item is in the library but masked."""
        try:
            point = item.mountpoint
        except AttributeError:
            # Checking a key.
            for point in self._masked.values():
                if item in point:
                    return True
        else:
            # Checking a full item.
            return item in self._masked.get(point, {}).values()

    def unmask(self, point):
        print_d(f"Unmasking {point!r}", self._name)
        items = self._masked.pop(point, {})
        if items:
            self.add(items.values())

    def mask(self, point):
        print_d(f"Masking {point!r}", self._name)
        removed = {}
        for item in self.values():
            if item.mountpoint == point:
                removed[item.key] = item
        if removed:
            self.remove(removed.values())
            self._masked.setdefault(point, {}).update(removed)

    @property
    def masked_mount_points(self):
        """List of mount points that contain masked items"""

        return list(self._masked.keys())

    def get_masked(self, mount_point):
        """List of items for a mount point"""

        return list(self._masked.get(mount_point, {}).values())

    def remove_masked(self, mount_point):
        """Remove all songs for a masked point"""

        self._masked.pop(mount_point, {})

    def move_root(self, old_root: str, new_root: fsnative) \
        -> Generator[None, None, None]:
        """
        Move the root for all songs in a given (scan) directory.

        We avoid dereferencing the destination, to allow users things like:
          1. Symlink new_path -> old_root
          2. Move QL root to new_path
          3. Remove symlink
          4. Move audio files: old_root -> new_path

        """
        old_path = Path(normalize_path(old_root, canonicalise=True)).expanduser()
        new_path = Path(normalize_path(new_root)).expanduser()
        if not old_path.is_dir():
            raise ValueError(f"Source {old_path!r} is not a directory")
        if not new_path.is_dir():
            raise ValueError(f"Destination {new_path!r} is not a directory")
        print_d(f"{self._name}: checking entire library for {old_path!r}")
        missing: Set[AudioFile] = set()
        changed = set()
        total = len(self)
        if not total:
            return
        with Task(_("Library"), _("Moving library files")) as task:
            yield
            for i, song in enumerate(list(self.values())):
                task.update(i / total)
                key = normalize_path(song.key)
                path = Path(key)
                if old_path in path.parents:
                    # TODO: more Pathlib-friendly dir replacement...
                    new_key = key.replace(str(old_path), str(new_path), 1)
                    new_key = normalize_path(new_key, canonicalise=False)
                    if new_key == key:
                        print_w(f"Substitution failed for {key!r}")
                    # We need to update ~filename and ~mountpoint
                    song.sanitize()
                    song.write()
                    if self.move_song(song, new_key):
                        changed.add(song)
                    else:
                        missing.add(song)
                elif not (i % 1000):
                    print_d(f"Not moved, for example: {key!r}")
                if not i % 100:
                    yield
            self.changed(changed)
            if missing:
                print_w(f"Couldn't find {len(list(missing))} files: {missing}")
        yield
        self.save()
        print_d(f"Done moving to {new_path!r}.")

    def move_song(self, song: AudioFile, new_path: fsnative) -> bool:
        """Updates the location of a song, without touching the file.

        :returns: True if it was could be found (and moved)
        """
        existed = True
        key = song.key
        print_d(f"Moving {key!r} -> {new_path!r}")
        try:
            del self._contents[key]  # type: ignore
        except KeyError:
            existed = False
            # Continue - maybe it's already moved
        song.sanitize(new_path)
        self._contents[new_path] = song
        return existed

    def remove_roots(self, old_roots: Iterable[str]) -> Generator[None, None, None]:
        """Remove library roots (scandirs) entirely, and all their songs"""
        old_paths = [Path(normalize_path(root, canonicalise=True)).expanduser()
                     for root in old_roots]
        total = len(self)
        removed = set()
        print_d(f"Removing library roots {old_roots} from {self._name} library")
        yield
        with Task(_("Library"), _("Removing library files")) as task:
            for i, song in enumerate(list(self.values())):
                task.update(i / total)
                key = normalize_path(song.key)
                song_path = Path(key)
                if any(path in song_path.parents for path in old_paths):
                    removed.add(song)
                if not i % 100:
                    yield
        if removed:
            self.remove(removed)
        else:
            print_d(f"No tracks in {old_roots} to remove from {self._name}")
