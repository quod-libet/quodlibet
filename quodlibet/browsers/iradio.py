# Copyright 2011 Joe Wreschnig, Christoph Reiter
#      2013-2022 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import sys
import bz2
import itertools
from functools import reduce
from http.client import HTTPException
from os.path import splitext
from threading import Thread
from typing import Dict, Collection, Callable, Iterable, Optional
from urllib.request import urlopen

import re
from gi.repository import Gtk, GLib, Pango
from senf import text2fsn

from quodlibet.util.dprint import print_d, print_e

import quodlibet
from quodlibet import _
from quodlibet import qltk
from quodlibet import util
from quodlibet import config

from quodlibet.browsers import Browser
from quodlibet.formats.remote import RemoteFile
from quodlibet.formats._audio import TAG_TO_SORT, MIGRATE, AudioFile
from quodlibet.library import SongLibrary
from quodlibet.query import Query
from quodlibet.qltk.getstring import GetStringDialog
from quodlibet.qltk.songsmenu import SongsMenu
from quodlibet.qltk.notif import Task
from quodlibet.qltk import Icons, ErrorMessage, WarningMessage
from quodlibet.util import copool, connect_destroy, sanitize_tags, connect_obj
from quodlibet.util.i18n import numeric_phrase
from quodlibet.util.path import uri_is_valid
from quodlibet.util.string import decode, encode
from quodlibet.util import print_w
from quodlibet.qltk.views import AllTreeView
from quodlibet.qltk.searchbar import SearchBarBox
from quodlibet.qltk.completion import LibraryTagCompletion
from quodlibet.qltk.x import MenuItem, Align, ScrolledWindow, Button

STATION_LIST_URL = \
    "https://quodlibet.github.io/radio/radiolist.bz2"
STATIONS_FAV = os.path.join(quodlibet.get_data_dir(), "stations")
STATIONS_ALL = os.path.join(quodlibet.get_cache_dir(), "stations_all")

# TODO: - Ranking: reduce duplicate stations (max 3 URLs per station)
#                  prefer stations that match a genre?

# Migration path for pickle
sys.modules["browsers.iradio"] = sys.modules[__name__]


class IRadioError(Exception):
    pass


class IRFile(RemoteFile):
    multisong = True
    can_add = False

    format = "Radio Station"

    __CAN_CHANGE = "title artist grouping".split()

    def __get(self, base_call, key, *args, **kwargs):
        if key == "title" and "title" not in self and "organization" in self:
            return base_call("organization", *args, **kwargs)

        # split title by " - " if no artist tag is present and
        # this is not the main song: common format for shoutcast stations
        if not self.multisong and key in ("title", "artist") and \
                "title" in self and "artist" not in self:
            title = base_call("title").split(" - ", 1)
            if len(title) > 1:
                return (key == "title" and title[-1]) or title[0]

        if key in ("artist", TAG_TO_SORT["artist"]) and \
                not base_call(key, *args) and "website" in self:
            return base_call("website", *args)

        if key == "~format" and "audio-codec" in self:
            return "%s (%s)" % (self.format,
                                base_call("audio-codec", *args, **kwargs))
        return base_call(key, *args, **kwargs)

    def __call__(self, key, *args, **kwargs):
        base_call = super().__call__
        return self.__get(base_call, key, *args, **kwargs)

    def get(self, key, *args, **kwargs):
        base_call = super().get
        return self.__get(base_call, key, *args, **kwargs)

    def write(self):
        pass

    def to_dump(self):
        # dump without title
        title = None
        if "title" in self:
            title = self["title"]
            del self["title"]
        dump = super().to_dump()
        if title is not None:
            self["title"] = title

        # add all generated tags
        lines = dump.splitlines()
        for tag in ["title", "artist", "~format"]:
            value = self.get(tag)
            if value is not None:
                lines.append(encode(tag) + b"=" + encode(value))
        return b"\n".join(lines)

    @property
    def lyric_filename(self) -> Optional[str]:
        return None

    def can_change(self, k=None):
        if self.streamsong:
            if k is None:
                return []
            else:
                return False
        else:
            if k is None:
                return self.__CAN_CHANGE
            else:
                return k in self.__CAN_CHANGE


