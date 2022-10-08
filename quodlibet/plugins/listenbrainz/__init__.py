# -*- coding: utf-8 -*-
# Copyright 2018 Ian Campbell <ijc@hellion.org.uk>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Derived from the QLScrobbler plugin:
#
# QLScrobbler: an Audioscrobbler client plugin for Quod Libet.
# version 0.11
# (C) 2005-2016 by Joshua Kwan <joshk@triplehelix.org>,
#                  Joe Wreschnig <piman@sacredchao.net>,
#                  Franz Pletyz <fpletz@franz-pletz.org>,
#                  Nicholas J. Michalek <djphazer@gmail.com>,
#                  Steven Robertson <steven@strobe.cc>
#                  Nick Boultbee <nick.boultbee@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from typing import List, Tuple
import os
import threading
import time

from gi.repository import Gtk, GLib

import quodlibet
from quodlibet import _
from quodlibet import app, qltk
from quodlibet.pattern import Pattern
from quodlibet.query import Query
from quodlibet.plugins.events import EventPlugin
from quodlibet.plugins import PluginConfig
from quodlibet.qltk.entry import ValidatingEntry, UndoEntry
from quodlibet.qltk.msg import Message
from quodlibet.qltk import Icons
from quodlibet.util.dprint import print_d
from quodlibet.util.picklehelper import pickle_load, pickle_dump, PickleError

import csv
from io import StringIO

from . import listenbrainz

DEFAULT_TITLEPAT = '<title><version| (<version>)>'
DEFAULT_ARTISTPAT = '<artist|<artist>|<composer|<composer>|<performer>>>'

plugin_config = PluginConfig("listenbrainz")
defaults = plugin_config.defaults
#defaults.set("endpoint", "https://api.listenbrainz.org")
defaults.set("user_token", "")

defaults.set("titlepat", "")
defaults.set("artistpat", "")
defaults.set("exclude", "")
defaults.set("offline", False)
defaults.set("tags", "")


def config_get_title_pattern():
    return plugin_config.get('titlepat') or DEFAULT_TITLEPAT


def config_get_artist_pattern():
    return plugin_config.get('artistpat') or DEFAULT_ARTISTPAT


def config_get_tags():
    tags = plugin_config.get('tags') or None
    if tags is None:
        return []
    parser = csv.reader(StringIO(tags), quoting=csv.QUOTE_ALL, skipinitialspace=True)
    try:
        return next(parser)
    except Exception as e:
        print_d("Failed to parse tags \"%s\": %s" % tags, e)
        return []


