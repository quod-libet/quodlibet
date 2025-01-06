# Copyright 2006 Joe Wreschnig
#           2016 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from gi.repository import Gtk, GObject

from quodlibet import _
from quodlibet import app
from quodlibet import config
from quodlibet import qltk
from quodlibet.order import Order, OrderInOrder
from quodlibet.order.reorder import OrderShuffle, OrderWeighted, Reorder
from quodlibet.order.repeat import RepeatListForever, RepeatSongForever, \
    Repeat, OneSong
from quodlibet.qltk import Icons
from quodlibet.qltk.x import SymbolicIconImage, RadioMenuItem, \
    SeparatorMenuItem, HighlightToggleButton
from quodlibet.plugins import PluginManager
from quodlibet.util.dprint import print_w, print_d


class Orders(GObject.Object):
    """
    A minimal list-like container for Order objects
    that signals on changes
    """

    __gsignals__ = {
        "updated": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, initial=None):
        super().__init__()
        self.items = initial or []

    def by_name(self, name):
        if not name:
            return None
        for cls in self.items:
            if cls.name == name:
                return cls
        return None

    def __getitem__(self, y):
        return self.items.__getitem__(y)

    def __len__(self):
        return self.items.__len__()

    def append(self, x):
        self.items.append(x)
        self.emit("updated")

    def remove(self, x):
        self.items.remove(x)
        self.emit("updated")

    def __contains__(self, y):
        return self.items.__contains__(y)

    def __str__(self):
        return f"<{type(self).__name__} of {self.items}>"


class PluggableOrders(Orders, PluginManager):
    """Registers as a Plugin Handler for various types of `Order` plugins"""
    def __init__(self, orders, base_cls):
        assert issubclass(base_cls, Order)
        super().__init__(orders)
        self.base_cls = base_cls
        if PluginManager.instance:
            PluginManager.instance.register_handler(self)
        else:
            print_w("No plugin manager found")

    def plugin_handle(self, plugin):
        return issubclass(plugin.cls, self.base_cls)

    def plugin_enable(self, plugin):
        plugin_cls = plugin.cls
        if plugin_cls.name is None:
            plugin_cls.name = plugin.name
        if plugin_cls.display_name is None:
            plugin_cls.display_name = str(plugin.name).capitalize()
        if plugin_cls.accelerated_name is None:
            plugin_cls.accelerated_name = plugin_cls.display_name
        self.append(plugin_cls)

    def plugin_disable(self, plugin):
        self.remove(plugin.cls)

DEFAULT_SHUFFLE_ORDERS = [OrderShuffle, OrderWeighted]
DEFAULT_REPEAT_ORDERS = [RepeatSongForever, RepeatListForever, OneSong]


