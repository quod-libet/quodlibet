# Copyright 2012 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import sys
import os

if sys.platform == "win32":
    # To prevent us loading DLLs in the system directory which clash
    # with the ones we ship.
    # https://github.com/quodlibet/quodlibet/issues/2817
    from ctypes import windll
    windll.kernel32.SetDllDirectoryW(os.path.dirname(sys.executable))

from ._import import install_redirect_import_hook
install_redirect_import_hook()

from .util.i18n import _, C_, N_, ngettext, npgettext
from .util.dprint import print_d, print_e, print_w
from ._init import init_cli, init
from ._main import get_base_dir, is_release, app, \
    set_application_info, init_plugins, enable_periodic_save, run, \
    finish_first_session, get_image_dir, is_first_session, \
    get_build_description, get_build_version, \
    get_config_dir, get_data_dir, get_cache_dir, get_runtime_dir


_, C_, N_, ngettext, npgettext, is_release, init, init_cli, print_e, \
    get_base_dir, print_w, print_d, app, set_application_info, \
    init_plugins, enable_periodic_save, run, finish_first_session, \
    get_image_dir, is_first_session, \
    get_build_description, get_build_version, \
    get_config_dir, get_data_dir, get_cache_dir, get_runtime_dir
