# Copyright 2005 Joe Wreschnig
#           2012 Christoph Reiter
#           2014 Jan Path
#      2011-2023 Nick Boultbee
#           2018 David Morris
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from collections.abc import Sequence

from gi.repository import Gtk, GLib, Gdk, GObject
from senf import uri2fsn

from quodlibet import app, print_w, print_d
from quodlibet import config
from quodlibet import const
from quodlibet import qltk
from quodlibet import util
from quodlibet import _

from quodlibet.query import Query
from quodlibet.pattern import Pattern
from quodlibet.qltk.information import Information
from quodlibet.qltk.properties import SongProperties
from quodlibet.qltk.views import AllTreeView, DragScroll
from quodlibet.qltk.ratingsmenu import ConfirmRateMultipleDialog
from quodlibet.qltk.songmodel import PlaylistModel
from quodlibet.qltk import Icons
from quodlibet.qltk.util import GSignals
from quodlibet.qltk.delete import trash_songs
from quodlibet.formats._audio import TAG_TO_SORT, AudioFile
from quodlibet.qltk.x import SeparatorMenuItem
from quodlibet.qltk.songlistcolumns import create_songlist_column, SongListColumn
from quodlibet.util import connect_destroy

DND_QL, DND_URI_LIST = range(2)


class SongSelectionInfo(GObject.Object):
    """
    Songs which get included in the status bar summary.

    The `changed` signal gets fired after any of the songs in the
    selection or the selection itself has changed.
    The signal is async.

    Two selection states:
        - 0 or 1 selected row: all rows
        - 2 or more: only the selected rows

    The signals fires if the state changes.

    FIXME: `row-changed` for song lists isn't implemented (performance).
            Since a library change could change the selection it should
            also trigger this.

            Since this would happen quite often (song stat changes) and
            would lead to a complete re-calc in the common case,
            ignore it for now.
    """

    __gsignals__: GSignals = {
        # changed(songs:list)
        "changed": (GObject.SignalFlags.RUN_LAST, None, (object,))
    }

    def __init__(self, songlist):
        super().__init__()

        self.__idle = None
        self.__songlist = songlist
        self.__selection = sel = songlist.get_selection()
        self.__count = sel.count_selected_rows()
        self.__sel_id = songlist.connect("selection-changed", self.__selection_changed)
        self.__sel_id = songlist.connect("songs-removed", self.__songs_removed)

    def destroy(self):
        self.__songlist.disconnect(self.__sel_id)
        if self.__idle:
            GLib.source_remove(self.__idle)

    def refresh(self):
        songlist = self.__songlist
        songs = songlist.get_selected_songs() or songlist.get_songs()
        self.emit("changed", songs)

    def _update_songs(self, songs):
        """After making changes (filling the list) call this to
        skip any queued changes and emit the passed songs instead"""
        self.__emit_info_selection(songs)
        self.__count = len(songs)

    def __idle_emit(self, songs):
        if songs is None:
            if self.__count <= 1:
                songs = self.__songlist.get_songs()
            else:
                songs = self.__songlist.get_selected_songs()
        self.emit("changed", songs)
        self.__idle = None

    def __emit_info_selection(self, songs=None):
        if self.__idle:
            GLib.source_remove(self.__idle)
        self.__idle = GLib.idle_add(self.__idle_emit, songs, priority=GLib.PRIORITY_LOW)

    def __songs_removed(self, songlist, removed):
        try:
            self.__emit_info_selection()
        except Exception as e:
            print_w(f"Couldn't process removed songs ({e})r")

    def __selection_changed(self, songlist, selection):
        count = selection.count_selected_rows()
        if self.__count == count == 0:
            return
        if count <= 1:
            if self.__count > 1:
                self.__emit_info_selection()
        else:
            self.__emit_info_selection()
        self.__count = count


def get_columns():
    """Gets the list of songlist column headings"""

    columns = config.getstringlist("settings", "columns", const.DEFAULT_COLUMNS)
    if "~current" in columns:
        columns.remove("~current")
    return columns


def set_columns(vals: list[str]) -> None:
    """Persists the settings for songlist headings held in `vals`"""

    config.setstringlist("settings", "columns", vals)


def get_sort_tag(tag):
    """Returns a tag that can be used for sorting for the given column tag.

    Returns '' if the default sort key should be used.
    """

    replace_order = {
        "~#track": "",
        "~#disc": "",
        "~#tracks": "",
        "~#discs": "",
        "~length": "~#length",
    }

    if tag == "~title~version":
        tag = "title"
    elif tag == "~album~discsubtitle":
        tag = "album"

    if "<" in tag:
        for key, value in replace_order.items():
            if value:
                value = f"<{value}>"
            tag = tag.replace(f"<{key}>", value)
        for key, value in TAG_TO_SORT.items():
            tag = tag.replace(f"<{key}>", f"<{value}|<{value}>|<{key}>>")
        tag = Pattern(tag).format
    else:
        tags = util.tagsplit(tag)
        sort_tags = []
        for tag in tags:
            tag = replace_order.get(tag, tag)
            tag = TAG_TO_SORT.get(tag, tag)
            if tag not in sort_tags:
                sort_tags.append(tag)
        if len(sort_tags) > 1:
            tag = "~" + "~".join(sort_tags)

    return tag


