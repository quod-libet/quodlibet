# Copyright 2012-2017 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from gi.repository import Gtk

import os

from quodlibet.util.songwrapper import SongWrapper

from quodlibet.qltk.songsmenu import confirm_multi_song_invoke

import quodlibet
from quodlibet import _
from quodlibet import qltk
from quodlibet import util
from quodlibet.pattern import Pattern
from quodlibet.plugins import PluginConfigMixin
from quodlibet.plugins.playlist import PlaylistPlugin
from quodlibet.plugins.songsmenu import SongsMenuPlugin
from quodlibet.qltk.data_editors import JSONBasedEditor
from quodlibet.qltk.x import SeparatorMenuItem
from quodlibet.qltk import ErrorMessage, Icons
from quodlibet.qltk.getstring import GetStringDialog
from quodlibet.util.dprint import print_w, print_d, print_e
from quodlibet.util.json_data import JSONObject, JSONObjectDict
from quodlibet.util import connect_obj, print_exc

Field = JSONObject.Field


class Command(JSONObject):
    """
    Wraps an arbitrary shell command and its argument pattern.
    Serialises as JSON for some editability
    """

    NAME = _("Command")

    FIELDS = {
        "name": Field(_("name"), _("The name of this command")),

        "command": Field(_("command"), _("The shell command syntax to run")),

        "parameter": Field(_("parameter"),
                           _("If specified, a parameter whose occurrences in "
                             "the command will be substituted with a "
                             "user-supplied value, e.g. by using 'PARAM' "
                             "all instances of '{PARAM}' in your command will "
                             "have the value prompted for when run")),

        "pattern": Field(_("pattern"),
                         _("The QL pattern, e.g. <~filename>, to use to "
                           "compute a value for the command. For playlists, "
                           "this also supports virtual tags <~playlistname> "
                           "and <~#playlistindex>.")),

        "unique": Field(_("unique"),
                        _("If set, this will remove duplicate computed values "
                          "of the pattern")),

        "max_args": Field(_("max args"),
                          _("The maximum number of argument to pass to the "
                            "command at one time (like xargs)")),

        "reverse": Field(_("reverse"),
                          _("If set, the argument list will be reversed")),
    }

    def __init__(self, name=None, command=None, pattern="<~filename>",
                 unique=False, parameter=None, max_args=10000, reverse=False,
                 warn_threshold=50):
        JSONObject.__init__(self, name)
        self.command = str(command or "")
        self.pattern = str(pattern)
        self.unique = bool(unique)
        self.max_args = max_args
        self.reverse = bool(reverse)
        self.parameter = str(parameter or "")
        self.__pat = Pattern(self.pattern)
        self.warn_threshold = warn_threshold

    def run(self, songs, playlist_name=None):
        """
        Runs this command on `songs`,
        splitting into multiple calls if necessary.
        `playlist_name` if populated contains the Playlist's name.
        """
        args = []
        template_vars = {}
        if self.parameter:
            value = GetStringDialog(None, _("Input value"),
                                    _("Value for %s?") % self.parameter).run()
            template_vars[self.parameter] = value
        if playlist_name:
            print_d("Playlist command for %s" % playlist_name)
            template_vars["PLAYLIST"] = playlist_name

        actual_command = self.command.format(**template_vars)
        print_d("Actual command=%s" % actual_command)
        for i, song in enumerate(songs):
            wrapped = SongWrapper(song)
            if playlist_name:
                wrapped["~playlistname"] = playlist_name
                wrapped["~playlistindex"] = str(i + 1)
                wrapped["~#playlistindex"] = i + 1
            arg = str(self.__pat.format(wrapped))
            if not arg:
                print_w("Couldn't build shell command using \"%s\"."
                        "Check your pattern?" % self.pattern)
                break
            if not self.unique:
                args.append(arg)
            elif arg not in args:
                args.append(arg)
        max = int((self.max_args or 10000))
        com_words = actual_command.split(" ")

        if self.reverse:
            args.reverse()

        while args:
            print_d("Running %s with %d substituted arg(s) (of %d%s total)..."
                    % (actual_command, min(max, len(args)), len(args),
                       " unique" if self.unique else ""))
            util.spawn(com_words + args[:max])
            args = args[max:]

    @property
    def playlists_only(self):
        return ("~playlistname" in self.pattern
                or "playlistindex" in self.pattern)

    def __str__(self):
        return "{command} {pattern}".format(**dict(self.data))


