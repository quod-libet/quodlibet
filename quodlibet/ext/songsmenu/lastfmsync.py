# Copyright 2010 Steven Robertson
#           2016 Mice Pápai
#           2022 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import shelve
import time
from datetime import date
from threading import Thread
from urllib.parse import urlencode
import json

from gi.repository import Gtk, GLib

import quodlibet
from quodlibet import _, print_w, print_e
from quodlibet import config, util, qltk
from quodlibet.qltk.entry import UndoEntry
from quodlibet.qltk import Icons
from quodlibet.plugins.songsmenu import SongsMenuPlugin
from quodlibet.util.urllib import urlopen

max_wait = 15

API_KEY = "f536cdadb4c2aec75ae15e2b719cb3a1"


def apicall(method, **kwargs):
    """Performs Last.fm API call."""
    real_args = {'api_key': API_KEY, 'format': 'json', 'method': method}
    real_args.update(kwargs)
    url = ''.join(["https://ws.audioscrobbler.com/2.0/?", urlencode(real_args)])
    uobj = urlopen(url)
    json_text = uobj.read().decode("utf-8")
    resp = json.loads(json_text)
    if 'error' in resp:
        errmsg = f"Last.fm API error: {resp.get('message', '')}"
        print_e(errmsg)
        raise EnvironmentError(resp['error'], errmsg)
    return resp


def config_get(key, default=None):
    return config.get('plugins', f"lastfmsync_{key}", default)


class LastFMSyncCache:
    """Stores the Last.fm charts for a particular user."""

    registered = 0
    lastupdated = None

    def __init__(self, username):
        self.username = username
        self.charts = {}
        self.songs = {}

    def update_charts(self, progress=None):
        """Updates Last.fm charts for the given user. Returns True if an
        update was attempted, False otherwise.

        progress is a callback func (msg, frac) that will be called to
        update a UI. 'frac' may be None to indicate no change should be made.
        If the function returns False, this thread will stop early."""

        def prog(msg, frac):
            if progress:
                if not progress(msg, frac):
                    # this gets caught later
                    raise ValueError()

        try:
            # Last.fm updates their charts weekly; we only poll for new
            # charts if it's been more than a day since the last poll
            if not self.registered:
                resp = apicall('user.getinfo', user=self.username)
                self.registered = int(resp['user']['registered']['unixtime'])

            now = time.time()
            if not self.lastupdated or self.lastupdated + (24 * 60 * 60) < now:
                prog(_("Updating chart list."), 0)
                resp = apicall('user.getweeklychartlist', user=self.username)
                charts = resp['weeklychartlist']['chart']
                for chart in charts:
                    # Charts keys are 2-tuple (from_timestamp, to_timestamp);
                    # values are whether we still need to fetch the chart
                    fro, to = [int(chart[s]) for s in ('from', 'to')]

                    # If the chart is older than the register date of the
                    # user, don't download it. (So the download doesn't start
                    # with ~2005 every time.)
                    if to < self.registered:
                        continue

                    self.charts.setdefault((fro, to), True)
                self.lastupdated = now
            elif not [v for v in self.charts.values() if v]:
                # No charts to fetch, no update scheduled.
                prog(_("Already up-to-date."), 1.)
                return False

            new_charts = [k for k, v in self.charts.items() if v]

            for idx, (fro, to) in enumerate(sorted(new_charts)):
                chart_week = date.fromtimestamp(fro).isoformat()
                prog(_("Fetching chart for week of %s.") % chart_week,
                     (idx + 1.) / (len(new_charts) + 2.))
                args = {'user': self.username, 'from': fro, 'to': to}
                try:
                    resp = apicall('user.getweeklytrackchart', **args)
                except EnvironmentError as err:
                    msg = "HTTP error %d, retrying in %d seconds."
                    print_w(msg % (err.code, max_wait))
                    for i in range(max_wait, 0, -1):
                        time.sleep(1)
                        prog(msg % (err.code, i), None)
                    resp = apicall('user.getweeklytrackchart', **args)
                try:
                    tracks = resp['weeklytrackchart']['track']
                except KeyError:
                    tracks = []
                # Delightfully, the API JSON frontend unboxes 1-element lists.
                if isinstance(tracks, dict):
                    tracks = [tracks]
                for track in tracks:
                    self._update_stats(track, fro, to)
                self.charts[(fro, to)] = False
            prog(_("Sync complete."), 1.)
        except ValueError:
            # this is probably from prog()
            pass
        except Exception as e:
            util.print_exc()
            prog(_("Error during sync (%s)") % e, None)
            return False

        return True

    def _update_stats(self, track, chart_fro, chart_to):
        """Updates a single track's stats. 'track' is as returned by API;
        'chart_fro' and 'chart_to' are the chart's timestamp range."""

        # we try track mbid, (artist mbid, name), (artist name, name) as keys
        keys = []
        if track['mbid']:
            keys.append(track['mbid'])
        for artist in (track['artist']['mbid'], track['artist']['#text']):
            if artist:
                keys.append((artist.lower(), track['name'].lower()))

        stats = list(filter(None, map(self.songs.get, keys)))
        if stats:
            # Not sure if last.fm ever changes their tag values, but this
            # should map all changed values to the same object correctly
            plays = max(map(lambda d: d.get('playcount', 0), stats))
            last = max(map(lambda d: d.get('lastplayed', 0), stats))
            added = max(map(lambda d: d.get('added', chart_to), stats))
            stats = stats[0]
            stats.update({'playcount': plays, 'lastplayed': last, 'added': added})
        else:
            stats = {'playcount': 0, 'lastplayed': 0, 'added': chart_to}

        stats['playcount'] = stats['playcount'] + int(track['playcount'])
        stats['lastplayed'] = max(stats['lastplayed'], chart_fro)
        stats['added'] = min(stats['added'], chart_to)

        for key in keys:
            self.songs[key] = stats

    def update_songs(self, songs):
        """Updates each SongFile in songs from the cache."""
        for song in songs:
            keys = []
            if 'musicbrainz_trackid' in song:
                keys.append(song['musicbrainz_trackid'].lower())
            if 'musiscbrainz_artistid' in song:
                keys.append((song['musicbrainz_artistid'].lower(),
                            song.get('title', '').lower()))
            keys.append((song.get('artist', '').lower(),
                         song.get('title', '').lower()))
            stats = list(filter(None, map(self.songs.get, keys)))
            if not stats:
                continue
            stats = stats[0]

            playcount = max(song.get('~#playcount', 0), stats['playcount'])
            if playcount != 0:
                song['~#playcount'] = playcount
            lastplayed = max(song.get('~#lastplayed', 0), stats['lastplayed'])
            if lastplayed != 0:
                song['~#lastplayed'] = lastplayed
            song['~#added'] = min(song['~#added'], stats['added'])


