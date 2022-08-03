# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import random
from collections import OrderedDict
from typing import Callable, Any, Dict, Iterable, Optional

from quodlibet import print_d, print_w

UndoFunction = Callable[[Iterable[Any], Dict[str, Any]], Any]
UndoID = str


class Undo:

    def __init__(self, undo: UndoFunction, undo_id: str = None,
                 args: Optional[Iterable[Any]] = None,
                 kwargs: Optional[Dict[str, Any]] = None) -> None:
        self._id = undo_id
        self.undo = undo
        self.args = args or []
        self.kwargs = kwargs or {}

    def run(self) -> Any:
        return self.undo(*self.args, **self.kwargs)


class UndoStore:
    """Abstracts generic undo operations"""

    def __init__(self) -> None:
        self.undos: OrderedDict[UndoID, Undo] = OrderedDict()

    def checkpoint(self, undo_fn: UndoFunction, args=None, kwargs=None,
                   _id: str = None) -> UndoID:
        redo_id = _id or f"id-{random.randint(int(1e9), int(1e10))}"
        self.undos[redo_id] = Undo(undo_fn, _id, args, kwargs)
        print_d(f"Added redo for {redo_id}")
        print_d(f"Now have: {self.undos}")
        return redo_id

    def undo(self, undo_id: UndoID = None) -> bool:
        try:
            undo_id = undo_id or list(self.undos.keys())[-1]
        except IndexError:
            # Nothing to undo
            return False
        try:
            undo = self.undos[undo_id]
        except KeyError:
            print_w(f"Unknown undoID: {undo_id}")
            return False
        else:
            print_d(f"Undoing {undo_id!r}")
            undo.run()
        del self.undos[undo_id]
        return True