class CustomCommands(PlaylistPlugin, SongsMenuPlugin, PluginConfigMixin):

    PLUGIN_ICON = Icons.APPLICATION_UTILITIES
    PLUGIN_ID = "CustomCommands"
    PLUGIN_NAME = _("Custom Commands")
    PLUGIN_DESC = _("Runs custom commands (in batches if required) on songs "
                    "using any of their tags.")

    # Here are some starters...
    DEFAULT_COMS = [
        Command("Compress files", "file-roller -d"),

        Command("Browse folders (Thunar)", "thunar", "<~dirname>", unique=True,
                max_args=50, warn_threshold=20),

        Command("Burn using K3b", "k3b --audiocd"),

        Command("Burn using Brasero", "brasero --audio"),

        Command("Burn using Xfburn", "xfburn --audio-composition", reverse=True),

        Command("Flash notification",
                command="notify-send"
                    " -t 2000"
                    " -i "
                        "/usr/share/icons/hicolor/scalable/apps/"
                        "io.github.quodlibet.QuodLibet.svg",
                pattern="<~rating> \"<title><version| (<version>)>\""
                        "<~people| by <~people>>"
                    "<album|, from <album><discnumber| : disk <discnumber>>"
                    "<~length| (<~length>)>",
                max_args=1,
                warn_threshold=10),

        Command("Output playlist to stdout",
                command="echo -e",
                pattern="<~playlistname>: <~playlistindex>. "
                        " <~artist~title>\\\\n",
                warn_threshold=20),

        Command("Fix MP3 VBR with mp3val", "mp3val -f", unique=True,
                max_args=1),

        Command("Record Stream",
                command="x-terminal-emulator -e wget -P $HOME",
                pattern="<~filename>",
                max_args=1)
    ]

    COMS_FILE = os.path.join(
        quodlibet.get_data_dir(), 'lists', 'customcommands.json')

    _commands = None
    """Commands known to the class"""

    def __set_pat(self, name):
        self.com_index = name

    def get_data(self, key):
        """Gets the pattern for a given key"""
        try:
            return self.all_commands()[key]
        except (KeyError, TypeError):
            print_d("Invalid key %s" % key)
            return None

    @classmethod
    def edit_patterns(cls, button):
        win = JSONBasedEditor(Command, cls.all_commands(),
                              filename=cls.COMS_FILE,
                              title=_("Edit Custom Commands"))
        # Cache busting
        cls._commands = None
        win.show()

    @classmethod
    def PluginPreferences(cls, parent):
        hb = Gtk.HBox(spacing=3)
        hb.set_border_width(0)

        button = qltk.Button(_("Edit Custom Commands") + "…", Icons.EDIT)
        button.connect("clicked", cls.edit_patterns)
        hb.pack_start(button, True, True, 0)
        hb.show_all()
        return hb

    @classmethod
    def all_commands(cls):
        if cls._commands is None:
            cls._commands = cls._get_saved_commands()
        return cls._commands

    @classmethod
    def _get_saved_commands(cls):
        filename = cls.COMS_FILE
        print_d("Loading saved commands from '%s'..." % filename)
        coms = None
        try:
            with open(filename, "r", encoding="utf-8") as f:
                coms = JSONObjectDict.from_json(Command, f.read())
        except (IOError, ValueError) as e:
            print_w("Couldn't parse saved commands (%s)" % e)

        # Failing all else...
        if not coms:
            print_d("No commands found in %s. Using defaults." % filename)
            coms = {c.name: c for c in cls.DEFAULT_COMS}
        print_d("Loaded commands: %s" % coms.keys())
        return coms

    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)
        pl_mode = hasattr(self, '_playlists') and bool(len(self._playlists))
        self.com_index = None
        self.unique_only = False
        submenu = Gtk.Menu()
        for name, c in self.all_commands().items():
            item = Gtk.MenuItem(label=name)
            connect_obj(item, 'activate', self.__set_pat, name)
            if pl_mode and not c.playlists_only:
                continue
            item.set_sensitive(c.playlists_only == pl_mode)
            submenu.append(item)

        self.add_edit_item(submenu)
        if submenu.get_children():
            self.set_submenu(submenu)
        else:
            self.set_sensitive(False)

    @classmethod
    def add_edit_item(cls, submenu):
        config = Gtk.MenuItem(label=_("Edit Custom Commands") + "…")
        connect_obj(config, 'activate', cls.edit_patterns, config)
        config.set_sensitive(not JSONBasedEditor.is_not_unique())
        submenu.append(SeparatorMenuItem())
        submenu.append(config)

    def plugin_songs(self, songs):
        self._handle_songs(songs)

    def plugin_playlist(self, playlist):
        print_d("Running playlist plugin for %s" % playlist)
        return self._handle_songs(playlist.songs, playlist)

    def _handle_songs(self, songs, playlist=None):
        # Check this is a launch, not a configure
        if self.com_index:
            com = self.get_data(self.com_index)
            if len(songs) > com.warn_threshold:
                if not confirm_multi_song_invoke(
                        self, com.name, len(songs)):
                    print_d("User decided not to run on %d songs" % len(songs))
                    return
            print_d("Running %s on %d song(s)" % (com, len(songs)))
            try:
                com.run(songs, playlist and playlist.name)
            except Exception as err:
                print_e("Couldn't run command %s: %s %s at:"
                        % (com.name, type(err), err, ))
                print_exc()
                ErrorMessage(
                    self.plugin_window,
                    _("Unable to run custom command %s") %
                    util.escape(self.com_index),
                    util.escape(str(err))).run()