def parse_pls(file) -> Collection[IRFile]:
    data = {}

    lines = file.read().decode('utf-8', 'replace').splitlines()

    if not lines or "[playlist]" not in lines.pop(0):
        return []

    for line in lines:
        try:
            head, val = line.strip().split("=", 1)
        except (TypeError, ValueError):
            continue
        else:
            head = head.lower()
            if head.startswith("length") and val == "-1":
                continue
            else:
                data[head] = val

    count = 1
    files = []
    warnings = []
    while True:
        if "file%d" % count in data:
            filename = text2fsn(data["file%d" % count])
            if filename.lower()[-4:] in [".pls", ".m3u", "m3u8"]:
                warnings.append(filename)
            else:
                irf = IRFile(filename)
                for key in ["title", "genre", "artist"]:
                    try:
                        irf[key] = data["%s%d" % (key, count)]
                    except KeyError:
                        pass
                try:
                    irf["~#length"] = int(data["length%d" % count])
                except (KeyError, TypeError, ValueError):
                    pass
                files.append(irf)
        else:
            break
        count += 1

    if warnings:
        raise IRadioError(
            _("Station lists can only contain locations of stations, "
              "not other station lists or playlists. The following locations "
              "cannot be loaded:\n%s") %
            "\n  ".join(map(util.escape, warnings)))
    return files


def parse_m3u(fileobj) -> Collection[IRFile]:
    files = []
    pending_title = None
    lines = fileobj.read().decode('utf-8', 'replace').splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                pending_title = line.split(",", 1)[1]
            except IndexError:
                pending_title = None
        elif line.startswith("http"):
            irf = IRFile(text2fsn(line))
            if pending_title:
                irf["title"] = pending_title
                pending_title = None
            files.append(irf)
    return files


def _get_stations_from(uri: str,
                       on_done: Callable[[Iterable[IRFile], str], None])\
        -> None:
    """Fetches the URI content and extracts IRFiles
       Called from thread - so no direct GTK+ interaction
       :param uri: URI of station
       :param on_done: a callback taking files when done (or none if errored)
       """

    with Task(_("Internet Radio"), _("Add stations")) as task:
        irfs: Collection[IRFile] = []
        GLib.idle_add(task.pulse)
        if (uri.lower().endswith(".pls")
                or uri.lower().endswith(".m3u")
                or uri.lower().endswith(".m3u8")):
            if not re.match('^([^/:]+)://', uri):
                # Assume HTTP if no protocol given. See #2731
                uri = 'http://' + uri
                print_d("Assuming http: %s" % uri)

            # Error handling outside
            sock = None
            GLib.idle_add(task.pulse)
            _fn, ext = splitext(uri.lower())
            try:
                sock = urlopen(uri, timeout=6)
                if ext == ".pls":
                    irfs = parse_pls(sock)
                elif ext in (".m3u", ".m3u8"):
                    irfs = parse_m3u(sock)
                GLib.idle_add(task.pulse)
            except IOError as e:
                print_e(f"Couldn't download from {uri} ({e})")
            finally:
                if sock:
                    sock.close()
        else:
            try:
                irfs = [IRFile(uri)]
            except ValueError as e:
                print_e("Can't add URI %s" % uri, e)
    on_done(irfs, uri)


def download_taglist(url, callback, cofuncid, step=1024 * 10):
    """Generator for loading the bz2 compressed tag list.

    Calls callback with the decompressed data or None in case of
    an error."""

    with Task(_("Internet Radio"), _("Downloading station list")) as task:
        if cofuncid:
            task.copool(cofuncid)

        try:
            response = urlopen(url)
        except (EnvironmentError, HTTPException) as e:
            print_e(f"Failed fetching from {url}", e)
            GLib.idle_add(callback, None)
            return
        try:
            size = int(response.info().get("content-length", 0))
        except ValueError:
            size = 0

        decomp = bz2.BZ2Decompressor()

        data = b""
        temp = b""
        read = 0
        while temp or not data:
            read += len(temp)

            if size:
                task.update(float(read) / size)
            else:
                task.pulse()
            yield True

            try:
                data += decomp.decompress(temp)
                temp = response.read(step)
            except (IOError, EOFError):
                data = None
                break
        response.close()

        yield True

        stations = None
        if data:
            stations = parse_taglist(data)
        print_d(f"Got {len(stations or [])} station(s)")
        GLib.idle_add(callback, stations)