class LastFMSyncWindow(qltk.Dialog):
    def __init__(self, parent):
        super().__init__(_("Last.fm Sync"), parent)
        self.add_button(_("_Cancel"), Gtk.ResponseType.REJECT)
        self.add_icon_button(_("_Save"), Icons.DOCUMENT_SAVE, Gtk.ResponseType.ACCEPT)
        self.set_border_width(5)
        self.set_default_size(300, 100)

        vbox = Gtk.VBox()
        vbox.set_spacing(12)

        self.progbar = Gtk.ProgressBar()
        vbox.pack_start(self.progbar, False, True, 0)
        self.status = Gtk.Label(label="")
        vbox.pack_start(self.status, True, True, 0)
        self.get_content_area().pack_start(vbox, True, True, 0)

        self.set_response_sensitive(Gtk.ResponseType.ACCEPT, False)
        self.show_all()

    def progress(self, message, fraction):
        self.status.set_text(message)
        if fraction is not None:
            self.progbar.set_fraction(fraction)
            self.progbar.set_text("%2.1f%%" % (fraction * 100))
            if fraction == 1:
                self.set_response_sensitive(Gtk.ResponseType.ACCEPT, True)


class LastFMSync(SongsMenuPlugin):
    PLUGIN_ID = "Last.fm Sync"
    PLUGIN_NAME = _("Last.fm Sync")
    PLUGIN_DESC = _("Updates your library's statistics from your "
                    "Last.fm profile.")
    PLUGIN_ICON = Icons.EMBLEM_SHARED

    CACHE_PATH = os.path.join(quodlibet.get_cache_dir(), "lastfmsync.db")

    def runner(self, cache):
        changed = True
        try:
            changed = cache.update_charts(self.progress)
        except Exception as e:
            print_w(f"Couldn't update cache ({e})")
        if changed:
            self.cache_shelf[cache.username] = cache
        self.cache_shelf.close()

    def progress(self, msg, frac):
        if self.running:
            GLib.idle_add(self.dialog.progress, msg, frac)
            return True
        else:
            return False

    def plugin_songs(self, songs):
        try:
            self.cache_shelf = shelve.open(self.CACHE_PATH)
        except:
            # some Python 2 DB types can't be opened in Python 3
            self.cache_shelf = shelve.open(self.CACHE_PATH, "n")

        user = config_get('username', '')
        try:
            cache = self.cache_shelf.setdefault(user, LastFMSyncCache(user))
        except Exception:
            # unpickle can fail in many ways. this is just cache, so ignore
            cache = self.cache_shelf[user] = LastFMSyncCache(user)

        self.dialog = LastFMSyncWindow(self.plugin_window)
        self.running = True
        thread = Thread(target=self.runner, args=(cache,))
        thread.daemon = True
        thread.start()
        resp = self.dialog.run()
        if resp == Gtk.ResponseType.ACCEPT:
            cache.update_songs(songs)
        self.running = False
        self.dialog.destroy()

    @classmethod
    def PluginPreferences(klass, win):
        def entry_changed(entry):
            config.set('plugins', 'lastfmsync_username', entry.get_text())

        label = Gtk.Label(label=_("_Username:"), use_underline=True)
        entry = UndoEntry()
        entry.set_text(config_get('username', ''))
        entry.connect('changed', entry_changed)
        label.set_mnemonic_widget(entry)

        hbox = Gtk.HBox()
        hbox.set_spacing(6)
        hbox.pack_start(label, False, True, 0)
        hbox.pack_start(entry, True, True, 0)

        return qltk.Frame(_("Account"), child=hbox)