def header_tag_split(header):
    """Split a pattern or a tied tag into separate tags"""

    if "<" in header:
        try:
            return list(Pattern(header).tags)
        except ValueError:
            return []
    else:
        return util.tagsplit(header)


class SongListDnDMixin(GObject.GObject):
    """DnD support for the SongList class"""

    def setup_drop(self, library):
        self.connect("drag-begin", self.__drag_begin)
        self.connect("drag-motion", self.__drag_motion)
        self.connect("drag-leave", self.__drag_leave)
        self.connect("drag-data-get", self.__drag_data_get)
        self.connect("drag-data-received", self.__drag_data_received, library)

    def __drag_begin(self, *args):
        ok, state = Gtk.get_current_event_state()
        if ok and state & qltk.get_primary_accel_mod():
            self.__force_copy = True
        else:
            self.__force_copy = False

    def enable_drop(self, by_row=True):
        targets = [
            ("text/x-quodlibet-songs", Gtk.TargetFlags.SAME_APP, DND_QL),
            ("text/uri-list", 0, DND_URI_LIST),
        ]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]
        self.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE,
        )
        self.drag_dest_set(
            Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        )
        self.__drop_by_row = by_row
        self.__force_copy = False

    def disable_drop(self):
        targets = [
            ("text/x-quodlibet-songs", Gtk.TargetFlags.SAME_APP, DND_QL),
            ("text/uri-list", 0, DND_URI_LIST),
        ]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]
        self.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK, targets, Gdk.DragAction.COPY
        )
        self.drag_dest_unset()

    def __drag_motion(self, view, ctx, x, y, time):
        if self.__drop_by_row:
            self.set_drag_dest(x, y)
            self.scroll_motion(x, y)
            if Gtk.drag_get_source_widget(ctx) == self and not self.__force_copy:
                kind = Gdk.DragAction.MOVE
            else:
                kind = Gdk.DragAction.COPY
            Gdk.drag_status(ctx, kind, time)
            return True
        else:
            self.get_parent().drag_highlight()
            Gdk.drag_status(ctx, Gdk.DragAction.COPY, time)
            return True

    def __drag_leave(self, widget, ctx, time):
        widget.get_parent().drag_unhighlight()
        self.scroll_disable()

    def __drag_data_get(self, view, ctx, sel, tid, etime):
        model, paths = self.get_selection().get_selected_rows()
        if tid == DND_QL:
            songs = [model[path][0] for path in paths if model[path][0].can_add]
            if len(songs) != len(paths):
                qltk.ErrorMessage(
                    qltk.get_top_parent(self),
                    _("Unable to copy songs"),
                    _(
                        "The files selected cannot be copied to other "
                        "song lists or the queue."
                    ),
                ).run()
                Gdk.drag_abort(ctx, etime)
                return

            qltk.selection_set_songs(sel, songs)

            # DEM 2018/05/25: The below check is a deliberate repetition of
            # code in the drag-motion signal handler.  In MacOS/Quartz, the
            # context action is not propogated between event handlers for
            # drag-motion and drag-data-get using "ctx.get_actions()".  It is
            # unclear if this is a bug or expected behavior.  Regardless, the
            # context widget information is the same so identical behavior can
            # be achieved by simply using the same widget check as in the move
            # action.
            if Gtk.drag_get_source_widget(ctx) == self and not self.__force_copy:
                self.__drag_iters = list(map(model.get_iter, paths))
            else:
                self.__drag_iters = []
        else:
            uris = [model[path][0]("~uri") for path in paths]
            sel.set_uris(uris)
            self.__drag_iters = []

    def __drag_data_received(self, view, ctx, x, y, sel, info, etime, library):
        model = view.get_model()
        if info == DND_QL:
            filenames = qltk.selection_get_filenames(sel)
            move = bool(ctx.get_selected_action() & Gdk.DragAction.MOVE)
        elif info == DND_URI_LIST:

            def to_filename(s):
                try:
                    return uri2fsn(s)
                except ValueError:
                    return None

            filenames = list(filter(None, map(to_filename, sel.get_uris())))
            move = False
        else:
            Gtk.drag_finish(ctx, False, False, etime)
            return
        # Should always have one here, but you never know (also: types)
        librarian = library.librarian or library
        to_add = []
        for filename in filenames:
            if filename not in librarian:
                library.add_filename(filename)
            elif filename not in library:
                to_add.append(librarian[filename])
        library.add(to_add)
        songs: list = list(filter(None, map(library.get, filenames)))
        if not songs:
            Gtk.drag_finish(ctx, bool(not filenames), False, etime)
            return

        if not self.__drop_by_row:
            success = self.__drag_data_browser_dropped(songs)
            Gtk.drag_finish(ctx, success, False, etime)
            return

        try:
            path, position = view.get_dest_row_at_pos(x, y)
        except TypeError:
            path = max(0, len(model) - 1)
            position = Gtk.TreeViewDropPosition.AFTER

        if move and Gtk.drag_get_source_widget(ctx) == view:
            iter = model.get_iter(path)  # model can't be empty, we're moving
            if position in (
                Gtk.TreeViewDropPosition.BEFORE,
                Gtk.TreeViewDropPosition.INTO_OR_BEFORE,
            ):
                while self.__drag_iters:
                    model.move_before(self.__drag_iters.pop(0), iter)
            else:
                while self.__drag_iters:
                    model.move_after(self.__drag_iters.pop(), iter)
            Gtk.drag_finish(ctx, True, False, etime)
        else:
            song = songs.pop(0)
            try:
                iter = model.get_iter(path)
            except ValueError:
                iter = model.append(row=[song])  # empty model
            else:
                if position in (
                    Gtk.TreeViewDropPosition.BEFORE,
                    Gtk.TreeViewDropPosition.INTO_OR_BEFORE,
                ):
                    iter = model.insert_before(iter, [song])
                else:
                    iter = model.insert_after(iter, [song])
            for song in songs:
                iter = model.insert_after(iter, [song])
            Gtk.drag_finish(ctx, True, move, etime)

    def __drag_data_browser_dropped(self, songs):
        window = qltk.get_top_parent(self)
        return window.browser.dropped(songs)