def parse_taglist(data):
    """Parses a dump file like list of tags and returns a list of IRFiles

    uri=http://...
    tag=value1
    tag2=value
    tag=value2
    uri=http://...
    ...

    """

    stations = []
    station = None

    for l in data.split(b"\n"):
        if not l:
            continue
        key = l.split(b"=")[0]
        value = l.split(b"=", 1)[1]
        key = decode(key)
        value = decode(value)
        if key == "uri":
            if station:
                stations.append(station)
            station = IRFile(value)
            continue

        san = list(sanitize_tags({key: value}, stream=True).items())
        if not san:
            continue

        key, value = san[0]
        if key == "~listenerpeak":
            key = "~#listenerpeak"
            value = int(value)

        if not station:
            continue

        if isinstance(value, str):
            if value not in station.list(key):
                station.add(key, value)
        else:
            station[key] = value

    if station:
        stations.append(station)

    return stations


class AddNewStation(GetStringDialog):
    def __init__(self, parent):
        super().__init__(
            parent, _("New Station"),
            _("Enter the location of an Internet radio station:"),
            button_label=_("_Add"), button_icon=Icons.LIST_ADD)

    def _verify_clipboard(self, text):
        # try to extract a URI from the clipboard
        for line in text.splitlines():
            line = line.strip()

            if uri_is_valid(line):
                return line


class GenreFilter:
    STAR = ["genre", "organization"]

    # This probably needs improvements
    GENRES = {
        "electronic": (
            _("Electronic"),
            "|(electr,house,techno,trance,/trip.?hop/,&(drum,n,bass),chill,"
            "dnb,minimal,/down(beat|tempo)/,&(dub,step))"),
        "rap": (_("Hip Hop / Rap"), "|(&(hip,hop),rap)"),
        "oldies": (_("Oldies"), r"|(/[2-9]0\S?s/,oldies)"),
        "r&b": (_("R&B"), r"/r(\&|n)b/"),
        "japanese": (_("Japanese"), "|(anime,jpop,japan,jrock)"),
        "indian": (_("Indian"), "|(bollywood,hindi,indian,bhangra)"),
        "religious": (
            _("Religious"),
            "|(religious,christian,bible,gospel,spiritual,islam)"),
        "charts": (_("Charts"), "|(charts,hits,top)"),
        "turkish": (_("Turkish"), "|(turkish,turkce)"),
        "reggae": (_("Reggae / Dancehall"), r"|(/reggae([^\w]|$)/,dancehall)"),
        "latin": (_("Latin"), "|(latin,salsa)"),
        "college": (_("College Radio"), "|(college,campus)"),
        "talk_news": (_("Talk / News"), "|(news,talk)"),
        "ambient": (_("Ambient"), "|(ambient,easy)"),
        "jazz": (_("Jazz"), "|(jazz,swing)"),
        "classical": (_("Classical"), "classic"),
        "pop": (_("Pop"), None),
        "alternative": (_("Alternative"), None),
        "metal": (_("Metal"), None),
        "country": (_("Country"), None),
        "news": (_("News"), None),
        "schlager": (_("Schlager"), None),
        "funk": (_("Funk"), None),
        "indie": (_("Indie"), None),
        "blues": (_("Blues"), None),
        "soul": (_("Soul"), None),
        "lounge": (_("Lounge"), None),
        "punk": (_("Punk"), None),
        "reggaeton": (_("Reggaeton"), None),
        "slavic": (
            _("Slavic"),
            "|(narodna,albanian,manele,shqip,kosova)"),
        "greek": (_("Greek"), None),
        "gothic": (_("Gothic"), None),
        "rock": (_("Rock"), None),
    }

    # parsing all above takes 350ms on an atom, so only generate when needed
    __CACHE: Dict[str, Query] = {}

    def keys(self):
        return self.GENRES.keys()

    def query(self, key):
        if key not in self.__CACHE:
            text, filter_ = self.GENRES[key]
            if filter_ is None:
                filter_ = key
            self.__CACHE[key] = Query(filter_, star=self.STAR)
        return self.__CACHE[key]

    def text(self, key):
        return self.GENRES[key][0]


