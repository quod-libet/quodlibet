# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
import shutil
from pathlib import Path
from typing import Optional

from _pytest.fixtures import fixture
from gi.repository import Gio

from quodlibet import print_d
from quodlibet.library.file import EventType
from quodlibet.util.path import normalize_path
from tests import mkdtemp, run_gtk_loop


@fixture
def temp_dir() -> Path:
    out_path = Path(mkdtemp())
    yield out_path
    shutil.rmtree(out_path)


class BasicMonitor:

    def __init__(self, path: Path):
        self.changed = []
        f = Gio.File.new_for_path(str(path))
        monitor = f.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        handler_id = monitor.connect("changed", self._file_changed)
        self._monitors = {path: (monitor, handler_id)}
        print_d(f"Monitoring {path!s}")

    def _file_changed(self, _monitor, main_file: Gio.File,
                      other_file: Optional[Gio.File],
                      event_type: Gio.FileMonitorEvent) -> None:
        file_path = main_file.get_path()
        other_path = (Path(normalize_path(other_file.get_path(), True))
                      if other_file else None)
        print_d(f"Got event {event_type} on {file_path}-> {other_path}"
                if other_path else "")
        self.changed.append((event_type, file_path))


class TestMonitor:
    def test_gio_filemonitor(self, temp_dir):
        path = temp_dir
        monitor = BasicMonitor(path)
        some_file = (path / "foo.txt")
        some_file.write_text("test")
        run_gtk_loop()
        assert monitor.changed, "No events after creation"
        recent = monitor.changed.pop()
        assert recent[0] == EventType.CHANGES_DONE_HINT
        some_file.unlink()
        run_gtk_loop()
        assert monitor.changed, "No events after deletion"
        assert monitor.changed.pop()[0] == EventType.DELETED