class ListenBrainzSubmitQueue():
    """Manages the submit queue for listens. Works independently of the
    plugin being enabled; other plugins may use submit() to queue songs for
    submission.
    """
    DUMP = os.path.join(quodlibet.get_cache_dir(), "listenbrainz_cache")

    # These objects are shared across instances, to allow other plugins to
    # queue listens in future versions of QL.
    queue: List[Tuple[int, listenbrainz.Track]] = []
    condition = threading.Condition()

    def set_nowplaying(self, song):
        """Send a Now Playing notification."""
        track = self._track(song)
        if not track or self.nowplaying_track == track:
            return
        print_d("Set now playing: %s" % track)
        self.condition.acquire()
        self.nowplaying_track = track
        self.nowplaying_sent = False
        self.changed()
        self.condition.release()

    def submit(self, song, timestamp=0):
        """Submit a song. If 'timestamp' is 0, the current time will
        be used."""
        track = self._track(song)
        if track is None:
            return

        self.condition.acquire()
        if timestamp == 0:
            timestamp = int(time.time())
        print_d("Queueing: %s" % track)
        self.queue.append((timestamp, track))
        self.changed()
        self.condition.release()

    def _track(self, song):
        """Returns a listenbrainz.Track."""
        artist = self.artpat.format(song)
        title = self.titlepat.format(song)
        if artist is None or title is None:
            return None

        album = song.comma("album")

        # https://listenbrainz.readthedocs.io/en/latest/dev/json.html#payload-json-details
        #
        # artist_mbids          A list of MusicBrainz Artist IDs, one or more Artist
        #                       IDs may be included here. If you have a complete
        #                       MusicBrainz artist credit that contains multiple
        #                       Artist IDs, include them all in this list.
        # release_group_mbid    A MusicBrainz Release Group ID of the release group this
        #                       recording was played from.
        # release_mbid          A MusicBrainz Release ID of the release this recording
        #                       was played from.
        # recording_mbid        A MusicBrainz Recording ID of the recording that was
        #                       played.
        # track_mbid            A MusicBrainz Track ID associated with the recording
        #                       that was played.
        # work_mbids            A list of MusicBrainz Work IDs that may be associated
        #                       with this recording.
        # tracknumber           The tracknumber of the recording. This first recording
        #                       on a release is tracknumber 1.
        # isrc                  The ISRC code associated with the recording.
        # spotify_id            The Spotify track URL associated with this recording.
        # tags                  A list of user defined tags to be associated with this
        #                       recording. These tags are similar to last.fm tags. For
        #                       example, you have apply tags such as punk, see-live,
        #                       smelly. You may submit up to MAX_TAGS_PER_LISTEN tags
        #                       and each tag may be up to MAX_TAG_SIZE characters large.
        #
        # https://picard.musicbrainz.org/docs/mappings/
        # Above			Tag
        # artists_mbids		MUSICBRAINZ_ARTISTID (multiple)
        # release_group_mbid	MUSICBRAINZ_RELEASEGROUPID
        # release_mbid		MUSICBRAINZ_ALBUMID
        # recording_mbid	MUSICBRAINZ_TRACKID
        # track_mbid		MUSICBRAINZ_RELEASETRACKID
        # work_mbids		MUSICBRAINZ_WORKID (multiple)
        # tracknumber		TRACKNUMBER
        # isrc			ISRC
        # spotify_id		N/A
        # tags			N/A
        additional_info = {}

        for (k, v) in [
            ('artist_mbids', song.list("musicbrainz_artistid")),
            ('release_group_mbid', song.get("musicbrainz_releasegroupid", None)),
            ('release_mbid', song.get("musicbrainz_albumid", None)),
            ('recording_mbid', song.get("musicbrainz_trackid", None)),
            ('track_mbid', song.get("musicbrainz_releasetrackid", None)),
            ('work_mbids', song.list("musicbrainz_workid")),
            ('tracknumber', song.get("tracknumber", None)),
            ('isrc', song.get("isrc", None)),
            ('tags', self.tags)]:
            if v is not None and v != []:
                additional_info[k] = v

        print_d("Track(%s,%s,%s,%s)" % (artist, title, album, additional_info))
        return listenbrainz.Track(artist, title, album, additional_info)

    def __init__(self):
        print("logging")
        self.nowplaying_track = None
        self.nowplaying_sent = False

        self.broken = False
        self.offline = False
        self.retries = 0

        self.lb = listenbrainz.ListenBrainzClient() # XXX logger=xxx

        # These need to be set early for _format_song to work
        self.titlepat = Pattern(config_get_title_pattern())
        self.artpat = Pattern(config_get_artist_pattern())
        self.tags = config_get_tags()

        try:
            with open(self.DUMP, 'rb') as disk_queue_file:
                disk_queue = pickle_load(disk_queue_file)
            os.unlink(self.DUMP)
            self.queue += disk_queue
        except (EnvironmentError, PickleError):
            pass

    @classmethod
    def dump_queue(klass):
        if klass.queue:
            try:
                with open(klass.DUMP, 'wb') as disk_queue_file:
                    pickle_dump(klass.queue, disk_queue_file)
            except (EnvironmentError, PickleError):
                pass

    # Must be called with self.condition acquired
    def _check_config(self):
        #endpoint = plugin_config.get('endpoint')
        user_token = plugin_config.get('user_token')
        #if not endpoint or not user_token:
        if not user_token:
            if self.queue and not self.broken:
                self.quick_dialog(_("Please visit the Plugins window to set "
                              "ListenBrainz up. Until then, listens will not be "
                              "submitted."), Gtk.MessageType.INFO)
                self.broken = True
        #elif (self.lb.host_name, self.lb.user_token) != (endpoint, user_token):
        elif self.lb.user_token != user_token:
            #print_d("Setting %s, %s" % (endpoint, user_token))
            #self.lb.host_name, self.lb.user_token = (endpoint, user_token)
            print_d("Setting user_token %s" % user_token)
            self.lb.user_token = user_token
            self.broken = False
        self.offline = plugin_config.getboolean('offline')
        self.titlepat = Pattern(config_get_title_pattern())
        self.artpat = Pattern(config_get_artist_pattern())
        self.tags = config_get_tags()

    # Must be called with self.condition acquired
    def changed(self):
        """Signal that settings or queue contents were changed."""
        self._check_config()
        if not self.broken and not self.offline and (self.queue or
                (self.nowplaying_track and not self.nowplaying_sent)):
            self.condition.notify()

    def run(self):
        """Submit songs from the queue. Call from a daemon thread."""

        print_d("Submission queue thread running")

        while True:
            print_d("Top of queue loop")
            self.condition.acquire()

            while self.broken or \
                  self.offline or \
                  (not self.queue and
                   (not self.nowplaying_track or self.nowplaying_sent)):
                print_d("Nothing to do, waiting")
                self.condition.wait()
                print_d("Awoke")

            print_d("Running iteration")

            # Poll inputs under the lock

            submit = None
            if self.queue:
                submit = self.queue[0]
            nowplaying = None
            if self.nowplaying_track and not self.nowplaying_sent:
                nowplaying = self.nowplaying_track

            self.condition.release()

            # Call f() and handle errors with backoff and disable
            def with_backoff(f):
                try:
                    rsp = f()
                except Exception as e:
                    rsp = None
                    print_d("Error: %s" % e)

                if rsp and rsp.status == 200:
                    self.retries = 0
                    return True
                elif self.retries >= 6:
                    # Too many retries, put self offline
                    print_d("Too many retries, setting to offline")
                    self.offline = True
                    plugin_config.set("offline", True)

                    self.quick_dialog(_(
                        "Too many consecutive submission failures (%d). "
                        "Setting to offline mode. "
                        "Please visit the Plugins window to reset "
                        "ListenBrainz. Until then, listens will not be "
                        "submitted." % self.retries), Gtk.MessageType.INFO)
                    return False
                else:
                    delay = 10
                    print_d("Failure, waiting %ds" % delay)
                    self.retries += 1
                    time.sleep(delay)
                    print_d("Done sleeping")
                    return False
                return True

            if submit:
                (listened_at, track) = submit
                print_d("Submitting: %s" % track)

                if not with_backoff(lambda: self.lb.listen(listened_at, track)):
                    continue

                print_d("Submission successful")

                # Remove submitted entry under lock
                self.condition.acquire()
                if self.queue[0] == submit:
                    self.queue.pop(0)
                self.condition.release()

            if nowplaying:
                print_d("Now playing: %s" % nowplaying)

                if not with_backoff(lambda: self.lb.playing_now(nowplaying)):
                    continue

                print_d("Now playing submission successful")

                self.condition.acquire()
                if nowplaying == self.nowplaying_track:
                    # only if it didn't change under our feet
                    self.nowplaying_sent = True
                self.condition.release()

    def quick_dialog_helper(self, dialog_type, msg):
        dialog = Message(dialog_type, app.window, "ListenBrainz", msg)
        dialog.connect('response', lambda dia, resp: dia.destroy())
        dialog.show()

    def quick_dialog(self, msg, dialog_type):
        GLib.idle_add(self.quick_dialog_helper, dialog_type, msg)


