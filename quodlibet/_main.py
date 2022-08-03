# Copyright 2012 Christoph Reiter
#           2020 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import sys

from senf import path2fsn

from quodlibet import util
from quodlibet import const
from quodlibet import build
from quodlibet.util import cached_func, windows, set_process_title, is_osx
from quodlibet.util.dprint import print_d
from quodlibet.util.path import mkdir, xdg_get_config_home, xdg_get_cache_home


PLUGIN_DIRS = ["editing", "events", "playorder", "songsmenu", "playlist",
               "gstreamer", "covers", "query"]


class Application:
    """A main application class for controlling the application as a whole
    and accessing sub-modules.

    window    - The main window which is present as long as QL is running
    library   - The main library (see library.SongFileLibrary)
    librarian - The main (and atm only) librarian (see library.SongLibrarian)
    player    - see player._base

    quit()    - Quit the application

    """

    window = None
    library = None
    player = None

    player_options = None
    """A PlayerOptions instance or None in case there is no playback support"""

    cover_manager = None

    name = None
    """The application name e.g. 'Quod Libet'"""

    description = None
    """A short description of the application"""

    id = None
    """The application ID e.g. 'io.github.quodlibet.QuodLibet'"""

    process_name = None
    """e.g. quodlibet"""

    is_quitting = False
    """True after quit() is called at least once"""

    is_restarting = False
    """True if the program should restart after quitting"""

    undo_store = None
    """The global UndoStore"""

    @property
    def icon_name(self):
        return self.id

    @property
    def symbolic_icon_name(self):
        return "%s-symbolic" % self.icon_name

    @property
    def librarian(self):
        return self.library.librarian

    @property
    def browser(self):
        return self.window.browser

    def restart(self):
        self.is_restarting = True
        self.quit()

    def quit(self):
        from gi.repository import GLib

        self.is_quitting = True

        def idle_quit():
            if self.window:
                self.window.destroy()

        # so this can be called from a signal handler and before
        # the main loop starts
        GLib.idle_add(idle_quit, priority=GLib.PRIORITY_HIGH)

    def show(self):
        from quodlibet.qltk import Window
        for window in Window.windows:
            window.show()

    def present(self):
        # deiconify is needed if the window is on another workspace
        from quodlibet.qltk import Window
        for window in Window.windows:
            window.deiconify()
            window.present()

    def hide(self):
        from quodlibet.qltk import Window
        for window in Window.windows:
            window.hide()

app = Application()


@cached_func
def get_base_dir():
    """The path to the quodlibet package"""

    return util.get_module_dir()


@cached_func
def get_image_dir():
    """The path to the image directory in the quodlibet package"""

    return os.path.join(get_base_dir(), "images")


@cached_func
def get_cache_dir():
    """The directory to store things into which can be deleted at any time"""

    if os.name == "nt" and build.BUILD_TYPE == u"windows-portable":
        # avoid writing things to the host system for the portable build
        path = os.path.join(get_user_dir(), "cache")
    else:
        path = os.path.join(xdg_get_cache_home(), "quodlibet")

    mkdir(path, 0o700)
    return path


@cached_func
def get_user_dir():
    """Place where QL saves its state, database, config etc."""

    if os.name == "nt":
        USERDIR = os.path.join(windows.get_appdata_dir(), "Quod Libet")
    elif is_osx():
        USERDIR = os.path.join(os.path.expanduser("~"), ".quodlibet")
    else:
        USERDIR = os.path.join(xdg_get_config_home(), "quodlibet")

        if not os.path.exists(USERDIR):
            tmp = os.path.join(os.path.expanduser("~"), ".quodlibet")
            if os.path.exists(tmp):
                USERDIR = tmp

    if 'QUODLIBET_USERDIR' in os.environ:
        USERDIR = os.environ['QUODLIBET_USERDIR']

    if build.BUILD_TYPE == u"windows-portable":
        USERDIR = os.path.normpath(os.path.join(
            os.path.dirname(path2fsn(sys.executable)), "..", "..", "config"))

    # XXX: users shouldn't assume the dir is there, but we currently do in
    # some places
    mkdir(USERDIR, 0o750)

    return USERDIR


def is_release():
    """Returns whether the running version is a stable release or under
    development.
    """

    return const.VERSION_TUPLE[-1] != -1


def get_build_version():
    """Returns a build version tuple"""

    version = list(const.VERSION_TUPLE)
    if is_release() and build.BUILD_VERSION > 0:
        version.append(build.BUILD_VERSION)

    return tuple(version)


def get_build_description():
    """Returns text describing the version of the build.

    Includes additional build info like git hash and build version.
    """

    version = list(get_build_version())
    notes = []
    if not is_release():
        version = version[:-1]
        notes.append(u"development")

        if build.BUILD_INFO:
            notes.append(build.BUILD_INFO)

    version_string = u".".join(map(str, version))
    note = u" (%s)" % u", ".join(notes) if notes else u""

    return version_string + note


def init_plugins(no_plugins=False):
    print_d("Starting plugin manager")

    from quodlibet import plugins
    folders = [os.path.join(get_base_dir(), "ext", kind)
               for kind in PLUGIN_DIRS]
    folders.append(os.path.join(get_user_dir(), "plugins"))
    print_d("Scanning folders: %s" % folders)
    pm = plugins.init(folders, no_plugins)
    pm.rescan()

    from quodlibet.qltk.edittags import EditTags
    from quodlibet.qltk.renamefiles import RenameFiles
    from quodlibet.qltk.tagsfrompath import TagsFromPath
    EditTags.init_plugins()
    RenameFiles.init_plugins()
    TagsFromPath.init_plugins()

    return pm