class CloseButton(Gtk.Button):
    """Reimplementation of 3.10 close button for InfoBar."""

    def __init__(self):
        image = Gtk.Image(visible=True, can_focus=False,
                          icon_name="window-close-symbolic")

        super().__init__(
            visible=False, can_focus=True, image=image,
            relief=Gtk.ReliefStyle.NONE, valign=Gtk.Align.CENTER)

        ctx = self.get_style_context()
        ctx.add_class("raised")
        ctx.add_class("close")


class QuestionBar(Gtk.InfoBar):
    """A widget which suggest to download the radio list if
    no radio stations are present.

    Connect to Gtk.InfoBar::response and check for RESPONSE_LOAD
    as response id.
    """

    RESPONSE_LOAD = 1

    def __init__(self):
        super().__init__()
        self.connect("response", self.__response)
        self.set_message_type(Gtk.MessageType.QUESTION)

        label = Gtk.Label(label=_(
            "Would you like to load a list of popular radio stations?"))
        label.set_line_wrap(True)
        label.show()
        content = self.get_content_area()
        content.add(label)

        self.add_button(_("_Load Stations"), self.RESPONSE_LOAD)
        self.set_show_close_button(True)

    def __response(self, bar, response_id):
        if response_id == Gtk.ResponseType.CLOSE:
            bar.hide()


