# QLScrobbler: an Audioscrobbler client plugin for Quod Libet.
# version 0.11
# (C) 2005-2016 by Joshua Kwan <joshk@triplehelix.org>,
#                  Joe Wreschnig <piman@sacredchao.net>,
#                  Franz Pletyz <fpletz@franz-pletz.org>,
#                  Nicholas J. Michalek <djphazer@gmail.com>,
#                  Steven Robertson <steven@strobe.cc>
#     2012-2022    Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import threading
import time
from typing import Dict, List
from hashlib import md5
from urllib.parse import urlencode

from gi.repository import Gtk, GLib

import quodlibet
from quodlibet import _
from quodlibet import const, app, util, qltk
from quodlibet.pattern import Pattern
from quodlibet.query import Query
from quodlibet.plugins.events import EventPlugin
from quodlibet.plugins import PluginConfig
from quodlibet.qltk.entry import ValidatingEntry, UndoEntry
from quodlibet.qltk.msg import Message
from quodlibet.qltk import Icons
from quodlibet.util.dprint import print_d, print_w
from quodlibet.util.picklehelper import pickle_load, pickle_dump, PickleError
from quodlibet.util.urllib import urlopen, UrllibError
from quodlibet.errorreport import errorhook

SERVICES = {
    'Last.fm': 'http://post.audioscrobbler.com/',
    'Libre.fm': 'http://turtle.libre.fm/'
}

DEFAULT_SERVICE = 'Last.fm'
DEFAULT_TITLEPAT = '<title><version| (<version>)>'
DEFAULT_ARTISTPAT = '<artist|<artist>|<composer|<composer>|<performer>>>'

plugin_config = PluginConfig("scrobbler")
defaults = plugin_config.defaults
defaults.set("service", DEFAULT_SERVICE)
defaults.set("titlepat", "")
defaults.set("artistpat", "")
defaults.set("url", "")
defaults.set("username", "")
defaults.set("password", "")
defaults.set("exclude", "")
defaults.set("offline", False)


def config_get_url():
    """Gets the URL for the currently configured service.
    This logic was used often enough to be split out from generic config
    """

    # TODO: share this between the classes better
    service = plugin_config.get('service')
    if service in SERVICES:
        return SERVICES[service]
    else:
        return plugin_config.get('url')


def config_get_title_pattern():
    return plugin_config.get('titlepat') or DEFAULT_TITLEPAT


def config_get_artist_pattern():
    return plugin_config.get('artistpat') or DEFAULT_ARTISTPAT