def set_application_info(app):
    """Call after init() and before creating any windows to apply default
    values for names and icons.
    """

    from quodlibet._init import is_init

    assert is_init()

    from gi.repository import Gtk, GLib

    assert app.process_name
    set_process_title(app.process_name)
    # Issue 736 - set after main loop has started (gtk seems to reset it)
    GLib.idle_add(set_process_title, app.process_name)

    assert app.id
    # https://honk.sigxcpu.org/con/GTK__and_the_application_id.html
    GLib.set_prgname(app.id)
    assert app.name
    GLib.set_application_name(app.name)

    assert app.icon_name
    theme = Gtk.IconTheme.get_default()
    assert theme.has_icon(app.icon_name)
    Gtk.Window.set_default_icon_name(app.icon_name)


def _main_setup_osx(window):
    from AppKit import NSObject, NSApplication
    import objc

    try:
        import gi
        gi.require_version('GtkosxApplication', '1.0')
        from gi.repository import GtkosxApplication
    except (ValueError, ImportError):
        print_d("importing GtkosxApplication failed, no native menus")
    else:
        osx_app = GtkosxApplication.Application()
        window.set_as_osx_window(osx_app)
        osx_app.ready()

    shared_app = NSApplication.sharedApplication()
    gtk_delegate = shared_app.delegate()

    # Instead of quitting when the main window gets closed just hide it.
    # If the dock icon gets clicked we get
    # applicationShouldHandleReopen_hasVisibleWindows_ and show everything.
    class Delegate(NSObject):

        @objc.signature(b'B@:#B')
        def applicationShouldHandleReopen_hasVisibleWindows_(
                self, ns_app, flag):
            print_d("osx: handle reopen")
            app.present()
            return True

        def applicationShouldTerminate_(self, sender):
            print_d("osx: block termination")
            # FIXME: figure out why idle_add is needed here
            from gi.repository import GLib
            GLib.idle_add(app.quit)
            return False

        def applicationDockMenu_(self, sender):
            return gtk_delegate.applicationDockMenu_(sender)

        def application_openFile_(self, sender, filename):
            return app.window.open_file(filename.encode("utf-8"))

    delegate = Delegate.alloc().init()
    delegate.retain()
    shared_app.setDelegate_(delegate)

    # QL shouldn't exit on window close, EF should
    if window.get_is_persistent():
        window.connect(
            "delete-event", lambda window, event: window.hide() or True)


def run(window, before_quit=None):
    print_d("Entering quodlibet.main")
    from gi.repository import Gtk, Gdk, GLib
    from quodlibet._init import is_init

    assert is_init()

    def quit_gtk(window):

        if before_quit is not None:
            before_quit()

        # disable plugins
        import quodlibet.plugins
        quodlibet.plugins.quit()

        # for debug: this will list active copools
        from quodlibet.util import copool
        copool.pause_all()

        # See which browser windows are open and save their names
        # so we can restore them on start
        from quodlibet.qltk.browser import LibraryBrowser
        LibraryBrowser.save()

        # destroy all open windows so they hide immediately on close:
        # destroying all top level windows doesn't work (weird errors),
        # so we hide them all and only destroy our tracked instances
        # (browser windows, tag editors, pref window etc.)
        from quodlibet.qltk import Window
        for toplevel in Gtk.Window.list_toplevels():
            toplevel.hide()

        for window in Window.windows:
            window.destroy()

        Gtk.main_quit()

        print_d("Quit GTK: done.")

    window.connect('destroy', quit_gtk)

    if sys.platform == "darwin":
        _main_setup_osx(window)

    if not window.show_maybe():
        # if we don't show a window, startup isn't completed, so call manually
        Gdk.notify_startup_complete()

    from quodlibet.errorreport import faulthandling

    # gtk+ on osx is just too crashy
    if not is_osx():
        try:
            faulthandling.enable(os.path.join(get_user_dir(), "faultdump"))
        except IOError:
            util.print_exc()
        else:
            GLib.idle_add(faulthandling.raise_and_clear_error)

    # set QUODLIBET_START_PERF to measure startup time until the
    # windows is first shown.
    if "QUODLIBET_START_PERF" in os.environ:
        window.connect("draw", Gtk.main_quit)
        Gtk.main()
        sys.exit()
    else:
        Gtk.main()

    print_d("Gtk.main() done.")


def enable_periodic_save(save_library):
    import quodlibet.library
    from quodlibet.util import copool
    from quodlibet import config

    timeout = 5 * 60 * 1000  # 5 minutes

    def periodic_config_save():
        while 1:
            config.save()
            yield

    copool.add(periodic_config_save, timeout=timeout)

    if not save_library:
        return

    def periodic_library_save():
        while 1:
            # max every 15 minutes
            quodlibet.library.save(save_period=15 * 60)
            yield

    copool.add(periodic_library_save, timeout=timeout)


def is_first_session(app_name):
    """Returns True if the current session is the first one to e.g.
    show a wizard/setup dialog etc.

    Will return True after each upgrade as well.

    app_name: e.g. 'quodlibet'
    """

    from quodlibet import config
    from quodlibet import const

    value = config.get("memory", "%s_last_active_version" % app_name, "")

    if value != const.VERSION:
        return True

    return False


def finish_first_session(app_name):
    """Call on shutdown so that is_first_session() works"""

    from quodlibet import config
    from quodlibet import const

    config.set("memory", "%s_last_active_version" % app_name, const.VERSION)
