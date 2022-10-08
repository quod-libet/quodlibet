# Copyright 2017 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import threading
import sys
import platform
import os

from gi.repository import GLib
import mutagen
import cairo

import quodlibet
from quodlibet import app
from quodlibet.build import BUILD_TYPE, BUILD_INFO
from quodlibet.util import fver, cached_func, is_main_thread
from quodlibet.util.dprint import format_exception, print_exc, print_e
from quodlibet.qltk import gtk_version, pygobject_version, get_backend_name

from .sentrywrapper import Sentry, SentryError
from .ui import ErrorDialog, find_active_window, SubmitErrorDialog
from .faulthandling import FaultHandlerCrash
from .logdump import dump_to_disk


@cached_func
def get_sentry():
    """Returns a cached Sentry instance

    Returns:
        Sentry
    """

    # reverse, so it isn't so easy to search for at least
    SENTRY_DSN = (
        "514241/oi.yrtnes@0818e5ab0218038bcbb41f049ec5de21:"
        "0d15f73b978d143b5e84030a1ddf9a73//:sptth")[::-1]

    sentry = Sentry(SENTRY_DSN)
    sentry.add_tag("release", quodlibet.get_build_description())
    sentry.add_tag("build_type", BUILD_TYPE)
    sentry.add_tag("build_info", BUILD_INFO or "NONE")
    sentry.add_tag("mutagen_version", fver(mutagen.version))
    sentry.add_tag("python_version", platform.python_version())
    sentry.add_tag("gtk_version", fver(gtk_version))
    sentry.add_tag("gtk_backend", get_backend_name())
    sentry.add_tag("pygobject_version", fver(pygobject_version))
    sentry.add_tag("pycairo_version", fver(cairo.version_info))
    sentry.add_tag("platform", platform.platform())

    return sentry


# We guard against recursive errors
_error_lock = threading.Lock()
_errorhook_enabled = False


def enable_errorhook(value):
    """Enables/Disables the error hook and the excepthook integration.

    Args:
        value (bool)
    """

    global _errorhook_enabled

    value = bool(value)
    _errorhook_enabled = value
    if value:
        sys.excepthook = excepthook
    else:
        sys.excepthook = sys.__excepthook__


def run_error_dialogs(exc_info, sentry_error):
    assert sentry_error is not None

    error_text = u"%s: %s" % (
        exc_info[0].__name__,
        (str(exc_info[1]).strip() or u"\n").splitlines()[0])
    error_text += u"\n------\n"
    error_text += u"\n".join(format_exception(*exc_info))

    # Don't reshow the error dialog in case the user wanted to quit the app
    # but due to the error state more errors pile up..
    if app.is_quitting:
        return

    window = find_active_window()
    if window is None:
        return

    # XXX: This does blocking IO and uses nested event loops... but it's simple
    dialog = ErrorDialog(window, error_text)
    while 1:
        response = dialog.run()
        if response == ErrorDialog.RESPONSE_RESTART:
            dialog.destroy()
            app.restart()
        elif response == ErrorDialog.RESPONSE_SUBMIT:
            dialog.hide()
            submit_dialog = SubmitErrorDialog(
                window, sentry_error.get_report())
            submit_response = submit_dialog.run()

            if submit_response == SubmitErrorDialog.RESPONSE_SUBMIT:
                sentry_error.set_comment(submit_dialog.get_comment())
                timeout_seconds = 5
                try:
                    sentry_error.send(timeout_seconds)
                except SentryError:
                    print_exc()
                submit_dialog.destroy()
                dialog.destroy()
            else:
                submit_dialog.destroy()
                dialog.show()
                continue
        else:
            dialog.destroy()
        break


def errorhook(exc_info=None):
    """This is the main entry point

    Call in an exception context. Thread safe.

    def my_thread():
        try:
            do_work()
        except Exception:
            errorhook()
    """

    global _error_lock, _errorhook_enabled

    if not _errorhook_enabled:
        return

    if exc_info is None:
        exc_info = sys.exc_info()

    if exc_info[0] is None:
        # called outside of an exception context, just ignore
        print_e("no active exception!")
        return

    # In case something goes wrong during error handling print it first
    print_exc(exc_info)

    if not _error_lock.acquire(False):
        # Make sure only one of these is active at a time
        return

    # write error and logs to disk (cache folder)
    dump_dir = os.path.join(quodlibet.get_cache_dir(), "dumps")
    dump_to_disk(dump_dir, exc_info)

    sentry = get_sentry()

    # For crashes the stack trace is not enough to differentiating different
    # crash sources. We need to give our own grouping key (fingerprint) based
    # on the stack trace provided by faulthandler.
    fingerprint = None
    if isinstance(exc_info[1], FaultHandlerCrash):
        fingerprint = ["{{ default }}", exc_info[1].get_grouping_key()]

    try:
        sentry_error = sentry.capture(exc_info, fingerprint=fingerprint)
    except SentryError:
        print_exc()
        sentry_error = None

    def called_in_main_thread():
        try:
            if sentry_error is not None:
                run_error_dialogs(exc_info, sentry_error)
        finally:
            _error_lock.release()

    if is_main_thread():
        called_in_main_thread()
    else:
        GLib.idle_add(called_in_main_thread)


def excepthook(*exc_info):
    """Custom exception hook. This is called in case an unhandled exception
    occurs in the main thread. In other threads errorhook() has to be called
    explicitly.
    """

    errorhook(exc_info)
