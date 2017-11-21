# -*- coding: utf-8 -*-
# Copyright 2016 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import random

from quodlibet import _
from quodlibet.order import Order, OrderRemembered
from quodlibet.compat import iteritems


class Reorder(Order):
    """Base class for all `Order`s that potentially reorder the playlist,
    and thus usually identify as a "shuffle" implementation."""
    pass


class OrderShuffle(Reorder, OrderRemembered):
    name = "random"
    display_name = _("Random")
    accelerated_name = _("_Random")

    def next(self, playlist, iter):
        super(OrderShuffle, self).next(playlist, iter)
        played = set(self._played)
        songs = set(range(len(playlist)))
        remaining = songs.difference(played)

        if remaining:
            return playlist.get_iter((random.choice(list(remaining)),))
        return None


class OrderWeighted(Reorder, OrderRemembered):
    name = "weighted"
    display_name = _("Prefer higher rated")
    accelerated_name = _("Prefer higher rated")

    def next(self, playlist, iter):
        super(OrderWeighted, self).next(playlist, iter)
        remaining = self.remaining(playlist)

        # Don't try to search through an empty / played playlist.
        if len(remaining) <= 0:
            return None

        total_score = sum([song('~#rating') for song in remaining.values()])
        choice = random.random() * total_score
        current = 0.0
        for i, song in iteritems(remaining):
            current += song("~#rating")
            if current >= choice:
                return playlist.get_iter([i])
        raise ValueError
