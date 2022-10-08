# Copyright 2005 Joe Wreschnig
#      2017-2022 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import sys
import threading
import time
from urllib.request import urlopen, Request

from gi.repository import Gtk, GLib, Pango, Gdk
import feedparser

import quodlibet
from quodlibet import _
from quodlibet import config
from quodlibet import formats
from quodlibet import print_d
from quodlibet import qltk
from quodlibet import util
from quodlibet import app

from quodlibet.browsers import Browser
from quodlibet.formats import AudioFile
from quodlibet.formats.remote import RemoteFile
from quodlibet.qltk.getstring import GetStringDialog
from quodlibet.qltk.msg import ErrorMessage
from quodlibet.qltk.songsmenu import SongsMenu
from quodlibet.qltk.views import AllTreeView
from quodlibet.qltk import Icons
from quodlibet.util import connect_obj, print_w
from quodlibet.qltk.x import ScrolledWindow, Align, Button, MenuItem
from quodlibet.util.path import uri_is_valid
from quodlibet.util.picklehelper import pickle_load, pickle_dump, PickleError


FEEDS = os.path.join(quodlibet.get_data_dir(), "feeds")
DND_URI_LIST, DND_MOZ_URL = range(2)

# Migration path for pickle
sys.modules["browsers.audiofeeds"] = sys.modules[__name__]


class InvalidFeed(ValueError):
    pass


class Feed(list):
    def __init__(self, uri):
        self.name = _("Unknown")
        self.uri = uri
        self.changed = False
        self.website = ""
        self.__lastgot = 0

    def get_age(self):
        return time.time() - self.__lastgot

    @staticmethod
    def __fill_af(feed, af):
        try:
            af["title"] = feed.title or _("Unknown")
        except (TypeError, AttributeError):
            af["title"] = _("Unknown")
        try:
            af["date"] = "%04d-%02d-%02d" % feed.modified_parsed[:3]
        except (AttributeError, TypeError):
            pass

        for songkey, feedkey in [
                ("website", "link"),
                ("description", "tagline"),
                ("language", "language"),
                ("copyright", "copyright"),
                ("organization", "publisher"),
                ("license", "license")]:
            try:
                value = getattr(feed, feedkey)
            except AttributeError:
                pass
            else:
                if value and value not in af.list(songkey):
                    af.add(songkey, value)

        try:
            af.add("artwork_url", feed.image["href"])
        except (AttributeError, KeyError):
            pass
        try:
            author = feed.author_detail
        except AttributeError:
            try:
                author = feed.author
            except AttributeError:
                pass
            else:
                if author and author not in af.list("artist"):
                    af.add('artist', author)
        else:
            try:
                if author.email and author.email not in af.list("contact"):
                    af.add("contact", author.email)
            except AttributeError:
                pass
            try:
                if author.name and author.name not in af.list("artist"):
                    af.add("artist", author.name)
            except AttributeError:
                pass

        try:
            values = feed.contributors
        except AttributeError:
            pass
        else:
            for value in values:
                try:
                    value = value.name
                except AttributeError:
                    pass
                else:
                    if value and value not in af.list("performer"):
                        af.add("performer", value)

        try:
            af["~#length"] = util.parse_time(feed.itunes_duration)
        except (AttributeError, ValueError):
            pass

        try:
            values = dict(feed.categories).values()
        except AttributeError:
            pass
        else:
            for value in values:
                if value and value not in af.list("genre"):
                    af.add("genre", value)

    def parse(self):
        req = Request(self.uri, method="HEAD")
        try:
            with urlopen(req, timeout=5) as head:
                # Some requests don't support status, e.g. file://
                if hasattr(head, "status"):
                    print_d(f"Feed URL {self.uri!r} ({head.url}) "
                            f"returned HTTP {head.status}, "
                            f"with content {head.headers.get('Content-Type')}")
                    if head.status and head.status >= 400:
                        return False
                if head.headers.get("Content-Type").lower().startswith("audio"):
                    print_w("Looks like an audio stream / radio, not a audio feed.")
                    return False
            # Don't pass feedparser URLs
            # see https://github.com/kurtmckee/feedparser/pull/80#issuecomment-449543486
            content = urlopen(self.uri, timeout=15).read()
        except IOError as e:
            print_w(f"Couldn't fetch content from {self.uri} ({e})")
            return False
        try:
            doc = feedparser.parse(content)
        except Exception as e:
            print_w(f"Couldn't parse feed: {self.uri} ({e})")
            return False

        try:
            album = doc.channel.title
        except AttributeError:
            print_w("No channel title in %s" % doc)
            return False

        if album:
            self.name = album
        else:
            self.name = _("Unknown")

        defaults = AudioFile({"feed": self.uri})
        try:
            self.__fill_af(doc.channel, defaults)
        except Exception as e:
            print_w("Error creating feed data: %s (%s)" % (self.uri, e))
            return False

        entries = []
        uris = set()
        print_d("Found %d entries in channel" % len(doc.entries))
        for entry in doc.entries:
            try:
                for enclosure in entry.enclosures:
                    try:
                        if ("audio" in enclosure.type or
                                "ogg" in enclosure.type or
                                formats.filter(enclosure.url)):
                            uri = enclosure.url
                            if not isinstance(uri, str):
                                uri = uri.decode('utf-8')
                            try:
                                size = float(enclosure.length)
                            except (AttributeError, ValueError):
                                size = 0
                            entries.append((uri, entry, size))
                            uris.add(uri)
                            break
                    except AttributeError:
                        pass
            except AttributeError:
                print_d("No enclosures found in %s" % entry)

        for entry in list(self):
            if entry["~uri"] not in uris:
                self.remove(entry)
            else:
                uris.remove(entry["~uri"])
        print_d("Successfully got %d episodes in channel" % len(entries))
        entries.reverse()
        for uri, entry, size in entries:
            if uri in uris:
                song = RemoteFile(uri)
                song["~#size"] = size
                song.fill_metadata = False
                song.update(defaults)
                song["album"] = self.name
                try:
                    self.__fill_af(entry, song)
                except Exception as e:
                    print_d("Couldn't convert %s to AudioFile (%s)" % (uri, e))
                else:
                    self.insert(0, song)
        self.__lastgot = time.time()
        return bool(uris)