class ListenbrainzSubmission(EventPlugin):
    PLUGIN_ID = "listenbrainz"
    PLUGIN_NAME = _("ListenBrainz Submission")
    PLUGIN_DESC = _("Submit listens to ListenBrainz.")
    PLUGIN_ICON = Icons.NETWORK_WORKGROUP

    def __init__(self):
        self.__enabled = False
        self.queue = ListenBrainzSubmitQueue()
        queue_thread = threading.Thread(None, self.queue.run)
        queue_thread.setDaemon(True)
        queue_thread.start()

        self.start_time = 0
        self.unpaused_time = 0
        self.elapsed = 0
        self.nowplaying = None

        self.exclude = plugin_config.get('exclude')

    def plugin_on_song_ended(self, song, stopped):
        if song is None or not self.__enabled:
            return
        if self.unpaused_time > 0:
            self.elapsed += time.time() - self.unpaused_time
        # https://listenbrainz.readthedocs.io/en/latest/dev/api.html
        #
        # Listens should be submitted for tracks when the user has
        # listened to half the track or 4 minutes of the track,
        # whichever is lower. If the user hasn’t listened to 4 minutes
        # or half the track, it doesn’t fully count as a listen and
        # should not be submitted.
        #
        # we check 'elapsed' rather than 'length' to work around wrong ~#length
        if self.elapsed < (4 * 60) and self.elapsed <= .5 * song.get("~#length", 0):
            return
        print_d("Checking against filter %s" % self.exclude)
        if self.exclude and Query(self.exclude).search(song):
            print_d("Not submitting: %s" % song("~artist~title"))
            return
        self.queue.submit(song, self.start_time)

    def song_excluded(self, song):
        if self.exclude and Query(self.exclude).search(song):
            print_d("%s is excluded by %s" %
                    (song("~artist~title"), self.exclude))
            return True
        return False

    def send_nowplaying(self, song):
        if not self.song_excluded(song):
            self.queue.set_nowplaying(song)

    def plugin_on_song_started(self, song):
        if song is None:
            return
        self.start_time = int(time.time())
        if app.player.paused:
            self.unpaused_time = 0
        else:
            self.unpaused_time = time.time()
        self.elapsed = 0
        if self.__enabled and not app.player.paused:
            self.send_nowplaying(song)
        else:
            self.nowplaying = song

    def plugin_on_paused(self):
        if self.unpaused_time > 0:
            self.elapsed += time.time() - self.unpaused_time
        self.unpaused_time = 0

    def plugin_on_unpaused(self):
        self.unpaused_time = time.time()
        if self.__enabled and self.nowplaying:
            self.send_nowplaying(self.nowplaying)
            self.nowplaying = None

    def enabled(self):
        self.__enabled = True
        print_d("Plugin enabled - accepting new songs.")

    def disabled(self):
        self.__enabled = False
        print_d("Plugin disabled - not accepting any new songs.")
        #ListenBrainzSubmitQueue.dump_queue()

    def PluginPreferences(self, parent):
        def changed(entry, key):
            if entry.get_property('sensitive'):
                plugin_config.set(key, entry.get_text())

        box = Gtk.VBox(spacing=12)

        # first frame
        table = Gtk.Table(n_rows=2, n_columns=2)
        table.props.expand = False
        table.set_col_spacings(6)
        table.set_row_spacings(6)

        labels = []
        label_names = [_("User _token:")]
        for idx, name in enumerate(label_names):
            label = Gtk.Label(label=name)
            label.set_alignment(0.0, 0.5)
            label.set_use_underline(True)
            table.attach(label, 0, 1, idx, idx + 1,
                         xoptions=Gtk.AttachOptions.FILL |
                         Gtk.AttachOptions.SHRINK)
            labels.append(label)

        row = 0

        # endpoint url / hostname
        #entry = UndoEntry()
        #entry.set_text(plugin_config.get('endpoint'))
        #entry.connect('changed', changed, 'endpoint')
        #table.attach(entry, 1, 2, row, row + 1)
        #labels[row].set_mnemonic_widget(entry)
        #row += 1

        # token
        entry = UndoEntry()
        entry.set_text(plugin_config.get('user_token'))
        entry.connect('changed', changed, 'user_token')
        table.attach(entry, 1, 2, row, row + 1)
        labels[row].set_mnemonic_widget(entry)
        row += 1

        # verify data
        #button = qltk.Button(_("_Verify account data"),
        #                     Icons.DIALOG_INFORMATION)
        #button.connect('clicked', check_login)
        #table.attach(button, 0, 2, 4, 5)

        box.pack_start(qltk.Frame(_("Account"), child=table), True, True, 0)

        # second frame
        table = Gtk.Table(n_rows=5, n_columns=2)
        table.props.expand = False
        table.set_col_spacings(6)
        table.set_row_spacings(6)

        label_names = [_("_Artist pattern:"), _("_Title pattern:"),
            _("T_ags:"), _("Exclude _filter:")]

        labels = []
        for idx, name in enumerate(label_names):
            label = Gtk.Label(label=name)
            label.set_alignment(0.0, 0.5)
            label.set_use_underline(True)
            table.attach(label, 0, 1, idx, idx + 1,
                         xoptions=Gtk.AttachOptions.FILL |
                         Gtk.AttachOptions.SHRINK)
            labels.append(label)

        row = 0
        # artist pattern
        entry = UndoEntry()
        entry.set_text(plugin_config.get('artistpat'))
        entry.connect('changed', changed, 'artistpat')
        table.attach(entry, 1, 2, row, row + 1)
        entry.set_tooltip_text(_("The pattern used to format "
            "the artist name for submission. Leave blank for default."))
        labels[row].set_mnemonic_widget(entry)
        row += 1

        # title pattern
        entry = UndoEntry()
        entry.set_text(plugin_config.get('titlepat'))
        entry.connect('changed', changed, 'titlepat')
        table.attach(entry, 1, 2, row, row + 1)
        entry.set_tooltip_text(_("The pattern used to format "
            "the title for submission. Leave blank for default."))
        labels[row].set_mnemonic_widget(entry)
        row += 1

        # tags
        entry = UndoEntry()
        entry.set_text(plugin_config.get('tags'))
        entry.connect('changed', changed, 'tags')
        table.attach(entry, 1, 2, row, row + 1)
        entry.set_tooltip_text(_("List of tags to include in the submission. "
                                 "Comma-separated, use double-quotes if necessary."))
        labels[row].set_mnemonic_widget(entry)
        row += 1

        # exclude filter
        entry = ValidatingEntry(Query.validator)
        entry.set_text(plugin_config.get('exclude'))
        entry.set_tooltip_text(
                _("Songs matching this filter will not be submitted."))
        entry.connect('changed', changed, 'exclude')
        table.attach(entry, 1, 2, row, row + 1)
        labels[row].set_mnemonic_widget(entry)
        row += 1

        # offline mode
        offline = plugin_config.ConfigCheckButton(
                _("_Offline mode (don't submit anything)"),
                'offline', populate=True)
        table.attach(offline, 0, 2, row, row + 1)

        box.pack_start(qltk.Frame(_("Submission"), child=table), True, True, 0)

        return box
