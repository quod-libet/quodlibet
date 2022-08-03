# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import sys
import unittest
import tempfile
import shutil
import atexit
import subprocess
import locale

try:
    import pytest
except ImportError:
    raise SystemExit("pytest missing: sudo apt-get install python3-pytest")

try:
    import xvfbwrapper
except ImportError:
    xvfbwrapper = None

import quodlibet
from quodlibet.util.path import xdg_get_cache_home
from quodlibet import util

from senf import fsnative, path2fsn
from unittest import TestCase as OrigTestCase


class TestCase(OrigTestCase):
    """Adds aliases for equality-type methods.
    Also swaps first and second parameters to support our mostly-favoured
    assertion style e.g. `assertEqual(actual, expected)`"""

    def assertEqual(self, first, second, msg=None):
        super().assertEqual(second, first, msg)

    def assertNotEqual(self, first, second, msg=None):
        super().assertNotEqual(second, first, msg)

    def assertAlmostEqual(self, first, second, places=None, msg=None,
                          delta=None):
        super().assertAlmostEqual(second, first, places, msg, delta)

    def assertNotAlmostEqual(self, first, second, places=None, msg=None,
                             delta=None):
        super().assertNotAlmostEqual(second, first, places, msg, delta)

    # silence deprec warnings about useless renames
    failUnless = OrigTestCase.assertTrue
    failIf = OrigTestCase.assertFalse
    failUnlessRaises = OrigTestCase.assertRaises

    assertEquals = assertEqual
    failUnlessEqual = assertEqual
    failIfEqual = assertNotEqual
    failUnlessAlmostEqual = assertAlmostEqual
    failIfAlmostEqual = assertNotAlmostEqual


skip = unittest.skip
skipUnless = unittest.skipUnless
skipIf = unittest.skipIf


def is_ci():
    """Guesses if this is being run in (Travis, maybe other) CI.
       See https://docs.travis-ci.com/user/environment-variables
    """
    return os.environ.get('CI', "").lower() == 'true'

_DATA_DIR = os.path.join(util.get_module_dir(), "data")
assert isinstance(_DATA_DIR, fsnative)
_TEMP_DIR = None


def get_data_path(filename):
    return os.path.join(_DATA_DIR, path2fsn(filename))


def _wrap_tempfile(func):
    def wrap(*args, **kwargs):
        if kwargs.get("dir") is None and _TEMP_DIR is not None:
            assert isinstance(_TEMP_DIR, fsnative)
            kwargs["dir"] = _TEMP_DIR
        return func(*args, **kwargs)
    return wrap


NamedTemporaryFile = _wrap_tempfile(tempfile.NamedTemporaryFile)


def mkdtemp(*args, **kwargs):
    path = _wrap_tempfile(tempfile.mkdtemp)(*args, **kwargs)
    assert isinstance(path, fsnative)
    return path


def mkstemp(*args, **kwargs):
    fd, filename = _wrap_tempfile(tempfile.mkstemp)(*args, **kwargs)
    assert isinstance(filename, fsnative)
    return (fd, filename)


def init_fake_app():
    from quodlibet import app

    from quodlibet import browsers
    from quodlibet.player.nullbe import NullPlayer
    from quodlibet.library import SongFileLibrary
    from quodlibet.library.librarians import SongLibrarian
    from quodlibet.qltk.quodlibetwindow import QuodLibetWindow, PlayerOptions
    from quodlibet.util.cover import CoverManager
    from quodlibet.qltk.undo import UndoStore

    browsers.init()
    app.name = "Quod Libet"
    app.id = "io.github.quodlibet.QuodLibet"
    app.player = NullPlayer()
    app.library = SongFileLibrary()
    app.library.librarian = SongLibrarian()
    app.cover_manager = CoverManager()
    app.window = QuodLibetWindow(app.library, app.player, headless=True)
    app.player_options = PlayerOptions(app.window)
    app.undo_store = UndoStore()


def destroy_fake_app():
    from quodlibet import app

    app.window.destroy()
    app.library.destroy()
    app.library.librarian.destroy()
    app.player.destroy()

    app.window = app.library = app.player = app.name = app.id = None
    app.cover_manager = None


def dbus_launch_user():
    """Returns a dict with env vars, or an empty dict"""

    try:
        out = subprocess.check_output([
            "dbus-daemon", "--session", "--fork", "--print-address=1",
            "--print-pid=1"])
    except (subprocess.CalledProcessError, OSError):
        return {}
    else:
        out = out.decode("utf-8")
        addr, pid = out.splitlines()
        return {"DBUS_SESSION_BUS_PID": pid, "DBUS_SESSION_BUS_ADDRESS": addr}


def dbus_kill_user(info):
    """Kills the dbus daemon used for testing"""

    if not info:
        return

    try:
        subprocess.check_call(
            ["kill", "-9", info["DBUS_SESSION_BUS_PID"]])
    except (subprocess.CalledProcessError, OSError):
        pass