class SongList(AllTreeView, SongListDnDMixin, DragScroll, util.InstanceTracker):
    """A TreeView containing a list of songs."""

    __gsignals__: GSignals = {
        "songs-removed": (GObject.SignalFlags.RUN_LAST, None, (object,)),
        "orders-changed": (GObject.SignalFlags.RUN_LAST, None, []),
    }

    headers: list[str] = []
    """The list of current headers."""

    star = list(Query.STAR)

    def menu(self, header: str, browser, library):
        songs = self.get_selected_songs()
        if not songs:
            return

        def Filter(t):
            # Translators: The substituted string is the name of the
            # selected column (a translated tag name).
            b = qltk.MenuItem(_("_Filter on %s") % util.tag(t, True), Icons.EDIT_FIND)
            b.connect("activate", self.__filter_on, t, songs, browser)
            return b

        header = header_tag_split(header)[0]
        can_filter = browser.can_filter
        menu_items = []
        if header not in ["artist", "album"] and can_filter(header):
            menu_items.append(Filter(header))
        if can_filter("artist"):
            menu_items.append(Filter("artist"))
        if can_filter("album"):
            menu_items.append(Filter("album"))

        menu = browser.menu(songs, library, items=[menu_items])
        menu.show_all()
        return menu

    def __init__(
        self,
        library,
        player=None,
        update=False,
        model_cls=PlaylistModel,
        sortable: bool = True,
    ):
        super().__init__()
        self.sortable = sortable
        self._register_instance(SongList)
        self.set_model(model_cls())
        self.info = SongSelectionInfo(self)
        self.set_size_request(200, 150)
        self.set_rules_hint(True)
        self.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.set_fixed_height_mode(True)
        self.__csig = self.connect("columns-changed", self.__columns_changed)
        self._first_column = None
        # A priority list of how to apply the sort keys.
        # might contain column header names not present...
        self._sort_sequence: list[str] = []
        self.set_column_headers(self.headers)
        librarian = library.librarian or library

        connect_destroy(librarian, "changed", self.__song_updated)
        connect_destroy(librarian, "removed", self.__song_removed, player)

        if update:
            connect_destroy(librarian, "added", self.__song_added)

        if player:
            connect_destroy(player, "paused", lambda *x: self.__redraw_current())
            connect_destroy(player, "unpaused", lambda *x: self.__redraw_current())
            connect_destroy(player, "error", lambda *x: self.__redraw_current())

        self.connect("button-press-event", self.__button_press, library)
        self.connect("key-press-event", self.__key_press, library, player)

        self.setup_drop(library)
        self.disable_drop()

        self.set_search_equal_func(self.__search_func, None)

        self.connect("destroy", self.__destroy)

    @property
    def sortable(self) -> bool:
        """Whether the columns clicked to enable sorting of songs"""
        return self._sortable

    @sortable.setter
    def sortable(self, value: bool):
        # It's either sortable or clickable columns, both ends in a buggy UI (see #4099)
        always_sortable = config.getboolean("song_list", "always_allow_sorting")
        self._sortable = value or always_sortable
        for col in self.get_columns():
            try:
                col.get_widget().set_sensitive(self._sortable)
            except (TypeError, AttributeError):
                # Just in case columns don't always work like this
                pass

    @property
    def model(self) -> Gtk.TreeModel:
        return self.get_model()

    @property
    def sourced(self):
        return self.model.sourced

    def toggle_column_sort(self, column, replace=True, refresh=True):
        """Toggles the sort order of a column.

        If not sorted, defaults to Gtk.SortType.ASCENDING

        If replace is False, the column will be appended to existing
        sorted columns. If it replaces a sort sequence where it was part of
        before it will not toggle itself, only remove the other ones.

        If refresh is True, the song list will be resorted.
        """

        if not self.sortable:
            return

        # update the sort priority list
        if replace:
            del self._sort_sequence[:]
        else:
            try:
                self._sort_sequence.remove(column.header_name)
            except ValueError:
                pass
        self._sort_sequence.append(column.header_name)

        # in case we replace a multi sort with one sort that was part before
        # don't toggle, because it usually means we want to get rid of
        # the other one
        dont_reverse = False
        if replace and column.get_sort_indicator():
            for c in self.get_columns():
                if c is not column and c.get_sort_indicator():
                    dont_reverse = True
                    break

        # set the indicators
        default_order = Gtk.SortType.ASCENDING
        for c in self.get_columns():
            if c is column:
                if c.get_sort_indicator():
                    if dont_reverse:
                        order = c.get_sort_order()
                    else:
                        order = not c.get_sort_order()
                else:
                    order = default_order
                c.set_sort_order(order)
                c.set_sort_indicator(True)
                if not replace:
                    break
            elif replace:
                c.set_sort_indicator(False)

        if refresh:
            songs = self.get_songs()
            song_order = self._get_song_order(songs)
            self.model.reorder(song_order)

            selection = self.get_selection()
            _, paths = selection.get_selected_rows()
            if paths:
                self.scroll_to_cell(paths[0], use_align=True, row_align=0.5)

        self.emit("orders-changed")

    def find_default_sort_column(self):
        """Returns a column that will sort using only the default sort key
        or None if none can't be found
        """

        for c in self.get_columns():
            # get_sort_tag == "" if the default sort key should be used
            if not get_sort_tag(c.header_name):
                return c

    def is_sorted(self):
        """If any of the columns has a sort indicator.

        This does not mean that the list content is sorted.
        """

        for c in self.get_columns():
            if c.get_sort_indicator():
                return True
        return False

    def clear_sort(self):
        """Remove all column sort indicators"""

        for h in self.get_columns():
            h.set_sort_indicator(False)
        del self._sort_sequence[:]

        self.emit("orders-changed")

    def get_sort_orders(self):
        """Returns a list of tuples (header_name, descending)"""

        sorted_ = [c for c in self.get_columns() if c.get_sort_indicator()]

        # if someone adds columns and sorts them using the TV API directly..
        # better not crash I guess
        for c in sorted_:
            if c.header_name not in self._sort_sequence:
                self._sort_sequence.append(c.header_name)

        sorted_.sort(key=lambda c: self._sort_sequence.index(c.header_name))
        return [(c.header_name, bool(c.get_sort_order())) for c in sorted_]

    def set_sort_orders(self, orders):
        """Pass a value returned by get_sort_orders() to restore the state"""

        if not self.sortable:
            return

        self._sort_sequence = [tag for tag, o in orders]

        orders = dict(orders)
        for c in self.get_columns():
            if c.header_name in orders:
                c.set_sort_indicator(True)
                c.set_sort_order(orders[c.header_name])
            else:
                c.set_sort_indicator(False)

        self.emit("orders-changed")

    def __destroy(self, *args):
        self.info.destroy()
        self.info = None
        self.handler_block(self.__csig)
        for column in self.get_columns():
            self.remove_column(column)
        self.handler_unblock(self.__csig)

    def __search_func(self, model, column, key, iter, *args):
        for column in self.get_columns():
            value = model.get_value(iter)(column.header_name)
            if not isinstance(value, str):
                continue
            elif key in value.lower() or key in value:
                return False
        else:
            return True

    def __filter_on(self, widget, header, songs, browser):
        if not browser:
            return

        # Fall back to the playing song
        if songs is None:
            if app.player.song:
                songs = [app.player.song]
            else:
                return

        browser.filter_on(songs, header)

    def __button_press(self, view, event, librarian):
        if event.button != Gdk.BUTTON_PRIMARY:
            return
        x, y = map(int, [event.x, event.y])
        try:
            path, col, cellx, celly = view.get_path_at_pos(x, y)
        except TypeError:
            return True
        if event.window != self.get_bin_window():
            return False
        if col.header_name == "~rating":
            if not config.getboolean("browsers", "rating_click"):
                return

            song = view.get_model()[path][0]
            l = Gtk.Label()
            l.show()
            l.set_text(config.RATINGS.full_symbol)
            width = l.get_preferred_size()[1].width
            l.destroy()
            if not width:
                return False
            precision = config.RATINGS.precision
            count = int(float(cellx - 5) / width) + 1
            rating = max(0.0, min(1.0, count * precision))
            if rating <= precision and song("~#rating") == precision:
                rating = 0.0
            self.__set_rating(rating, [song], librarian)

    def __set_rating(self, value, songs, librarian):
        count = len(songs)
        if count > 1 and config.getboolean("browsers", "rating_confirm_multiple"):
            dialog = ConfirmRateMultipleDialog(self, count, value)
            if dialog.run() != Gtk.ResponseType.YES:
                return
        for song in songs:
            song["~#rating"] = value
        librarian.changed(songs)

    def __key_press(self, songlist, event, librarian, player):
        if qltk.is_accel(event, "<Primary>Return", "<Primary>KP_Enter"):
            self.__enqueue(self.get_selected_songs())
            return True
        elif qltk.is_accel(event, "<Primary>F"):
            self.emit("start-interactive-search")
            return True
        elif qltk.is_accel(event, "<Primary>Delete"):
            songs = self.get_selected_songs()
            if songs:
                trash_songs(self, songs, librarian)
            return True
        elif qltk.is_accel(event, "<alt>Return"):
            songs = self.get_selected_songs()
            if songs:
                window = SongProperties(librarian, songs, parent=self)
                window.show()
            return True
        elif qltk.is_accel(event, "<Primary>I"):
            songs = self.get_selected_songs()
            if songs:
                window = Information(librarian, songs, self)
                window.show()
            return True
        elif qltk.is_accel(event, "space", "KP_Space") and player is not None:
            player.paused = not player.paused
            return True
        elif qltk.is_accel(event, "F2"):
            songs = self.get_selected_songs()
            if len(songs) > 1:
                print_d("Can't edit more than one")
            elif songs:
                path, col = songlist.get_cursor()
                song = self.get_first_selected_song()
                cls = type(col).__name__
                if col.can_edit:
                    print_d(f"Let's edit this: {song} ({cls} can be edited)")
                    renderers = col.get_cells()
                    renderers[0].props.editable = True
                    self.set_cursor(path, col, start_editing=True)
                else:
                    print_d(f"Can't edit {cls}. Maybe it's synthetic / numeric?")

        return False

    def __enqueue(self, songs):
        songs = [s for s in songs if s.can_add]
        if songs:
            from quodlibet import app

            app.window.playlist.enqueue(songs)

    def __redraw_current(self):
        model = self.get_model()
        iter_ = model.current_iter
        if iter_:
            path = model.get_path(iter_)
            model.row_changed(path, iter_)

    def __columns_changed(self, *args):
        headers = [h.header_name for h in self.get_columns()]
        SongList.set_all_column_headers(headers)
        SongList.headers = headers

    def __column_width_changed(self, *args):
        # make sure non-resizable columns stay non-expanding.
        # gtk likes to change them sometimes
        for c in self.get_columns():
            if not c.get_resizable() and c.get_expand():
                c.set_expand(False)

        widths = []
        expands = []
        for c in self.get_columns():
            if not c.get_resizable():
                continue
            widths.extend((c.header_name, str(c.get_fixed_width())))
            expands.extend((c.header_name, str(int(c.get_expand()))))
        config.setstringlist("memory", "column_widths", widths)
        config.setstringlist("memory", "column_expands", expands)

    @classmethod
    def set_all_column_headers(cls, headers: list[str]):
        set_columns(headers)
        try:
            headers.remove("~current")
        except ValueError:
            pass
        cls.headers = headers
        for listview in cls.instances():
            listview.set_column_headers(headers)

        star = list(Query.STAR)
        for header in headers:
            for tag in header_tag_split(header):
                if not tag.startswith("~#") and tag not in star:
                    star.append(tag)

        for tag in config.getlist("settings", "search_tags"):
            if tag and tag not in star:
                star.append(tag)

        SongList.star = star

    def set_model(self, model):
        super().set_model(model)
        self.set_search_column(0)

    def clear(self):
        """Remove all songs"""

        model = self.get_model()
        if model:
            model.clear()

    def get_songs(self):
        """Get all songs currently in the song list"""

        model = self.get_model()
        if not model:
            return []
        return model.get()

    def _get_song_order(self, songs: list[AudioFile]) -> Sequence[int] | None:
        """Returns mapping from new position to position in given list of songs
        when sorted based on the column sort orders"""

        orders = self.get_sort_orders()
        if orders:
            song_order = list(range(len(songs)))
            for key, reverse in self.__get_song_sort_key_func(orders):
                song_order.sort(key=lambda i: key(songs[i]), reverse=reverse)
            return song_order
        else:
            return None

    def __get_song_sort_key_func(self, order):
        last_tag = None
        last_order = None
        first = True
        key_func = []
        for tag, reverse in order:
            tag = get_sort_tag(tag)

            # always sort using the default sort key first
            if first:
                first = False
                key_func.append((lambda s: s.sort_key, reverse))
                last_order = reverse
                last_tag = ""

            # no need to sort twice in a row with the same key/order
            if tag == last_tag and last_order == reverse:
                continue
            last_order = reverse
            last_tag = tag

            if tag == "":
                key_func.append((lambda s: s.sort_key, reverse))
            else:
                sort_func = AudioFile.sort_by_func(tag)
                key_func.append((sort_func, reverse))
        return key_func

    def add_songs(self, songs):
        """Add songs to the list in the right order and position"""

        if not songs:
            return

        model = self.get_model()
        if not len(model):
            self.set_songs(songs, scroll=False)
            return

        if not self.is_sorted():
            model.append_many(songs)
            return

        for song in songs:
            insert_iter = self.__find_song_position(song)
            model.insert_before(insert_iter, row=[song])

    def set_songs(
        self,
        songs: list[AudioFile],
        sorted: bool = False,
        scroll: bool = True,
        scroll_select: bool = False,
    ):
        """Fill the song list.

        If sorted is True, the passed songs will not be sorted and
        all sort indicators will be removed.

        If scroll is True the list will scroll to the current song.

        If scroll_select is True restore the selection of the first
        selected song and scroll to. Falls back to the current song.
        """

        model = self.get_model()
        assert model is not None

        song_order = None

        if not sorted:
            # make sure some sorting is set and visible
            if not self.is_sorted():
                default = self.find_default_sort_column()
                if default:
                    self.toggle_column_sort(default, refresh=False)
            song_order = self._get_song_order(songs)
        else:
            self.clear_sort()

        restore_song = None
        if scroll_select:
            restore_song = self.get_first_selected_song()

        with self.without_model() as model:
            model.set(songs)
            if song_order:
                model.reorder(song_order)

        # scroll to the first selected or current song and restore
        # selection for the first selected item if there was one
        if scroll or scroll_select:
            if restore_song is not None and restore_song is not model.current:
                try:
                    index = songs.index(restore_song)
                except ValueError:
                    path = model.current_path
                else:
                    path = Gtk.TreePath.new()
                    path.append_index(index)
            else:
                path = model.current_path

            if path is not None:
                if restore_song is not None:
                    self.set_cursor(path)
                self.scroll_to_cell(path, use_align=True, row_align=0.5)

        # the song selection has queued a change now, cancel that and
        # pass the songs manually
        self.info._update_songs(songs)

    def jump_to_song(self, song, select=False):
        """Scrolls to and selects the given song if in the list.

        Returns True if the song was found.
        """

        model = self.get_model()
        if not model:
            return False

        # fast path
        if song == model.current:
            path = model.current_path
        else:
            iter_ = model.find(song)
            if iter_ is None:
                return False
            path = model.get_path(iter_)

        if select:
            self.set_cursor(path)
        self.scroll_to_cell(path, use_align=True, row_align=0.5)

        return True

    def get_first_selected_song(self):
        """The first selected song in the list or None"""

        selection = self.get_selection()
        model, paths = selection.get_selected_rows()
        if paths:
            return model.get_value(model.get_iter(paths[0]))

    def get_selected_songs(self):
        """Returns a list of selected songs"""

        songs = []

        def func(model, path, iter_, user_data):
            songs.append(model.get_value(iter_))

        selection = self.get_selection()
        selection.selected_foreach(func, None)
        return songs

    def __find_song_position(self, song):
        """Finds the appropriate position of a song in a sorted song list.

        Returns iter of the song after the given song according to the current
        sort order.

        Returns None if the correct position is at the end of the song list.
        """

        model = self.get_model()
        order = self.get_sort_orders()
        sort_key_func = list(enumerate(reversed(self.__get_song_sort_key_func(order))))
        song_sort_keys = [key(song) for i, (key, r) in sort_key_func]
        i = 0
        j = len(model)
        while i < j:
            mid = (i + j) // 2
            other_song_iter = model.iter_nth_child(None, mid)
            other_song = model.get_value(other_song_iter)
            song_is_lower = False
            for i, (key, reverse) in sort_key_func:
                other_key = key(other_song)
                is_lower = song_sort_keys[i] < other_key
                is_greater = song_sort_keys[i] > other_key
                if not reverse and is_lower or reverse and is_greater:
                    song_is_lower = True
                    break
                if not reverse and is_greater or reverse and is_lower:
                    break
            if song_is_lower:
                j = mid
            else:
                i = mid + 1
        if i < len(model):
            return model.iter_nth_child(None, i)
        return None

    def __find_iters_in_selection(self, songs) -> tuple[list, list, bool]:
        model, rows = self.get_selection().get_selected_rows()
        rows = rows or []
        iters = []
        removed_songs = []
        for r in rows:
            if model[r][0] in songs:
                iters.append(model[r].iter)
                removed_songs.append(model[r][0])
        complete = len(iters) == len(songs)
        return iters, removed_songs, complete

    def __song_updated(self, librarian, songs):
        """Only update rows that are currently displayed.
        Warning: This makes the row-changed signal useless.
        """
        model = self.get_model()
        if config.getboolean("song_list", "auto_sort") and self.is_sorted():
            iters, _, complete = self.__find_iters_in_selection(songs)

            if not complete:
                iters = model.find_all(songs)

            rows = [Gtk.TreeRowReference.new(model, model.get_path(i)) for i in iters]

            for row in rows:
                iter = model.get_iter(row.get_path())
                song = model.get_value(iter)
                insert_iter = self.__find_song_position(song)
                model.move_before(iter, insert_iter)

        vrange = self.get_visible_range()
        if vrange is None:
            return
        (start,), (end,) = vrange
        for path in range(start, end + 1):
            row = model[path]
            if row[0] in songs:
                model.row_changed(row.path, row.iter)

    def __song_added(self, librarian, songs):
        window = qltk.get_top_parent(self)
        filter_ = window.browser.active_filter
        if callable(filter_):
            self.add_songs(list(filter(filter_, songs)))

    def __song_removed(self, librarian, songs, player):
        try:
            # The player needs to be called first so it can ge the next song
            # in case the current one gets deleted and the order gets reset.
            if player:
                for song in songs:
                    player.remove(song)

            model = self.get_model()

            # The selected songs are removed from the library and should
            # be removed from the view.

            if not len(model):
                return

            songs = set(songs)

            # search in the selection first
            # speeds up common case: select songs and remove them
            iters, removed_songs, complete = self.__find_iters_in_selection(songs)

            # if not all songs were in the selection, search the whole view
            if not complete:
                removed_songs = []
                for iter_, value in self.model.iterrows():
                    if value in songs:
                        iters.append(iter_)
                        removed_songs.append(value)

            if removed_songs:
                self.emit("songs-removed", set(removed_songs))
            self.remove_iters(iters)
        except Exception as e:
            print_w(f"Couldn't process removed songs: {e}", self)

    def set_first_column_type(self, column_type):
        """Set a column that will be included at the beginning"""

        self._first_column = column_type

        # refresh
        self.set_column_headers(self.headers)

    # Build a new filter around our list model, set the headers to their
    # new values.
    def set_column_headers(self, headers):
        if len(headers) == 0:
            return

        self.handler_block(self.__csig)

        old_sort = self.get_sort_orders()
        for column in self.get_columns():
            self.remove_column(column)

        if self._first_column:
            column = self._first_column()
            self.append_column(column)

        cws = config.getstringlist("memory", "column_widths")
        column_widths = {}
        for i in range(0, len(cws), 2):
            column_widths[cws[i]] = int(cws[i + 1])

        ce = config.getstringlist("memory", "column_expands")
        column_expands = {}
        for i in range(0, len(ce), 2):
            column_expands[ce[i]] = int(ce[i + 1])

        for t in headers:
            column = create_songlist_column(self.model, t)
            if column.get_resizable():
                if t in column_widths:
                    column.set_fixed_width(column_widths[t])
                if t in column_expands:
                    column.set_expand(column_expands[t])
                else:
                    column.set_expand(True)

            def column_clicked(column, *args):
                if not self._sortable:
                    return
                # if ctrl is held during the sort click, append a sort key
                # or change order if already sorted
                ctrl_held = False
                event = Gtk.get_current_event()
                if event:
                    ok, state = event.get_state()
                    if ok and state & qltk.get_primary_accel_mod():
                        ctrl_held = True

                self.toggle_column_sort(column, replace=not ctrl_held)

            column.connect("clicked", column_clicked)
            column.connect("button-press-event", self.__showmenu)
            column.connect("popup-menu", self.__showmenu)
            column.connect("notify::width", self.__column_width_changed)
            column.set_reorderable(True)
            self.append_column(column)

        self.set_sort_orders(old_sort)
        self.columns_autosize()

        self.handler_unblock(self.__csig)

    def _menu(self, column: SongListColumn) -> Gtk.Menu:
        menu = Gtk.Menu()

        def selection_done_cb(menu):
            menu.destroy()

        menu.connect("selection-done", selection_done_cb)

        current_set = set(SongList.headers)

        def tag_title(tag: str):
            if "<" in tag:
                return util.pattern(tag)
            return util.tag(tag)

        current = [(tag_title(c), c) for c in SongList.headers]

        def add_header_toggle(
            menu: Gtk.Menu,
            header: str,
            tag: str,
            active: bool,
            column: SongListColumn = column,
        ):
            item = Gtk.CheckMenuItem(label=header)
            item.tag = tag
            item.set_active(active)
            item.connect("activate", self.__toggle_header_item, column)
            item.show()
            item.set_tooltip_text(tag)
            menu.append(item)

        for header, tag in current:
            add_header_toggle(menu, header, tag, True)

        sep = SeparatorMenuItem()
        sep.show()
        menu.append(sep)

        trackinfo = """title genre comment ~title~version ~#track
            ~#playcount ~#skipcount ~rating ~#length ~playlists
            bpm initialkey""".split()
        peopleinfo = """artist ~people performer arranger author composer
            conductor lyricist originalartist""".split()
        albuminfo = """album ~album~discsubtitle labelid ~#disc ~#discs
            ~#tracks albumartist""".split()
        dateinfo = """date originaldate recordingdate ~year ~originalyear
            ~#laststarted ~#lastplayed ~#added ~#mtime""".split()
        fileinfo = """~format ~#bitdepth ~#bitrate ~#filesize
            ~filename ~basename ~dirname ~uri ~codec ~encoding ~#channels
            ~#samplerate""".split()
        copyinfo = """copyright organization location isrc
            contact website""".split()
        all_headers: list[str] = sum(
            [trackinfo, peopleinfo, albuminfo, dateinfo, fileinfo, copyinfo], []
        )

        for name, group in [
            (_("All _Headers"), all_headers),
            (_("_Track Headers"), trackinfo),
            (_("_Album Headers"), albuminfo),
            (_("_People Headers"), peopleinfo),
            (_("_Date Headers"), dateinfo),
            (_("_File Headers"), fileinfo),
            (_("_Production Headers"), copyinfo),
        ]:
            item = Gtk.MenuItem(label=name, use_underline=True)
            item.show()
            menu.append(item)
            submenu = Gtk.Menu()
            item.set_submenu(submenu)
            for header, tag in sorted(zip(map(util.tag, group), group)):  # noqa
                add_header_toggle(submenu, header, tag, tag in current_set)

        sep = SeparatorMenuItem()
        sep.show()
        menu.append(sep)

        custom = Gtk.MenuItem(label=_("_Customize Headers…"), use_underline=True)
        custom.show()
        custom.connect("activate", self.__add_custom_column)
        menu.append(custom)

        item = Gtk.CheckMenuItem(label=_("_Expand Column"), use_underline=True)
        item.set_active(column.get_expand())
        item.set_sensitive(column.get_resizable())

        def set_expand_cb(item, column):
            do_expand = item.get_active()
            if not do_expand:
                # in case we unexpand, get the current width and set it
                # so the column doesn't give up all its space
                # to the left over expanded columns
                column.set_fixed_width(column.get_width())
            else:
                # in case we expand this seems to trigger a re-distribution
                # between all expanded columns
                column.set_fixed_width(-1)
            column.set_expand(do_expand)
            self.columns_autosize()

        sep = SeparatorMenuItem()
        sep.show()
        menu.append(sep)

        item.connect("activate", set_expand_cb, column)
        item.show()
        menu.append(item)

        return menu

    def __toggle_header_item(self, item, column):
        headers = SongList.headers[:]
        if item.get_active():
            try:
                headers.insert(self.get_columns().index(column), item.tag)
            except ValueError:
                headers.append(item.tag)
        else:
            try:
                headers.remove(item.tag)
            except ValueError:
                pass

        SongList.set_all_column_headers(headers)
        SongList.headers = headers

    def __add_custom_column(self, item):
        # Prefs has to import SongList, so do this here to avoid
        # a circular import.
        from quodlibet.qltk.prefs import PreferencesWindow

        window = PreferencesWindow(self)
        window.show()
        window.set_page("songlist")

    def __showmenu(self, column, event=None):
        time = event.time if event else Gtk.get_current_event_time()

        if event is not None and not event.triggers_context_menu():
            return False

        menu = self._menu(column)
        menu.attach_to_widget(self, None)

        if event:
            menu.popup(None, None, None, None, event.button, time)
            return True

        widget = column.get_widget()
        qltk.popup_menu_under_widget(menu, widget, 3, time)
        return True


@config.register_upgrade_function
def _migrate_rating_column(config, old, new):
    if old < 0:
        # https://github.com/quodlibet/quodlibet/issues/1381
        columns = get_columns()[:]
        for i, c in enumerate(columns):
            if c == "~#rating":
                columns[i] = "~rating"
        set_columns(columns)
