# Copyright 2014 Christoph Reiter
#           2020 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
from typing import Type

from senf import path2fsn, fsn2bytes, bytes2fsn, fsnative

from quodlibet.util import fifo, print_w
from quodlibet import get_runtime_dir
try:
    from quodlibet.util import winpipe
except ImportError:
    winpipe = None  # type: ignore


class RemoteError(Exception):
    pass


class RemoteBase:
    """A thing for communicating with existing instances of ourself."""

    def __init__(self, app, cmd_registry):
        """
        Args:
            app (Application)
            cmd_registry (CommandRegistry)
        """

        raise NotImplementedError

    @classmethod
    def remote_exists(cls):
        """See if another instance exists

        Returns:
            bool
        """

        raise NotImplementedError

    @classmethod
    def send_message(cls, message):
        """Send data to the existing instance if possible and returns
        a response.

        Args:
            message (fsnative)
        Returns:
            fsnative or None
        Raises:
            RemoteError: in case the message couldn't be send or
                there was no response.
        """

        raise NotImplementedError

    def start(self):
        """Start the listener for other instances.

        Raises:
            RemoteError: in case another instance is already listening.
        """

        raise NotImplementedError

    def stop(self):
        """Stop the listener for other instances"""

        raise NotImplementedError


class QuodLibetWinRemote(RemoteBase):

    _NAME = "quodlibet"

    def __init__(self, app, cmd_registry):
        self._app = app
        self._cmd_registry = cmd_registry
        self._server = winpipe.NamedPipeServer(self._NAME, self._callback)

    @classmethod
    def remote_exists(cls):
        return winpipe.pipe_exists(cls._NAME)

    @classmethod
    def send_message(cls, message):
        data = fsn2bytes(path2fsn(message), "utf-8")
        try:
            winpipe.write_pipe(cls._NAME, data)
        except EnvironmentError as e:
            raise RemoteError(e)

    def start(self):
        try:
            self._server.start()
        except winpipe.NamedPipeServerError as e:
            raise RemoteError(e)

    def stop(self):
        self._server.stop()

    def _callback(self, data):
        message = bytes2fsn(data, "utf-8")
        self._cmd_registry.handle_line(self._app, message)


class QuodLibetUnixRemote(RemoteBase):

    _FIFO_NAME = "control"
    _PATH = os.path.join(get_runtime_dir(), _FIFO_NAME)

    def __init__(self, app, cmd_registry):
        self._app = app
        self._cmd_registry = cmd_registry
        self._fifo = fifo.FIFO(self._PATH, self._callback)

    @classmethod
    def remote_exists(cls):
        return fifo.fifo_exists(cls._PATH)

    @classmethod
    def send_message(cls, message):
        assert isinstance(message, fsnative)

        try:
            return fifo.write_fifo(cls._PATH, fsn2bytes(message, None))
        except fifo.FIFOError as e:
            raise RemoteError(e)

    def start(self):
        try:
            self._fifo.open()
        except fifo.FIFOError as e:
            raise RemoteError(e)

    def stop(self):
        self._fifo.destroy()

    def _callback(self, data):
        try:
            messages = list(fifo.split_message(data))
        except ValueError:
            print_w("invalid message: %r" % data)
            return

        for command, path in messages:
            command = bytes2fsn(command, None)
            response = self._cmd_registry.handle_line(self._app, command)
            if path is not None:
                path = bytes2fsn(path, None)
                with open(path, "wb") as h:
                    if response is not None:
                        assert isinstance(response, fsnative)
                        h.write(fsn2bytes(response, None))


Remote: Type[RemoteBase]

if os.name == "nt":
    Remote = QuodLibetWinRemote
else:
    Remote = QuodLibetUnixRemote