_BUS_INFO = None
_VDISPLAY = None


def init_test_environ():
    """This needs to be called before any test can be run.

    Before exiting the process call exit_test_environ() to clean up
    any resources created.
    """

    global _TEMP_DIR, _BUS_INFO, _VDISPLAY

    # create a user dir in /tmp and set env vars
    _TEMP_DIR = tempfile.mkdtemp(prefix=fsnative(u"QL-TEST-"))

    # needed for dbus/dconf
    runtime_dir = tempfile.mkdtemp(prefix=fsnative(u"RUNTIME-"), dir=_TEMP_DIR)
    os.chmod(runtime_dir, 0o700)
    os.environ["XDG_RUNTIME_DIR"] = runtime_dir

    # force the old cache dir so that GStreamer can re-use the GstRegistry
    # cache file
    os.environ["XDG_CACHE_HOME"] = xdg_get_cache_home()
    # GStreamer will update the cache if the environment has changed
    # (in Gst.init()). Since it takes 0.5s here and doesn't add much,
    # disable it. If the registry cache is missing it will be created
    # despite this setting.
    os.environ["GST_REGISTRY_UPDATE"] = fsnative(u"no")

    # In flatpak we might get a registry from a different/old flatpak build
    # when testing new versions etc. Better always update in that case.
    if util.is_flatpak():
        del os.environ["GST_REGISTRY_UPDATE"]

    # set HOME and remove all XDG vars that default to it if not set
    home_dir = tempfile.mkdtemp(prefix=fsnative(u"HOME-"), dir=_TEMP_DIR)
    os.environ["HOME"] = home_dir

    # set to new default
    os.environ.pop("XDG_DATA_HOME", None)
    os.environ.pop("XDG_CONFIG_HOME", None)

    # don't use dconf
    os.environ["GSETTINGS_BACKEND"] = "memory"

    # don't use dconf
    os.environ["GSETTINGS_BACKEND"] = "memory"

    # Force the default theme so broken themes don't affect the tests
    os.environ["GTK_THEME"] = "Adwaita"

    if xvfbwrapper is not None:
        _VDISPLAY = xvfbwrapper.Xvfb()
        _VDISPLAY.start()

    _BUS_INFO = None
    if os.name != "nt" and sys.platform != "darwin":
        _BUS_INFO = dbus_launch_user()
        os.environ.update(_BUS_INFO)

    quodlibet.init(no_translations=True, no_excepthook=True)
    quodlibet.app.name = "QL Tests"

    # try to make things the same in case a different locale is active.
    # LANG for gettext, setlocale for number formatting etc.
    # Note: setlocale has to be called after Gtk.init()
    try:
        if os.name != "nt":
            os.environ["LANG"] = locale.setlocale(locale.LC_ALL, "en_US.UTF-8")
        else:
            os.environ["LANG"] = "en_US.utf8"
            locale.setlocale(locale.LC_ALL, "english")
    except locale.Error:
        pass


def exit_test_environ():
    """Call after init_test_environ() and all tests are finished"""

    global _TEMP_DIR, _BUS_INFO, _VDISPLAY

    try:
        shutil.rmtree(_TEMP_DIR)
    except EnvironmentError:
        pass

    dbus_kill_user(_BUS_INFO)

    if _VDISPLAY is not None:
        _VDISPLAY.stop()
        _VDISPLAY = None


# we have to do this on import so the tests work with other test runners
# like py.test which don't know about out setup code and just import
init_test_environ()
atexit.register(exit_test_environ)


def unit(run=[], suite=None, strict=False, exitfirst=False, network=True,
         quality=True):
    """Returns 0 if everything passed"""

    # make glib warnings fatal
    if strict:
        from gi.repository import GLib
        GLib.log_set_always_fatal(
            GLib.LogLevelFlags.LEVEL_CRITICAL |
            GLib.LogLevelFlags.LEVEL_ERROR |
            GLib.LogLevelFlags.LEVEL_WARNING)

    args = []

    if is_ci():
        args.extend(["-p", "no:cacheprovider"])
        args.extend(["-p", "no:stepwise"])

    if run:
        args.append("-k")
        args.append(" or ".join(run))

    skip_markers = []

    if not quality:
        skip_markers.append("quality")

    if not network:
        skip_markers.append("network")

    if skip_markers:
        args.append("-m")
        args.append(" and ".join(["not %s" % m for m in skip_markers]))

    if exitfirst:
        args.append("-x")

    if suite is None:
        args.append("tests")
    else:
        args.append(os.path.join("tests", suite))

    return pytest.main(args=args)


def run_gtk_loop():
    """Exhausts the GTK main loop of any events"""

    # Import late as various version / init checks fail otherwise
    from gi.repository import Gtk
    while Gtk.events_pending():
        Gtk.main_iteration()