class AddFeedDialog(GetStringDialog):
    def __init__(self, parent):
        super().__init__(
            qltk.get_top_parent(parent), _("New Feed"),
            _("Enter the podcast / audio feed location:"),
            button_label=_("_Add"), button_icon=Icons.LIST_ADD)

    def run(self, text='', clipboard=True, test=False):
        uri = super().run(text=text, clipboard=clipboard, test=test)
        if uri:
            if not isinstance(uri, str):
                uri = uri.decode('utf-8')
            return Feed(uri)
        return None

    def _verify_clipboard(self, text):
        # try to extract a URI from the clipboard
        for line in text.splitlines():
            line = line.strip()

            if uri_is_valid(line):
                return line


def hacky_py2_unpickle_recover(fileobj):
    """Can raise anything"""

    # We just recover the uri and let it refresh the feed later

    def lookup_func(func, mod, name):

        class Feed(list):
            pass

        class Bar(dict):
            pass

        if name == "Feed":
            return Feed
        else:
            return Bar

    with open(FEEDS, "rb") as fileobj:
        feeds = pickle_load(fileobj, lookup_func)

    uris = []
    for item in feeds:
        d = item.__dict__
        if b"uri" in d:
            v = d[b"uri"]
        elif "uri" in d:
            v = d["uri"]
        else:
            continue
        if isinstance(v, bytes):
            v = v.decode("utf-8")
        uris.append(v)

    return [Feed(u) for u in uris]


