# Copyright 2010-2011 Christoph Reiter, Steven Robertson
#           2016-2018 Nick Boultbee
#           2018-2019 Peter Strulo
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import operator
import os
from functools import reduce

from gi.repository import Gtk, GObject, GLib

import quodlibet
from quodlibet import config
from quodlibet import _

from quodlibet.query import Query
from quodlibet.qltk.cbes import ComboBoxEntrySave
from quodlibet.qltk.ccb import ConfigCheckMenuItem
from quodlibet.qltk.x import SeparatorMenuItem
from quodlibet.qltk import is_accel
from quodlibet.util import limit_songs, DeferredSignal


class SearchBarBox(Gtk.Box):
    """
        A search bar widget for inputting queries.

        signals:
            query-changed - a parsable query string
            focus-out - If the widget gets focused while being focused
                (usually for focusing the songlist)
    """

    __gsignals__ = {
        'query-changed': (
            GObject.SignalFlags.RUN_LAST, None, (object,)),
        'focus-out': (GObject.SignalFlags.RUN_LAST, None, ()),
        }

    DEFAULT_TIMEOUT = 400

    def __init__(self, filename=None, completion=None, accel_group=None,
                 timeout=DEFAULT_TIMEOUT, validator=Query.validator,
                 star=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        if filename is None:
            filename = os.path.join(
                quodlibet.get_data_dir(), "lists", "queries")

        historic_entries_count = config.getint("browsers", "searchbar_historic_entries")

        combo = ComboBoxEntrySave(
            filename, count=historic_entries_count, validator=validator,
            title=_("Saved Searches"), edit_title=_(u"Edit saved searches…"))

        self.__deferred_changed = DeferredSignal(
            self._filter_changed, timeout=timeout, owner=self)

        self.__combo = combo
        entry = combo.get_child()
        self._entry = entry
        if completion:
            entry.set_completion(completion)

        self._star = star
        self._query = None
        self.__sig = combo.connect('text-changed', self.__text_changed)

        entry.connect('clear', self._filter_changed)
        entry.connect('backspace', self.__text_changed)
        entry.connect('populate-popup', self.__menu)
        entry.connect('activate', self._filter_changed)
        entry.connect('activate', self.__save_search)
        entry.connect('focus-out-event', self.__save_search)
        entry.connect('key-press-event', self.__key_pressed)

        entry.set_placeholder_text(_("Search"))
        entry.set_tooltip_text(_("Search your library, "
                                 "using free text or QL queries"))

        combo.enable_clear_button()
        self.pack_start(combo, True, True, 0)

        if accel_group:
            key, mod = Gtk.accelerator_parse("<Primary>L")
            accel_group.connect(key, mod, 0,
                    lambda *x: entry.mnemonic_activate(True))

        for child in self.get_children():
            child.show_all()

    def set_enabled(self, enabled=True):
        self._entry.set_sensitive(enabled)

    def set_text(self, text):
        """Set the text without firing any signals"""

        self.__deferred_changed.abort()
        self._update_query_from(text)

        # deactivate all signals and change the entry text
        self.__inhibit()
        self._entry.set_text(text)
        self.__uninhibit()

    def _update_query_from(self, text):
        # TODO: remove tight coupling to Query
        self._query = Query(text, star=self._star)

    def get_text(self):
        """Get the active text as unicode"""

        return self._entry.get_text()

    def get_query(self, star=None):
        if star and star != self._star:
            self._star = star
            self._update_query_from(self.get_text())
        return self._query

    def changed(self):
        """Triggers a filter-changed signal if the current text
        is a parsable query
        """

        self._filter_changed()

    def __inhibit(self):
        self.__combo.handler_block(self.__sig)

    def __uninhibit(self):
        self.__combo.handler_unblock(self.__sig)

    def __menu(self, entry, menu):
        sep = SeparatorMenuItem()
        sep.show()
        menu.prepend(sep)

        cb = ConfigCheckMenuItem(
            _("Search after _typing"), 'settings', 'eager_search',
            populate=True)
        cb.set_tooltip_text(
            _("Show search results after the user stops typing"))
        cb.show()
        menu.prepend(cb)

    def __mnemonic_activate(self, label, group_cycling):
        widget = label.get_mnemonic_widget()
        if widget.is_focus():
            self.emit('focus-out')
            return True

    def __save_search(self, entry, *args):
        # only save the query on focus-out if eager_search is turned on
        if (len(args) > 0
                and args[0]
                and not config.getboolean('settings', 'eager_search')):
            return

        text = self.get_text().strip()
        if text and self._query and self._query.is_parsable:
            # Adding the active text to the model triggers a changed signal
            # (get_active is no longer -1), so inhibit
            self.__inhibit()
            self.__combo.prepend_text(text)
            self.__combo.write()
            self.__uninhibit()

    def __key_pressed(self, entry, event):
        if (is_accel(event, '<Primary>Return') or
                is_accel(event, '<Primary>KP_Enter')):
            # Save query on Primary+Return accel, even though the focus is kept
            self.__save_search(entry)
        return False

    def _filter_changed(self, *args):
        self.__deferred_changed.abort()
        text = self.get_text()
        self._update_query_from(text)
        if self._query.is_parsable:
            GLib.idle_add(self.emit, 'query-changed', text)

    def __text_changed(self, *args):
        if not self._entry.is_sensitive():
            return
        # the combobox has an active entry selected -> no timeout
        # todo: we need a timeout when the selection changed because
        # of keyboard input (up/down arrows)
        if self.__combo.get_active() != -1:
            self._filter_changed()
            return

        if not config.getboolean('settings', 'eager_search'):
            return

        self.__deferred_changed()


class LimitSearchBarBox(SearchBarBox):
    """A version of `SearchBarBox` that allows specifying the limiting and
    weighting of a search."""

    class Limit(Gtk.HBox):
        __gsignals__ = {
            'changed': (GObject.SignalFlags.RUN_LAST, None, ()),
        }

        def __init__(self):
            super().__init__(spacing=3,
                                                          no_show_all=True)
            label = Gtk.Label(label=_("_Limit:"))
            self.pack_start(label, True, True, 0)

            self.__limit = limit = Gtk.SpinButton()
            self.__limit.connect("value-changed", self.__changed)
            limit.set_numeric(True)
            limit.set_range(0, 9999)
            limit.set_increments(5, 100)
            label.set_mnemonic_widget(limit)
            label.set_use_underline(True)
            self.pack_start(limit, True, True, 0)

            self.__weight = Gtk.CheckButton(
                label=_("_Weight"), use_underline=True)
            self.__weight.connect("toggled", self.__changed)
            self.pack_start(self.__weight, True, True, 0)

            for child in self.get_children():
                child.show()

        def __changed(self, *args):
            self.emit("changed")

        @property
        def value(self):
            return self.__limit.get_value_as_int()

        @property
        def weighted(self):
            return self.__weight.get_active()

    def __init__(self, show_limit=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__limit = self.Limit()
        self.__limit.set_visible(show_limit)
        self.pack_start(self.__limit, False, True, 0)
        self.__limit.connect("changed", self.__limit_changed)

    def __limit_changed(self, *args):
        self.changed()

    def limit(self, songs):
        if self.__limit.get_visible():
            return limit_songs(songs, self.__limit.value,
                               self.__limit.weighted)
        else:
            return songs

    def toggle_limit_widgets(self, button):
        """Toggles the visibility of the limit widget according to `button`"""
        if button.get_active():
            self.__limit.show()
        else:
            self.__limit.hide()
        self.changed()


class MultiSearchBarBox(LimitSearchBarBox):
    """An extension of `LimitSearchBarBox` allowing multiple queries.

    Note: Instances of this class must have their flow_box attribute packed by
    their parents or the multiple queries won't work.
    """

    def __init__(self, *args, show_multi=False, multi_filename=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.multi_filename = os.path.join(
            quodlibet.get_data_dir(), "lists", "multiqueries"
        ) if multi_filename is None else multi_filename

        self._old_placeholder = self._entry.get_placeholder_text()
        self._old_tooltip = self._entry.get_tooltip_text()

        self._add_button = Gtk.Button.new_from_icon_name("list-add",
                                                         Gtk.IconSize.BUTTON)
        self._add_button.set_no_show_all(True)
        self.pack_start(self._add_button, False, True, 0)
        self._add_button.connect('clicked', self.activated)
        self._entry.connect('activate', self.activated)

        self.flow_box = Gtk.FlowBox(no_show_all=True,
                                    max_children_per_line=99,
                                    selection_mode=Gtk.SelectionMode.NONE)

        self.toggle_multi_bool(show_multi)

    def activated(self, _):
        if self.flow_box.get_visible():
            text = self.get_text().strip()
            if text == "":  # disallow empty queries
                return
            self.add_query_item(text)
            self.set_text("")
            self._filter_changed()

    def add_query_item(self, text):
        q = QueryItem(text, self._filter_changed)
        q.show()
        self.flow_box.add(q)

    def load(self):
        try:
            with open(self.multi_filename) as f:
                for row in f:
                    self.add_query_item(row.strip())
        except OSError:
            pass

    def save(self):
        if not os.path.isdir(os.path.dirname(self.multi_filename)):
            os.makedirs(os.path.dirname(self.multi_filename))

        with open(self.multi_filename, "w") as f:
            f.writelines(lq.string + "\n"
                         for lq in self.flow_box.get_children())

    def _update_query_from(self, text):
        if self.flow_box.get_visible():
            matches = [lq.query._unpack()
                       for lq in self.flow_box.get_children()]

            self._query = Query(text, star=self._star)
            self._query._match = reduce(operator.and_, matches,
                                        self._query._match)
        else:
            super()._update_query_from(text)

    def toggle_multi(self, button):
        """Toggles the multiquery mode according to `button`"""
        self.toggle_multi_bool(button.get_active())

    def toggle_multi_bool(self, multi):
        """Toggles the multiquery mode to the given bool"""
        if multi:
            self._add_button.show()
            self.flow_box.show()

            self._old_placeholder = self._entry.get_placeholder_text()
            self._old_tooltip = self._entry.get_tooltip_text()
            self._entry.set_placeholder_text(_("Add query"))
            self._entry.set_tooltip_text(_("Add a QL query or free text "
                                           "to be &ed together"))
        else:
            self._add_button.hide()
            self.flow_box.hide()

            self._entry.set_placeholder_text(self._old_placeholder)
            self._entry.set_tooltip_text(self._old_tooltip)
        self.changed()


class QueryItem(Gtk.FlowBoxChild):
    """A FlowBoxChild representing a query"""

    def __init__(self, string, changed_callback):
        super().__init__()

        self.changed_callback = changed_callback
        self.string = string
        self.query = Query(string)

        hbox = Gtk.HBox()
        hbox.pack_start(Gtk.Label(string, halign=Gtk.Align.START, margin=6),
                        True, True, 0)
        btn = Gtk.Button.new_from_icon_name("window-close",
                                            Gtk.IconSize.BUTTON)
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.connect('clicked', self.remove)
        hbox.pack_start(btn, False, True, 0)
        frame = Gtk.Frame()
        frame.add(hbox)
        self.add(frame)
        self.show_all()

    def remove(self, _):
        self.destroy()
        self.changed_callback()