class QLSubmitQueue:
    """Manages the submit queue for scrobbles. Works independently of the
    QLScrobbler plugin being enabled; other plugins may use submit() to queue
    songs for scrobbling.
    """

    CLIENT = "qlb"
    CLIENT_VERSION = const.VERSION
    PROTOCOL_VERSION = "1.2"
    SCROBBLER_CACHE_FILE = os.path.join(quodlibet.get_cache_dir(), "scrobbler_cache_v2")

    # These objects are shared across instances, to allow other plugins to
    # queue scrobbles in future versions of QL
    queue: List[Dict[str, str]] = []
    changed_event = threading.Event()

    def set_nowplaying(self, song):
        """Send a Now Playing notification."""
        formatted = self._format_song(song)
        if not formatted or self.nowplaying_song == formatted:
            return
        self.nowplaying_song = formatted
        self.nowplaying_sent = False
        self.changed()

    def submit(self, song, timestamp=0):
        """Submit a song. If 'timestamp' is 0, the current time will
        be used."""
        formatted = self._format_song(song)
        if formatted is None:
            return
        if timestamp > 0:
            formatted['i'] = str(timestamp)
        elif timestamp == 0:
            formatted['i'] = str(int(time.time()))
        else:
            # TODO: Forging timestamps for submission from PMPs
            return
        self.queue.append(formatted)
        self.changed()

    def _format_song(self, song):
        """Returns a dict with the keys formatted as required by spec."""
        store = {
            "l": str(song.get("~#length", 0)),
            "n": str(song("~#track")),
            "b": song.comma("album"),
            "m": song("musicbrainz_trackid"),
            "t": self.titlepat.format(song),
            "a": self.artpat.format(song),
        }

        # Spec requires title and artist at minimum
        if not (store.get("a") and store.get("t")):
            return None
        return store

    def __init__(self):
        self.nowplaying_song = None
        self.nowplaying_sent = False
        self.sessionid = None
        self.handshake_event = None
        self.handshake_delay = 1.0
        self.failures = 0
        self.handshake_sent = 0
        self.session_id, self.nowplaying_url, self.submit_url = None, None, None

        self.broken = False

        self.username, self.password, self.base_url = ('', '', '')

        # These need to be set early for _format_song to work
        self.titlepat = Pattern(config_get_title_pattern())
        self.artpat = Pattern(config_get_artist_pattern())

        self._load_queue()

    def _load_queue(self):
        try:
            with open(self.SCROBBLER_CACHE_FILE, 'rb') as disk_queue_file:
                disk_queue = pickle_load(disk_queue_file)
            os.unlink(self.SCROBBLER_CACHE_FILE)
            self.queue += disk_queue
        except (EnvironmentError, PickleError):
            pass

    @classmethod
    def dump_queue(klass):
        if klass.queue:
            print_d(f"Saving scrobble queue to {klass.SCROBBLER_CACHE_FILE}")
            try:
                with open(klass.SCROBBLER_CACHE_FILE, 'wb') as disk_queue_file:
                    pickle_dump(klass.queue, disk_queue_file)
            except (EnvironmentError, PickleError) as e:
                print_w(f"Couldn't persist scrobble queue ({e})")

    def _check_config(self):
        user = plugin_config.get('username')
        passw = md5(plugin_config.getbytes('password')).hexdigest()
        url = config_get_url()
        if not user or not passw or not url:
            if self.queue and not self.broken:
                self.quick_dialog(_("Please visit the Plugins window to set "
                                    "QLScrobbler up. Until then, songs will not be "
                                    "submitted."), Gtk.MessageType.INFO)
                self.broken = True
        elif (self.username, self.password, self.base_url) != (user, passw, url):
            self.username, self.password, self.base_url = (user, passw, url)
            self.broken = False
            self.handshake_sent = False
        self.offline = plugin_config.getboolean('offline')
        self.titlepat = Pattern(config_get_title_pattern())
        self.artpat = Pattern(config_get_artist_pattern())

    def changed(self):
        """Signal that settings or queue contents were changed."""
        self._check_config()
        pending = self.queue or (self.nowplaying_song and not self.nowplaying_sent)
        if not self.broken and not self.offline and pending:
            self.changed_event.set()
            return
        self.changed_event.clear()

    def run(self):
        """Submit songs from the queue. Call from a daemon thread."""
        # The spec calls for exponential backoff of failed handshakes, with a
        # minimum of 1m and maximum of 120m delay between attempts.
        self.handshake_sent = False
        self.handshake_event = threading.Event()
        self.handshake_event.set()
        self.handshake_delay = 1

        self.failures = 0

        while True:
            self.changed_event.wait()
            if not self.handshake_sent:
                self.handshake_event.wait()
                if self.send_handshake():
                    self.failures = 0
                    self.handshake_delay = 1
                    self.handshake_sent = True
                else:
                    self.handshake_event.clear()
                    self.handshake_delay = min(self.handshake_delay * 2, 120)
                    GLib.timeout_add(self.handshake_delay * 60 * 1000,
                                     self.handshake_event.set)
                    continue
            self.changed_event.wait()
            if self.queue:
                if self.send_submission():
                    self.failures = 0
                else:
                    self.failures += 1
                    if self.failures >= 3:
                        self.handshake_sent = False
            elif self.nowplaying_song and not self.nowplaying_sent:
                self.send_nowplaying()
                self.nowplaying_sent = True
            else:
                # Nothing left to do; wait until something changes
                self.changed_event.clear()

    def send_handshake(self, show_dialog=False):
        # construct url
        stamp = int(time.time())
        auth = md5(self.password.encode("utf-8") +
                   str(stamp).encode("utf-8")).hexdigest()
        qp = dict(hs="true", p=self.PROTOCOL_VERSION, c=self.CLIENT,
                  v=self.CLIENT_VERSION, u=self.username, a=auth, t=int(stamp))
        qs = urlencode(qp)
        url = f"{self.base_url}/?{qs}"
        print_d(f"Sending handshake to service at {self.base_url}.")

        try:
            resp = urlopen(url)
        except UrllibError:
            if show_dialog:
                self.quick_dialog(
                    _("Could not contact service '%s'.") %
                    util.escape(self.base_url), Gtk.MessageType.ERROR)
            else:
                print_d("Could not contact service. Queueing submissions.")
            return False
        except ValueError:
            self.quick_dialog(_("Authentication failed: invalid URL."),
                              Gtk.MessageType.ERROR)
            self.broken = True
            return False

        # check response
        lines = resp.read().decode("utf-8", "ignore").rstrip().split("\n")
        status = lines.pop(0)

        if status == "OK":
            self.session_id, self.nowplaying_url, self.submit_url = lines
            self.handshake_sent = True
            print_d("Session ID: %s, NP URL: %s, Submit URL: %s" % (
                self.session_id, self.nowplaying_url, self.submit_url))
            return True
        print_w(f"Bad handshake status: {status}")
        if status == "BADAUTH":
            self.quick_dialog(_("Authentication failed: Invalid username '%s' "
                                "or bad password.") % util.escape(self.username),
                              Gtk.MessageType.ERROR)
            self.broken = True
        elif status == "BANNED":
            self.quick_dialog(_("Client is banned. Contact the author."),
                              Gtk.MessageType.ERROR)
            self.broken = True
        elif status == "BADTIME":
            self.quick_dialog(_("Wrong system time. Submissions may fail "
                                "until it is corrected."), Gtk.MessageType.ERROR)
        else:  # "FAILED"
            self.quick_dialog(util.escape(status), Gtk.MessageType.ERROR)
        self.changed()
        return False

    def _check_submit(self, url, data):
        data_str = urlencode(data).encode("ascii")
        try:
            resp = urlopen(url, data_str)
        except EnvironmentError:
            print_d("Audioscrobbler server not responding, will try later.")
            return False

        resp_save = resp.read().decode("utf-8", "ignore")
        status = resp_save.rstrip().split("\n")[0]
        print_d("Submission status: %s" % status)
        if status == "OK":
            return True
        elif status == "BADSESSION":
            self.handshake_sent = False
            return False
        else:
            return False

    def send_submission(self):
        data = {'s': self.session_id}
        to_submit = self.queue[:min(len(self.queue), 50)]
        for idx, song in enumerate(to_submit):
            for key, val in song.items():
                data['%s[%d]' % (key, idx)] = val.encode('utf-8')
            data['o[%d]' % idx] = 'P'
            data['r[%d]' % idx] = ''

        song_info = '\n\t'.join(f"{s['a']} - {s['t']}" for s in to_submit)
        print_d(f"Submitting song(s): {song_info}")

        if self._check_submit(self.submit_url, data):
            del self.queue[:len(to_submit)]
            return True
        else:
            return False

    def send_nowplaying(self):
        data = {'s': self.session_id}
        for key, val in self.nowplaying_song.items():
            data[key] = val.encode('utf-8')
        print_d('Now playing song: %s - %s' %
                (self.nowplaying_song['a'], self.nowplaying_song['t']))

        return self._check_submit(self.nowplaying_url, data)

    def quick_dialog_helper(self, dialog_type, msg):
        dialog = Message(dialog_type, app.window, "QLScrobbler", msg)
        dialog.connect('response', lambda dia, resp: dia.destroy())
        dialog.show()

    def quick_dialog(self, msg, dialog_type):
        GLib.idle_add(self.quick_dialog_helper, dialog_type, msg)