class ToggledPlayOrderMenu(Gtk.Box):
    """A toggle button with a menu button.
    Items displayed are all `PlayOrder`

    When the button is toggled, a `toggled` signal gets emitted.
    """

    __gsignals__ = {
        "toggled": (GObject.SignalFlags.RUN_LAST, None, ()),
        "changed": (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    def __init__(self, icon_name, orders, current_order, enabled=False,
                 tooltip=None, arrow_down=False):
        """arrow_down -- the direction of the menu and arrow icon"""
        assert issubclass(current_order, Order)
        if current_order not in orders:
            raise ValueError(f"{current_order.__name__} is not supported by {orders}")

        super().__init__()
        self.__inhibit = True

        context = self.get_style_context()
        context.add_class(Gtk.STYLE_CLASS_LINKED)

        self._toggle_button = toggle = HighlightToggleButton(
            image=SymbolicIconImage(icon_name, Gtk.IconSize.SMALL_TOOLBAR))

        if tooltip:
            toggle.set_tooltip_text(tooltip)
        toggle.set_active(enabled)
        toggle.show_all()
        qltk.remove_padding(toggle)
        toggle.set_size_request(26, 26)
        self.pack_start(toggle, True, True, 0)

        def forward_signal(*args):
            if not self.__inhibit:
                self.emit("toggled")

        toggle.connect("toggled", forward_signal)
        self._toggle_button = toggle

        from quodlibet.qltk.menubutton import MenuButton
        arrow = MenuButton(arrow=True, down=arrow_down)
        arrow.show_all()
        arrow.set_size_request(20, 26)
        qltk.remove_padding(arrow)
        self.pack_start(arrow, True, True, 0)
        self._menu_button = arrow
        self.__current = current_order
        self.__orders = orders
        self.__rebuild_menu()
        self.__inhibit = False

    @property
    def enabled(self):
        """Returns True if toggle button is active"""
        return self._toggle_button.get_active()

    @enabled.setter
    def enabled(self, value):
        """Set button to be active or inactive"""
        self.__inhibit = True
        self._toggle_button.set_active(bool(value))
        self.emit("toggled")
        self.__inhibit = False

    @property
    def orders(self):
        return self.__orders

    @orders.setter
    def orders(self, values):
        self.__orders = values
        if self.__current not in self.orders:
            self.__current = None
            # if current order has become invalid, deactivate
            self.enabled = False
        self.__rebuild_menu()

    @property
    def current(self):
        return self.__current

    @current.setter
    def current(self, value):
        if value not in self.orders:
            orders = ", ".join(o.__name__ for o in self.__orders)
            raise ValueError(f"Unknown order {value}. Try: {orders}")

        self.__current = value
        if not self.__inhibit:
            self.emit("changed", self.__current)
        self.__rebuild_menu()

    def set_active_by_name(self, name):
        for cls in self.__orders:
            if cls.name == name:
                self.current = cls
                return
        raise ValueError(f'Unknown order named "{name}". '
                         f'Try: {[o.name for o in self.__orders]}')

    def set_orders(self, orders):
        self.orders = orders

    def __rebuild_menu(self):

        def toggled_cb(item, order):
            if item.get_active():
                self.current = order

        menu = Gtk.Menu()
        group = None
        prev_priority = None

        def ui_sorted(items):
            return sorted(items, key=lambda k: (k.priority, k.display_name))

        for order in ui_sorted(self.__orders):
            if prev_priority and order.priority > prev_priority:
                menu.append(SeparatorMenuItem())
            prev_priority = order.priority
            group = RadioMenuItem(
                label=order.accelerated_name,
                use_underline=True,
                group=group)
            group.set_active(order == self.__current)
            group.connect("toggled", toggled_cb, order)
            menu.append(group)
        menu.show_all()
        self._menu_button.set_menu(menu)


class PlayOrderWidget(Gtk.HBox):
    """A combined play order selection widget.
    Whenever something changes the 'changed' signal gets emitted.
    """

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, model, player):
        super().__init__(spacing=6)
        self.order = None
        self.__inhibit = True
        self.__playlist = model
        self.__player = player

        def create_shuffle(orders):
            shuffle = ToggledPlayOrderMenu(
                Icons.MEDIA_PLAYLIST_SHUFFLE,
                orders=orders,
                current_order=self.__get_shuffle_class(),
                enabled=(config.getboolean("memory", "shuffle", False)),
                tooltip=_("Toggle shuffle mode"))
            shuffle.connect("changed", self.__shuffle_updated)
            shuffle.connect("toggled", self.__shuffle_toggled)
            return shuffle

        self._shuffle_orders = PluggableOrders(DEFAULT_SHUFFLE_ORDERS, Reorder)
        self.__shuffle_widget = create_shuffle(self._shuffle_orders)
        self._shuffle_orders.connect("updated",
                                     self.__shuffle_widget.set_orders)

        def create_repeat(orders):
            repeat = ToggledPlayOrderMenu(
                Icons.MEDIA_PLAYLIST_REPEAT,
                orders=orders,
                current_order=self.__get_repeat_class(),
                enabled=config.getboolean("memory", "repeat", False),
                tooltip=_("Toggle repeat mode"))
            repeat.connect("changed", self.__repeat_updated)
            repeat.connect("toggled", self.__repeat_toggled)
            return repeat
        self._repeat_orders = PluggableOrders(DEFAULT_REPEAT_ORDERS, Repeat)
        self.__repeat_widget = create_repeat(self._repeat_orders)
        self._repeat_orders.connect("updated", self.__repeat_widget.set_orders)

        self.__compose_order()
        self.pack_start(self.__shuffle_widget, False, True, 0)
        self.pack_start(self.__repeat_widget, False, True, 0)
        self.__inhibit = False

    @property
    def shuffler(self):
        return self.__shuffle_widget.current

    @shuffler.setter
    def shuffler(self, cls):
        assert issubclass(cls, Reorder)
        self.__shuffle_widget.current = cls

    @property
    def repeater(self):
        return self.__repeat_widget.current

    @repeater.setter
    def repeater(self, cls):
        assert issubclass(cls, Repeat)
        self.__repeat_widget.current = cls

    @property
    def shuffled(self):
        return self.__shuffle_widget.enabled

    @shuffled.setter
    def shuffled(self, enabled):
        self.__shuffle_widget.enabled = bool(enabled)

    @property
    def repeated(self):
        return self.__repeat_widget.enabled

    @repeated.setter
    def repeated(self, enabled):
        self.__repeat_widget.enabled = bool(enabled)

    def __repeat_updated(self, widget, repeat_cls):
        if self.__inhibit or app.is_quitting:
            return
        print_d("New repeat mode: %s" % repeat_cls.name)
        config.set("memory", "repeat_mode", repeat_cls.name)
        self.__compose_order()

    def __shuffle_updated(self, widget, shuffle_cls):
        if self.__inhibit or app.is_quitting:
            return
        print_d("New shuffle mode: %s" % shuffle_cls.name)
        config.set("memory", "shuffle_mode", shuffle_cls.name)
        self.__compose_order()

    def __shuffle_toggled(self, widget):
        if self.__inhibit or app.is_quitting:
            return
        config.set("memory", "shuffle", widget.enabled)
        self.__compose_order()

    def __repeat_toggled(self, widget):
        if self.__inhibit or app.is_quitting:
            return
        config.set("memory", "repeat", widget.enabled)
        self.__compose_order()

    def __compose_order(self):
        if app.is_quitting:
            return
        old_order = self.order
        repeat_cls = self.__get_repeat_class()
        shuffle_cls = self.__get_shuffle_class()
        shuffler = (shuffle_cls() if self.shuffled else OrderInOrder())
        self.order = repeat_cls(shuffler) if self.repeated else shuffler
        print_d(f"Updating {type(self.__playlist).__name__} order to {self.order}")
        self.__playlist.order = self.order
        self.__player.replaygain_profiles[2] = shuffler.replaygain_profiles
        self.__player.reset_replaygain()
        if self.order != old_order:
            self.emit("changed")

    def __get_shuffle_class(self):
        name = config.get("memory", "shuffle_mode", None)
        return self._shuffle_orders.by_name(name) or OrderShuffle

    def __get_repeat_class(self):
        name = config.get("memory", "repeat_mode", None)
        return self._repeat_orders.by_name(name) or RepeatSongForever
