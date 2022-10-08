# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#           2012,2013 Christoph Reiter
#           2010-2021 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import sys
import os

from quodlibet import _
from quodlibet import config
from quodlibet.cli import process_arguments, exit_
from quodlibet.util.dprint import print_d, print_, print_exc


def main(argv=None):
    if argv is None:
        argv = sys.argv

    import quodlibet

    config.migrate_legacy_data(move=False)  # TODO: When tested, `move=True`?
    config_file = os.path.join(quodlibet.get_config_dir(), "config")
    quodlibet.init_cli(config_file=config_file)

    try:
        # we want basic commands not to import gtk (doubles process time)
        assert "gi.repository.Gtk" not in sys.modules
        sys.modules["gi.repository.Gtk"] = None
        startup_actions, cmds_todo = process_arguments(argv)
    finally:
        sys.modules.pop("gi.repository.Gtk", None)

    quodlibet.init()

    from quodlibet import app
    from quodlibet.qltk import add_signal_watch
    add_signal_watch(app.quit)

    import quodlibet.player
    import quodlibet.library
    from quodlibet import browsers
    from quodlibet import util

    app.name = "Quod Libet"
    app.description = _("Music player and music library manager")
    app.id = "io.github.quodlibet.QuodLibet"
    app.process_name = "quodlibet"
    quodlibet.set_application_info(app)

    library_path = os.path.join(quodlibet.get_data_dir(), "songs")

    print_d("Initializing main library (%s)" % (
            quodlibet.util.path.unexpand(library_path)))

    library = quodlibet.library.init(library_path)
    app.library = library

    # this assumes that nullbe will always succeed
    from quodlibet.player import PlayerError
    wanted_backend = os.environ.get(
        "QUODLIBET_BACKEND", config.get("player", "backend"))

    try:
        player = quodlibet.player.init_player(wanted_backend, app.librarian)
    except PlayerError:
        print_exc()
        player = quodlibet.player.init_player("nullbe", app.librarian)

    app.player = player

    os.environ["PULSE_PROP_media.role"] = "music"
    os.environ["PULSE_PROP_application.icon_name"] = app.icon_name

    browsers.init()

    from quodlibet.qltk.songlist import SongList, get_columns

    headers = get_columns()
    SongList.set_all_column_headers(headers)

    for opt in config.options("header_maps"):
        val = config.get("header_maps", opt)
        util.tags.add(opt, val)

    in_all = ("~filename ~uri ~#lastplayed ~#rating ~#playcount ~#skipcount "
              "~#added ~#bitrate ~current ~#laststarted ~basename "
              "~dirname").split()
    for Kind in browsers.browsers:
        if Kind.headers is not None:
            Kind.headers.extend(in_all)
        Kind.init(library)

    pm = quodlibet.init_plugins("no-plugins" in startup_actions)

    if hasattr(player, "init_plugins"):
        player.init_plugins()

    from quodlibet.qltk import unity
    unity.init("io.github.quodlibet.QuodLibet.desktop", player)

    from quodlibet.qltk.songsmenu import SongsMenu
    SongsMenu.init_plugins()

    from quodlibet.util.cover import CoverManager
    app.cover_manager = CoverManager()
    app.cover_manager.init_plugins()

    from quodlibet.plugins.playlist import PLAYLIST_HANDLER
    PLAYLIST_HANDLER.init_plugins()

    from quodlibet.plugins.query import QUERY_HANDLER
    QUERY_HANDLER.init_plugins()

    from gi.repository import GLib

    from quodlibet.commands import registry as cmd_registry, CommandError

    def exec_commands(*args):
        for cmd in cmds_todo:
            try:
                resp = cmd_registry.run(app, *cmd)
            except CommandError:
                pass
            else:
                if resp is not None:
                    print_(resp, end="", flush=True)

    from quodlibet.qltk.quodlibetwindow import QuodLibetWindow, PlayerOptions
    # Call exec_commands after the window is restored, but make sure
    # it's after the mainloop has started so everything is set up.

    app.window = window = QuodLibetWindow(
        library, player,
        restore_cb=lambda:
            GLib.idle_add(exec_commands, priority=GLib.PRIORITY_HIGH))

    app.player_options = PlayerOptions(window)

    from quodlibet.qltk.window import Window

    from quodlibet.plugins.events import EventPluginHandler
    from quodlibet.plugins.gui import UserInterfacePluginHandler
    pm.register_handler(EventPluginHandler(library.librarian, player,
                                           app.window.songlist))
    pm.register_handler(UserInterfacePluginHandler())

    from quodlibet.mmkeys import MMKeysHandler
    from quodlibet.remote import Remote, RemoteError
    from quodlibet.qltk.tracker import SongTracker, FSInterface
    try:
        from quodlibet.qltk.dbus_ import DBusHandler
    except ImportError:
        DBusHandler = None

    mmkeys_handler = MMKeysHandler(app)
    mmkeys_handler.start()

    current_path = os.path.join(quodlibet.get_runtime_dir(), "current")
    fsiface = FSInterface(current_path, player, library)
    remote = Remote(app, cmd_registry)
    try:
        remote.start()
    except RemoteError:
        exit_(1, True)

    if DBusHandler is not None:
        DBusHandler(player, library)
    tracker = SongTracker(library.librarian, player, window.playlist)

    from quodlibet import session
    session_client = session.init(app)

    quodlibet.enable_periodic_save(save_library=True)

    if ("start-playing" in startup_actions or
            (config.getboolean("player", "restore_playing", False) and
                config.getboolean("player", "is_playing", False))):
        player.paused = False

    if "start-hidden" in startup_actions:
        Window.prevent_inital_show(True)

    # restore browser windows
    from quodlibet.qltk.browser import LibraryBrowser
    GLib.idle_add(LibraryBrowser.restore, library, player,
                  priority=GLib.PRIORITY_HIGH)

    def before_quit():
        print_d("Saving active browser state")
        try:
            app.browser.save()
        except NotImplementedError:
            pass

        print_d("Shutting down player device %r." % player.version_info)
        player.destroy()

    quodlibet.run(window, before_quit=before_quit)

    app.player_options.destroy()
    quodlibet.finish_first_session("quodlibet")
    mmkeys_handler.quit()
    remote.stop()
    fsiface.destroy()

    tracker.destroy()
    quodlibet.library.save()
    quodlibet.library.destroy()
    config.save()

    session_client.close()

    print_d("Finished shutdown.")

    if app.is_restarting:
        os.execv(sys.executable, [sys.executable] + sys.argv)