class Podcasts(Browser):
    """
    Allows interacting with remote feeds for playing and exploring podcasts
    and their episodes (or other forms of audio feeds)
    Formerly known as the AudioFeeds browser.
    """
    __feeds = Gtk.ListStore(object)  # unread

    headers = ("title artist performer ~people album date website language "
               "copyright organization license contact").split()

    name = _("Podcasts")
    accelerated_name = _("_Podcasts")
    keys = ["AudioFeeds", "Podcasts"]
    priority = 20
    uses_main_library = False

    def pack(self, songpane):
        container = qltk.ConfigRHPaned("browsers", "audiofeeds_pos", 0.4)
        self.show()
        container.pack1(self, True, False)
        container.pack2(songpane, True, False)
        return container

    def unpack(self, container, songpane):
        container.remove(songpane)
        container.remove(self)

    @staticmethod
    def cell_data(col, render, model, iter, data):
        if model[iter][0].changed:
            render.markup = "<b>%s</b>" % util.escape(model[iter][0].name)
        else:
            render.markup = util.escape(model[iter][0].name)
        render.set_property('markup', render.markup)

    @classmethod
    def changed(klass, feeds):
        for row in klass.__feeds:
            if row[0] in feeds:
                row[0].changed = True
                row[0] = row[0]
        Podcasts.write()

    @classmethod
    def write(klass):
        feeds = [row[0] for row in klass.__feeds]
        with open(FEEDS, "wb") as f:
            pickle_dump(feeds, f, 2)

    @classmethod
    def init(klass, library):
        uris = set()
        feeds = []

        try:
            with open(FEEDS, "rb") as fileobj:
                feeds = pickle_load(fileobj)
        except (PickleError, EnvironmentError):
            try:
                with open(FEEDS, "rb") as fileobj:
                    feeds = hacky_py2_unpickle_recover(fileobj)
            except Exception:
                pass

        for feed in feeds:
            if feed.uri in uris:
                continue
            klass.__feeds.append(row=[feed])
            uris.add(feed.uri)

        GLib.idle_add(klass.__do_check)

    @classmethod
    def reload(klass, library):
        klass.__feeds = Gtk.ListStore(object)  # unread
        klass.init(library)

    @classmethod
    def __do_check(klass):
        thread = threading.Thread(target=klass.__check, args=(), daemon=True)
        thread.start()

    @classmethod
    def __check(klass):
        for row in klass.__feeds:
            feed = row[0]
            if feed.get_age() < 2 * 60 * 60:
                continue
            elif feed.parse():
                feed.changed = True
                row[0] = feed
        klass.write()
        GLib.timeout_add(60 * 60 * 1000, klass.__do_check)

    def __init__(self, library):
        super().__init__(spacing=6)
        self.set_orientation(Gtk.Orientation.VERTICAL)

        self.__view = view = AllTreeView()
        self.__render = render = Gtk.CellRendererText()
        render.set_property('ellipsize', Pango.EllipsizeMode.END)
        col = Gtk.TreeViewColumn("Audio Feeds", render)
        col.set_cell_data_func(render, Podcasts.cell_data)
        view.append_column(col)
        view.set_model(self.__feeds)
        view.set_rules_hint(True)
        view.set_headers_visible(False)
        swin = ScrolledWindow()
        swin.set_shadow_type(Gtk.ShadowType.IN)
        swin.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        swin.add(view)
        self.pack_start(swin, True, True, 0)

        new = Button(_("_Add Feed…"), Icons.LIST_ADD, Gtk.IconSize.MENU)
        new.connect('clicked', self.__new_feed)
        view.get_selection().connect('changed', self.__changed)
        view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        view.connect('popup-menu', self.__popup_menu)

        targets = [
            ("text/uri-list", 0, DND_URI_LIST),
            ("text/x-moz-url", 0, DND_MOZ_URL)
        ]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]

        view.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        view.connect('drag-data-received', self.__drag_data_received)
        view.connect('drag-motion', self.__drag_motion)
        view.connect('drag-leave', self.__drag_leave)

        connect_obj(self, 'destroy', self.__save, view)

        self.pack_start(Align(new, left=3, bottom=3), False, True, 0)

        for child in self.get_children():
            child.show_all()

    def Menu(self, songs, library, items):
        return SongsMenu(library, songs, download=True, items=items)

    def __drag_motion(self, view, ctx, x, y, time):
        targets = [t.name() for t in ctx.list_targets()]
        if "text/x-quodlibet-songs" not in targets:
            view.get_parent().drag_highlight()
            return True
        return False

    def __drag_leave(self, view, ctx, time):
        view.get_parent().drag_unhighlight()

    def __drag_data_received(self, view, ctx, x, y, sel, tid, etime):
        view.emit_stop_by_name('drag-data-received')
        targets = [
            ("text/uri-list", 0, DND_URI_LIST),
            ("text/x-moz-url", 0, DND_MOZ_URL)
        ]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]

        view.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        if tid == DND_URI_LIST:
            uri = sel.get_uris()[0]
        elif tid == DND_MOZ_URL:
            uri = sel.data.decode('utf16', 'replace').split('\n')[0]
        else:
            ctx.finish(False, False, etime)
            return

        ctx.finish(True, False, etime)

        feed = Feed(uri.encode("ascii", "replace"))
        feed.changed = feed.parse()
        if feed:
            self.__feeds.append(row=[feed])
            Podcasts.write()
        else:
            self.feed_error(feed).run()

    def __popup_menu(self, view):
        model, paths = view.get_selection().get_selected_rows()
        menu = Gtk.Menu()
        refresh = MenuItem(_("_Refresh"), Icons.VIEW_REFRESH,
               tooltip=_("Search source for new episodes"))
        rebuild = MenuItem(_("_Rebuild"), Icons.EDIT_FIND_REPLACE,
               tooltip=_("Remove all existing episodes then reload from source"))
        delete = MenuItem(_("_Delete"), Icons.EDIT_DELETE,
                tooltip=_("Remove this podcast and its episodes"))

        connect_obj(refresh, 'activate',
                    self.__refresh, [model[p][0] for p in paths])
        connect_obj(rebuild, 'activate',
                    self.__rebuild, [model[p][0] for p in paths])
        connect_obj(delete, 'activate', self.__remove_paths, model, paths)

        menu.append(refresh)
        menu.append(rebuild)
        menu.append(delete)
        menu.show_all()
        menu.connect('selection-done', lambda m: m.destroy())

        # XXX: keep the menu around
        self.__menu = menu

        return view.popup_menu(menu, 0, Gtk.get_current_event_time())

    def __save(self, view):
        Podcasts.write()

    def __refresh(self, feeds):
        changed = list(filter(Feed.parse, feeds))
        Podcasts.changed(changed)

    def __remove_paths(self, model, paths):
        for path in paths:
            model.remove(model.get_iter(path))

    def activate(self):
        self.__changed(self.__view.get_selection())

    def __changed(self, selection):
        model, paths = selection.get_selected_rows()
        if model and paths:
            songs = []
            for path in paths:
                model[path][0].changed = False
                songs.extend(model[path][0])
            self.songs_selected(songs, True)
            config.set("browsers", "audiofeeds",
                       "\t".join([model[path][0].name for path in paths]))

    def __new_feed(self, activator):
        feed = AddFeedDialog(self).run()
        if feed is not None:
            feed.changed = feed.parse()
            if feed:
                self.__feeds.append(row=[feed])
                Podcasts.write()
            else:
                self.feed_error(feed).run()

    def feed_error(self, feed: Feed) -> ErrorMessage:
        return ErrorMessage(
            self,
            _("Unable to add feed"),
            _("%s could not be added. The server may be down, "
              "or the location may not be a podcast / audio feed.") %
            util.bold(util.escape(feed.uri)), escape_desc=False)

    def restore(self):
        try:
            names = config.get("browsers", "audiofeeds").split("\t")
        except Exception:
            pass
        else:
            self.__view.select_by_func(lambda r: r[0].name in names)


browsers = []
if not app.player or app.player.can_play_uri("http://"):
    browsers = [Podcasts]
else:
    print_w(_("The current audio backend does not support URLs, "
              "Podcast browser disabled."))