class QLScrobbler(EventPlugin):
    PLUGIN_ID = "QLScrobbler"
    PLUGIN_NAME = _("AudioScrobbler Submission")
    PLUGIN_DESC = _("Audioscrobbler client for Last.fm, Libre.fm and other "
                    "Audioscrobbler services.")
    PLUGIN_ICON = Icons.NETWORK_WORKGROUP

    AUTOSAVE_INTERVAL = 30
    """How often, in seconds, to save the queue (if any) to disk"""

    def __init__(self):
        self.__enabled = False
        self.queue = QLSubmitQueue()
        self._tid = None

        def queue_run():
            try:
                self.queue.run()
            except Exception:
                errorhook()

        queue_thread = threading.Thread(None, queue_run)
        queue_thread.setDaemon(True)
        queue_thread.start()
        print_d(f"Started Scrobbler thread {queue_thread}")

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
        # Spec: * don't submit when song length < 00:30
        #       * submit at end of playback (not in the middle, as with v1.1)
        #       * submit if played for >= .5*length or >= 240s
        # we check 'elapsed' rather than 'length' to work around wrong ~#length
        if self.elapsed < 30:
            return
        if self.elapsed < 240 and self.elapsed <= .5 * song.get("~#length", 0):
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
        if not self._tid:
            print_d("Starting scrobble autosaver")
            self._tid = GLib.timeout_add_seconds(
                self.AUTOSAVE_INTERVAL, lambda: QLSubmitQueue.dump_queue() or True)

    def disabled(self):
        self.__enabled = False
        print_d("Plugin disabled - not accepting any new songs.")
        if self._tid:
            print_d("Stopping scrobble autosaver")
            GLib.source_remove(self._tid)
            self._tid = None
        QLSubmitQueue.dump_queue()

    def PluginPreferences(self, parent):
        def changed(entry, key):
            if entry.get_property('sensitive'):
                plugin_config.set(key, entry.get_text())

        def combo_changed(widget, urlent):
            service = widget.get_active_text()
            plugin_config.set("service", service)
            urlent.set_sensitive((service not in SERVICES))
            urlent.set_text(config_get_url())

        def check_login(*args):
            queue = QLSubmitQueue()
            queue.changed()
            status = queue.send_handshake(show_dialog=True)
            if status:
                queue.quick_dialog(_("Authentication successful."),
                                   Gtk.MessageType.INFO)

        box = Gtk.VBox(spacing=12)

        # first frame
        table = Gtk.Table(n_rows=5, n_columns=2)
        table.props.expand = False
        table.set_col_spacings(6)
        table.set_row_spacings(6)

        labels = []
        label_names = [_("_Service:"), _("_URL:"), _("User_name:"), _("_Password:")]
        for idx, name in enumerate(label_names):
            label = Gtk.Label(label=name)
            label.set_alignment(0.0, 0.5)
            label.set_use_underline(True)
            table.attach(label, 0, 1, idx, idx + 1,
                         xoptions=Gtk.AttachOptions.FILL |
                                  Gtk.AttachOptions.SHRINK)
            labels.append(label)

        row = 0
        service_combo = Gtk.ComboBoxText()
        table.attach(service_combo, 1, 2, row, row + 1)
        cur_service = plugin_config.get('service')

        # Translators: Other service
        other_label = _(u"Other…")
        for idx, serv in enumerate(sorted(SERVICES.keys()) + [other_label]):
            service_combo.append_text(serv)
            if cur_service == serv:
                service_combo.set_active(idx)
        if service_combo.get_active() == -1:
            service_combo.set_active(0)
        labels[row].set_mnemonic_widget(service_combo)
        row += 1

        # url
        entry = UndoEntry()
        entry.set_text(plugin_config.get('url'))
        entry.connect('changed', changed, 'url')
        service_combo.connect('changed', combo_changed, entry)
        service_combo.emit('changed')
        table.attach(entry, 1, 2, row, row + 1)
        labels[row].set_mnemonic_widget(entry)
        row += 1

        # username
        entry = UndoEntry()
        entry.set_text(plugin_config.get('username'))
        entry.connect('changed', changed, 'username')
        table.attach(entry, 1, 2, row, row + 1)
        labels[row].set_mnemonic_widget(entry)
        row += 1

        # password
        entry = UndoEntry()
        entry.set_text(plugin_config.get('password'))
        entry.set_visibility(False)
        entry.connect('changed', changed, 'password')
        table.attach(entry, 1, 2, row, row + 1)
        labels[row].set_mnemonic_widget(entry)
        row += 1

        # verify data
        button = qltk.Button(_("_Verify account data"),
                             Icons.DIALOG_INFORMATION)
        button.connect('clicked', check_login)
        table.attach(button, 0, 2, 4, 5)

        box.pack_start(qltk.Frame(_("Account"), child=table), True, True, 0)

        # second frame
        table = Gtk.Table(n_rows=4, n_columns=2)
        table.props.expand = False
        table.set_col_spacings(6)
        table.set_row_spacings(6)

        label_names = [_("_Artist pattern:"), _("_Title pattern:"),
                       _("Exclude _filter:")]

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
                                 "the artist name for submission. Leave blank for "
                                 "default."))
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

        # exclude filter
        entry = ValidatingEntry(Query.validator)
        entry.set_text(plugin_config.get('exclude'))
        entry.set_tooltip_text(
            _("Songs matching this filter will not be submitted"))
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