class InternetRadio(Browser, util.InstanceTracker):
    __stations = None
    __fav_stations = None
    __librarian = None

    __filter = None

    name = _("Internet Radio")
    accelerated_name = _("_Internet Radio")
    keys = ["InternetRadio"]
    priority = 16
    uses_main_library = False
    headers = "title artist ~people grouping genre website ~format " \
        "channel-mode".split()

    TYPE, ICON_NAME, KEY, NAME = range(4)
    TYPE_FILTER, TYPE_ALL, TYPE_FAV, TYPE_SEP, TYPE_NOCAT = range(5)
    STAR = ["artist", "title", "website", "genre", "comment"]

    @classmethod
    def _init(klass, library):
        klass.__librarian = library.librarian

        klass.__stations = SongLibrary("iradio-remote")
        klass.__stations.load(STATIONS_ALL)

        klass.__fav_stations = SongLibrary("iradio")
        klass.__fav_stations.load(STATIONS_FAV)

        klass.filters = GenreFilter()

    @classmethod
    def _destroy(klass):
        if klass.__stations.dirty:
            klass.__stations.save()
        klass.__stations.destroy()
        klass.__stations = None

        if klass.__fav_stations.dirty:
            klass.__fav_stations.save()
        klass.__fav_stations.destroy()
        klass.__fav_stations = None

        klass.__librarian = None

        klass.filters = None

    def finalize(self, restored):
        if not restored:
            # Select "All Stations" by default
            def sel_all(row):
                return row[self.TYPE] == self.TYPE_ALL
            self.view.select_by_func(sel_all, one=True)

    def __inhibit(self):
        self.view.get_selection().handler_block(self.__changed_sig)

    def __uninhibit(self):
        self.view.get_selection().handler_unblock(self.__changed_sig)

    def __destroy(self, *args):
        if not self.instances():
            self._destroy()

    def __init__(self, library, station_list_url: str = STATION_LIST_URL):
        super().__init__(spacing=12)
        self.station_list_url = station_list_url
        self.set_orientation(Gtk.Orientation.VERTICAL)

        if not self.instances():
            self._init(library)
        self._register_instance()

        self.connect('destroy', self.__destroy)

        completion = LibraryTagCompletion(self.__stations)
        self.accelerators = Gtk.AccelGroup()
        self.__searchbar = search = SearchBarBox(completion=completion,
                                                 accel_group=self.accelerators)
        search.connect('query-changed', self.__filter_changed)

        def focus(widget, *args):
            qltk.get_top_parent(widget).songlist.grab_focus()
        search.connect('focus-out', focus)

        # treeview
        scrolled_window = ScrolledWindow()
        scrolled_window.show()
        scrolled_window.set_shadow_type(Gtk.ShadowType.IN)
        self.view = view = AllTreeView()
        view.show()
        view.set_headers_visible(False)
        scrolled_window.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.add(view)
        model = Gtk.ListStore(int, str, str, str)

        model.append(row=[self.TYPE_ALL, Icons.FOLDER, "__all", _("All Stations")])
        model.append(row=[self.TYPE_SEP, Icons.FOLDER, "", ""])
        # Translators: Favorite radio stations
        model.append(row=[self.TYPE_FAV, Icons.FOLDER, "__fav", _("Favorites")])
        model.append(row=[self.TYPE_SEP, Icons.FOLDER, "", ""])

        filters = self.filters
        for text, k in sorted([(filters.text(k), k) for k in filters.keys()]):
            model.append(row=[self.TYPE_FILTER, Icons.EDIT_FIND, k, text])

        model.append(row=[self.TYPE_NOCAT, Icons.FOLDER,
                          "nocat", _("No Category")])

        def separator(model, iter, data):
            return model[iter][self.TYPE] == self.TYPE_SEP
        view.set_row_separator_func(separator, None)

        def search_func(model, column, key, iter, data):
            return key.lower() not in model[iter][column].lower()
        view.set_search_column(self.NAME)
        view.set_search_equal_func(search_func, None)

        column = Gtk.TreeViewColumn("genres")
        column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)

        renderpb = Gtk.CellRendererPixbuf()
        renderpb.props.xpad = 3
        column.pack_start(renderpb, False)
        column.add_attribute(renderpb, "icon-name", self.ICON_NAME)

        render = Gtk.CellRendererText()
        render.set_property('ellipsize', Pango.EllipsizeMode.END)
        view.append_column(column)
        column.pack_start(render, True)
        column.add_attribute(render, "text", self.NAME)

        view.set_model(model)

        # selection
        selection = view.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        self.__changed_sig = connect_destroy(selection, 'changed',
            util.DeferredSignal(lambda x: self.activate()))

        box = Gtk.HBox(spacing=6)
        box.pack_start(search, True, True, 0)
        self._searchbox = Align(box, left=0, right=6, top=0)
        self._searchbox.show_all()

        def qbar_response(infobar, response_id):
            if response_id == infobar.RESPONSE_LOAD:
                self.__update()

        self.qbar = QuestionBar()
        self.qbar.connect("response", qbar_response)
        if not self.has_stations:
            self.qbar.show()

        pane = qltk.ConfigRHPaned("browsers", "internetradio_pos", 0.4)
        vb = Gtk.VBox(spacing=0)
        vb.pack_start(scrolled_window, True, True, 0)
        fb = Gtk.FlowBox()
        fb.set_column_spacing(3)
        fb.set_homogeneous(True)
        new_station = Button(_(u"_Add Station…"), Icons.LIST_ADD)
        new_station.connect('clicked', self.__add)
        self._update_button = Button(_("_Update Stations"), Icons.VIEW_REFRESH)
        self._update_button.connect('clicked', self.__update)
        fb.insert(new_station, 1)
        fb.insert(self._update_button, 2)
        vb.pack_end(Align(fb, left=3), False, False, 3)
        pane.pack1(vb, resize=False, shrink=False)
        pane.show_all()

        songbox = Gtk.VBox(spacing=6)
        songbox.pack_start(self._searchbox, False, True, 0)
        self._songpane_container = Gtk.VBox()
        songbox.pack_start(self._songpane_container, True, True, 0)
        songbox.pack_start(self.qbar, False, True, 0)
        songbox.show_all()
        pane.pack2(songbox, resize=True, shrink=False)
        self.pack_start(pane, True, True, 0)
        self.show()

    @property
    def has_stations(self) -> bool:
        return bool(len(self.__stations or []) + len(self.__fav_stations or []))

    def pack(self, songpane):
        container = Gtk.VBox()
        container.add(self)
        self._songpane_container.add(songpane)
        return container

    def unpack(self, container, songpane):
        self._songpane_container.remove(songpane)
        container.remove(self)

    def __update(self, *args):
        self.qbar.hide()
        copool.add(download_taglist, self.station_list_url, self.__update_done,
                   cofuncid="radio-load", funcid="radio-load")

    def __update_done(self, stations):
        if not stations:
            print_w("Loading remote station list failed.")
            return

        # filter stations based on quality, listenercount
        def filter_stations(station):
            peak = station.get("~#listenerpeak", 0)
            if peak < 10:
                return False
            aac = "AAC" in station("~format")
            bitrate = station("~#bitrate", 50)
            if (aac and bitrate < 40) or (not aac and bitrate < 60):
                return False
            return True
        stations = filter(filter_stations, stations)

        # group them based on the title
        groups = {}
        for s in stations:
            key = s("~title~artist")
            groups.setdefault(key, []).append(s)

        # keep at most 2 URLs for each group
        stations = []
        for key, sub in groups.items():
            sub.sort(key=lambda s: s.get("~#listenerpeak", 0), reverse=True)
            stations.extend(sub[:2])

        # only keep the ones in at least one category
        all_ = [self.filters.query(k) for k in self.filters.keys()]
        assert all_
        anycat_filter = reduce(lambda x, y: x | y, all_)
        stations = list(filter(anycat_filter.search, stations))

        # remove listenerpeak
        for s in stations:
            s.pop("~#listenerpeak", None)

        # update the libraries
        stations = dict(((s.key, s) for s in stations))
        # don't add ones that are in the fav list
        for fav in self.__fav_stations.keys():
            stations.pop(fav, None)

        # separate
        o, n = set(self.__stations.keys()), set(stations)
        to_add, to_change, to_remove = n - o, o & n, o - n
        del o, n

        # migrate stats
        to_change = [stations.pop(k) for k in to_change]
        for new in to_change:
            old = self.__stations[new.key]
            # clear everything except stats
            AudioFile.reload(old)
            # add new metadata except stats
            for k in (x for x in new.keys() if x not in MIGRATE):
                old[k] = new[k]

        to_add = [stations.pop(k) for k in to_add]
        to_remove = [self.__stations[k] for k in to_remove]

        self.__stations.remove(to_remove)
        self.__stations.changed(to_change)
        self.__stations.add(to_add)

    def __filter_changed(self, bar, text, restore=False):
        self.__filter = Query(text, self.STAR)

        if not restore:
            self.activate()

    def __get_selected_libraries(self):
        """Returns the libraries to search in depending on the
        filter selection"""

        selection = self.view.get_selection()
        model, rows = selection.get_selected_rows()
        types = [model[row][self.TYPE] for row in rows]
        libs = [self.__fav_stations]
        if types != [self.TYPE_FAV]:
            libs.append(self.__stations)

        return libs

    def __get_selection_filter(self):
        """Returns a filter object for the current selection or None
        if nothing should be filtered"""

        selection = self.view.get_selection()
        model, rows = selection.get_selected_rows()

        filter_ = None
        for row in rows:
            type_ = model[row][self.TYPE]
            if type_ == self.TYPE_FILTER:
                key = model[row][self.KEY]
                current_filter = self.filters.query(key)
                if current_filter:
                    if filter_:
                        filter_ |= current_filter
                    else:
                        filter_ = current_filter
            elif type_ == self.TYPE_NOCAT:
                # if notcat is selected, combine all filters, negate and merge
                all_ = [self.filters.query(k) for k in self.filters.keys()]
                nocat_filter = all_ and -reduce(lambda x, y: x | y, all_)
                if nocat_filter:
                    if filter_:
                        filter_ |= nocat_filter
                    else:
                        filter_ = nocat_filter
            elif type_ == self.TYPE_ALL:
                filter_ = None
                break

        return filter_

    def unfilter(self):
        self.filter_text("")

    def __add_fav(self, songs):
        songs = [s for s in songs if s in self.__stations]
        type(self).__librarian.move(
            songs, self.__stations, self.__fav_stations)

    def __remove_fav(self, songs):
        songs = [s for s in songs if s in self.__fav_stations]
        type(self).__librarian.move(
            songs, self.__fav_stations, self.__stations)

    def __add(self, button):
        parent = qltk.get_top_parent(self)
        uri = (AddNewStation(parent).run(clipboard=True) or "").strip()
        if uri != "":
            self.__add_stations_from(uri)

    def __add_stations(self, irfs: Collection[IRFile], uri: str) -> None:
        print_d(f"Got {len(irfs)} station(s) from {uri}")
        assert self.__fav_stations is not None
        if not irfs:
            msg = ErrorMessage(
                self, _("No stations found"),
                _("No Internet radio stations were found at %s.") %
                util.escape(uri))
            msg.run()
            return

        fav_uris = {af("~uri") for af in self.__fav_stations}
        irfs = {af for af in irfs if af("~uri") not in fav_uris}
        if irfs:
            print_d(f"Adding {irfs} to favourites")
            self.__fav_stations.add(irfs)
        else:
            message = WarningMessage(
                self,
                _("Nothing to add"),
                _("All stations listed are already in your library."))
            message.run()

    def __add_stations_from(self, uri: str) -> None:
        def on_done(irfs: Iterable[IRFile], uri: str):
            GLib.idle_add(self.__add_stations, irfs, uri)
            print_d("Quitting thread")
        Thread(target=_get_stations_from, args=(uri, on_done)).start()

    def Menu(self, songs, library, items):
        in_fav = False
        in_all = False
        for song in songs:
            if song in self.__fav_stations:
                in_fav = True
            elif song in self.__stations:
                in_all = True
            if in_fav and in_all:
                break

        iradio_items = []
        button = MenuItem(_("Add to Favorites"), Icons.LIST_ADD)
        button.set_sensitive(in_all)
        connect_obj(button, 'activate', self.__add_fav, songs)
        iradio_items.append(button)
        button = MenuItem(_("Remove from Favorites"), Icons.LIST_REMOVE)
        button.set_sensitive(in_fav)
        connect_obj(button, 'activate', self.__remove_fav, songs)
        iradio_items.append(button)

        items.append(iradio_items)
        menu = SongsMenu(self.__librarian, songs, playlists=False, remove=True,
                         queue=False, items=items)
        return menu

    def restore(self):
        text = config.gettext("browsers", "query_text")
        self.__searchbar.set_text(text)
        if Query(text).is_parsable:
            self.__filter_changed(self.__searchbar, text, restore=True)

        keys = config.get("browsers", "radio").splitlines()

        def select_func(row):
            return row[self.TYPE] != self.TYPE_SEP and row[self.KEY] in keys

        self.__inhibit()
        view = self.view
        if not view.select_by_func(select_func):
            for row in view.get_model():
                if row[self.TYPE] == self.TYPE_FAV:
                    view.set_cursor(row.path)
                    break
        self.__uninhibit()

    def __get_filter(self):
        filter_ = self.__get_selection_filter()
        text_filter = self.__filter or Query("")

        if filter_:
            filter_ &= text_filter
        else:
            filter_ = text_filter

        return filter_

    def can_filter_text(self):
        return True

    def filter_text(self, text):
        self.__searchbar.set_text(text)
        if Query(text).is_parsable:
            self.__filter_changed(self.__searchbar, text)
            self.activate()

    def get_filter_text(self):
        return self.__searchbar.get_text()

    def activate(self):
        filter_ = self.__get_filter()
        libs = self.__get_selected_libraries()
        songs = filter_.filter(itertools.chain(*libs))
        self.songs_selected(songs)

    def active_filter(self, song):
        for lib in self.__get_selected_libraries():
            if song in lib:
                break
        else:
            return False

        filter_ = self.__get_filter()

        if filter_:
            return filter_.search(song)
        return True

    def save(self):
        text = self.__searchbar.get_text()
        config.settext("browsers", "query_text", text)

        selection = self.view.get_selection()
        model, rows = selection.get_selected_rows()
        names = filter(None, [model[row][self.KEY] for row in rows])
        config.set("browsers", "radio", "\n".join(names))

    def scroll(self, song):
        # nothing we care about
        if song not in self.__stations and song not in self.__fav_stations:
            return

        path = None
        for row in self.view.get_model():
            if row[self.TYPE] == self.TYPE_FILTER:
                if self.filters.query(row[self.KEY]).search(song):
                    path = row.path
                    break
        else:
            # in case nothing matches, select all
            path = (0,)

        self.view.set_cursor(path)
        self.view.scroll_to_cell(path, use_align=True, row_align=0.5)

    def status_text(self, count: int, time: Optional[str] = None) -> str:
        return numeric_phrase("%(count)d station", "%(count)d stations", count, 'count')


from quodlibet import app

if not app.player or app.player.can_play_uri("http://"):
    browsers = [InternetRadio]
else:
    browsers = []
