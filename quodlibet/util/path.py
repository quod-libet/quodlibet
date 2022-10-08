# Copyright 2004-2009 Joe Wreschnig, Michael Urman, Steven Robertson
#           2011-2022 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import io
import re
import stat
import sys
import errno
import codecs
import shlex
from urllib.parse import urlparse, quote, unquote

from gi.repository import GLib

from senf import (fsnative, bytes2fsn, fsn2bytes,
                  fsn2text, path2fsn, uri2fsn, _fsnative)

from . import windows
from . import print_w
from .environment import is_windows
from .misc import NamedTemporaryFile

if sys.platform == "darwin":
    from Foundation import NSString


def mkdir(dir_, *args):
    """Make a directory, including all its parent directories. This does not
    raise an exception if the directory already exists (and is a
    directory)."""

    try:
        os.makedirs(dir_, *args)
    except OSError as e:
        if e.errno != errno.EEXIST or not os.path.isdir(dir_):
            raise


def uri2gsturi(uri):
    """Takes a correct uri and returns a gstreamer-compatible uri"""
    if not is_windows():
        return uri
    try:
        # gstreamer requires extra slashes for network shares
        return GLib.filename_to_uri(uri2fsn(uri))
    except (GLib.Error, ValueError):
        return uri


def iscommand(s):
    """True if an executable file `s` exists in the user's path, or is a
    fully qualified and existing executable file."""

    if s == "" or os.path.sep in s:
        return os.path.isfile(s) and os.access(s, os.X_OK)
    else:
        s = s.split()[0]
        path = os.environ.get('PATH', '') or os.defpath
        for p in path.split(os.path.pathsep):
            p2 = os.path.join(p, s)
            if os.path.isfile(p2) and os.access(p2, os.X_OK):
                return True
        else:
            return False


def listdir(path, hidden=False):
    """List files in a directory, sorted, fully-qualified.

    If hidden is false, Unix-style hidden files are not returned.
    """

    assert isinstance(path, fsnative)

    if hidden:
        filt = None
    else:
        filt = lambda base: not base.startswith(".")
    if path.endswith(os.sep):
        join = "".join
    else:
        join = os.sep.join
    return [join([path, basename])
            for basename in sorted(os.listdir(path))
            if filt(basename)]


def mtime(filename):
    """Return the mtime of a file, or 0 if an error occurs."""
    try:
        return os.path.getmtime(filename)
    except OSError:
        return 0


def filesize(filename):
    """Return the size of a file, or 0 if an error occurs."""
    try:
        return os.path.getsize(filename)
    except OSError:
        return 0


def escape_filename(s: str, safe: bytes = b''):
    """Escape a string in a manner suitable for a filename.

    Args:
        s (str) The string to convert
        safe (bytes) A string of characters that needn't be quoted
    Returns:
        fsnative
    """

    s = str(s)
    quoted = quote(s, safe=safe, encoding='utf-8')
    if isinstance(quoted, bytes):
        return bytes2fsn(quoted, "utf-8")
    return bytes2fsn(quoted.encode("ascii"), "utf-8")


def unescape_filename(filename: _fsnative) -> str:
    """Unescape a string in a manner suitable for a filename.

    Args:
        filename (fsnative)
    Returns:
        str
    """

    assert isinstance(filename, fsnative)
    return fsn2text(unquote(filename))


def join_path_with_escaped_name_of_legal_length(path: str, stem: str, ext: str) -> str:
    """Returns a path joined with the escaped stem and the unescaped extension.
    Stem is trimmed until the filename fits into the filesystems maximum file length"""

    # returns the maximum possible filename length at path (subtract one for dot)
    max_stem_length = os.pathconf(path, 'PC_NAME_MAX') - 1 - len(ext)

    escaped_stem = escape_filename(stem)
    while len(escaped_stem) > max_stem_length:
        # We don't want to break the escaping, so we only trim the actual name
        stem = stem[:max_stem_length]
        max_stem_length -= 1
        escaped_stem = escape_filename(stem)

    return os.path.join(path, f'{escaped_stem}.{ext}')


def stem_of_file_name(file_name: str) -> str:
    """:return: file name without the extension.

    Note these examples showcasing edge cases:

    >>> stem_of_file_name('a.b.c')
    'a.b'
    >>> stem_of_file_name('.test')
    '.test'
    """
    return os.path.splitext(file_name)[0]


def extension_of_file_name(file_name: str) -> str:
    """:return: extension of the file name. Is empty, or starts with a period.

    Note these examples showcasing edge cases:

    >>> extension_of_file_name('a.b.c')
    '.c'
    >>> extension_of_file_name('.test')
    ''
    """
    return os.path.splitext(file_name)[-1]


def unexpand(filename):
    """Replace the user's home directory with ~ or %USERPROFILE%, if it
    appears at the start of the path name.

    Args:
        filename (fsnative): The file path
    Returns:
        fsnative: The path with the home directory replaced
    """

    sub = (os.name == "nt" and fsnative(u"%USERPROFILE%")) or fsnative(u"~")
    home = os.path.normcase(get_home_dir()).rstrip(os.path.sep)
    norm = os.path.normcase(filename)
    if norm == home:
        return sub
    elif norm.startswith(home + os.path.sep):
        filename = sub + filename[len(home):]
    return filename


if is_windows():
    def ismount(path):
        # this can raise on py3+win, but we don't care
        try:
            return os.path.ismount(path)
        except OSError:
            return False
else:
    ismount = os.path.ismount


def find_mount_point(path):
    while not ismount(path):
        path = os.path.dirname(path)
    return path


def xdg_get_system_data_dirs():
    """http://standards.freedesktop.org/basedir-spec/latest/"""

    if os.name == "nt":
        from gi.repository import GLib
        dirs = []
        for dir_ in GLib.get_system_data_dirs():
            dirs.append(dir_)
        return dirs

    data_dirs = os.getenv("XDG_DATA_DIRS")
    if data_dirs:
        return [os.path.abspath(d)
                for d in data_dirs.split(":")]
    else:
        return ("/usr/local/share/", "/usr/share/")


def xdg_get_cache_home():
    if os.name == "nt":
        from gi.repository import GLib
        return GLib.get_user_cache_dir()

    data_home = os.getenv("XDG_CACHE_HOME")
    if data_home:
        return os.path.abspath(data_home)
    else:
        return os.path.join(os.path.expanduser("~"), ".cache")


def xdg_get_data_home():
    if os.name == "nt":
        from gi.repository import GLib
        return GLib.get_user_data_dir()

    data_home = os.getenv("XDG_DATA_HOME")
    if data_home:
        return os.path.abspath(data_home)
    else:
        return os.path.join(os.path.expanduser("~"), ".local", "share")


def xdg_get_config_home():
    if os.name == "nt":
        from gi.repository import GLib
        return GLib.get_user_config_dir()

    data_home = os.getenv("XDG_CONFIG_HOME")
    if data_home:
        return os.path.abspath(data_home)
    else:
        return os.path.join(os.path.expanduser("~"), ".config")


def xdg_get_runtime_dir():
    if os.name == "nt":
        from gi.repository import GLib
        return GLib.get_user_runtime_dir()

    data_home = os.getenv("XDG_RUNTIME_DIR")
    if data_home:
        return os.path.abspath(data_home)
    else:
        # > fall back to a replacement directory with similar capabilities and
        # > print a warning message
        print_w('Using the cache as runtime directory')
        return xdg_get_cache_home()


def parse_xdg_user_dirs(data):
    """Parses xdg-user-dirs and returns a dict of keys and paths.

    The paths depend on the content of environ while calling this function.
    See http://www.freedesktop.org/wiki/Software/xdg-user-dirs/

    Args:
        data (bytes)

    Can't fail (but might return garbage).
    """

    assert isinstance(data, bytes)

    paths = {}
    for line in data.splitlines():
        if line.startswith(b"#"):
            continue
        parts = line.split(b"=", 1)
        if len(parts) <= 1:
            continue
        key = parts[0]
        try:
            values = shlex.split(bytes2fsn(parts[1], "utf-8"))
        except ValueError:
            continue
        if len(values) != 1:
            continue
        paths[key] = os.path.normpath(os.path.expandvars(values[0]))

    return paths


def xdg_get_user_dirs():
    """Returns a dict of xdg keys to paths. The paths don't have to exist."""
    config_home = xdg_get_config_home()
    try:
        with open(os.path.join(config_home, "user-dirs.dirs"), "rb") as h:
            return parse_xdg_user_dirs(h.read())
    except EnvironmentError:
        return {}


def get_temp_cover_file(data):
    """Returns a file object or None"""

    try:
        # pass fsnative so that mkstemp() uses unicode on Windows
        fn = NamedTemporaryFile(prefix=fsnative(u"tmp"))
        fn.write(data)
        fn.flush()
        fn.seek(0, 0)
    except EnvironmentError:
        return
    else:
        return fn


def _strip_win32_incompat(string, BAD=r'\:*?;"<>|'):
    """Strip Win32-incompatible characters from a Windows or Unix path."""

    if os.name == "nt":
        BAD += "/"

    if not string:
        return string

    new = "".join((s in BAD and "_") or s
                  for s in string)
    parts = new.split(os.sep)

    def fix_end(string):
        return re.sub(r'[\. ]$', "_", string)
    return os.sep.join(fix_end(p)
                       for p in parts)


def strip_win32_incompat_from_path(string):
    """Strip Win32-incompatible chars from a path, ignoring os.sep
    and the drive part"""

    drive, tail = os.path.splitdrive(string)
    tail = os.sep.join(_strip_win32_incompat(s)
                       for s in tail.split(os.sep))
    return drive + tail


def _normalize_darwin_path(filename, canonicalise=False):

    filename = path2fsn(filename)

    if canonicalise:
        filename = os.path.realpath(filename)
    filename = os.path.normpath(filename)

    data = fsn2bytes(filename, "utf-8")
    decoded = data.decode("utf-8", "quodlibet-osx-path-decode")

    try:
        return bytes2fsn(
            NSString.fileSystemRepresentation(decoded), "utf-8")
    except ValueError:
        return filename


def _normalize_path(filename, canonicalise=False):
    """Normalize a path on Windows / Linux
    If `canonicalise` is True, dereference symlinks etc
    by calling `os.path.realpath`
    """
    filename = path2fsn(filename)
    if canonicalise:
        filename = os.path.realpath(filename)
    filename = os.path.normpath(filename)
    return os.path.normcase(filename)


if sys.platform == "darwin":

    def _osx_path_decode_error_handler(error):
        bytes_ = bytearray(error.object[error.start:error.end])
        return u"".join("%%%X".__mod__(b) for b in bytes_), error.end

    codecs.register_error(
        "quodlibet-osx-path-decode", _osx_path_decode_error_handler)

    normalize_path = _normalize_darwin_path
else:
    normalize_path = _normalize_path


def path_equal(p1, p2, canonicalise=False):
    return normalize_path(p1, canonicalise) == normalize_path(p2, canonicalise)


def limit_path(path, ellipsis=True):
    """Reduces the filename length of all filenames in the given path
    to the common maximum length for current platform.

    While the limits are depended on the file system and more restrictions
    may apply, this covers the common case.
    """

    assert isinstance(path, fsnative)

    main, ext = os.path.splitext(path)
    parts = main.split(os.sep)
    for i, p in enumerate(parts):
        # Limit each path section to 255 (bytes on linux, chars on win).
        # http://en.wikipedia.org/wiki/Comparison_of_file_systems#Limits
        limit = 255
        if i == len(parts) - 1:
            limit -= len(ext)

        if len(p) > limit:
            if ellipsis:
                p = p[:limit - 2] + fsnative(u"..")
            else:
                p = p[:limit]
        parts[i] = p

    return os.sep.join(parts) + ext


def get_home_dir():
    """Returns the root directory of the user, /home/user or C:\\Users\\user"""

    if os.name == "nt":
        return windows.get_profile_dir()
    else:
        return os.path.expanduser("~")


def is_hidden(path: _fsnative) -> bool:
    """Returns if a directory / file is considered hidden by the platform.

    Hidden meaning the user should normally not be exposed to those files when
    opening the parent directory in the default file manager using the default
    settings.

    Does not check if any of the parents are hidden.
    If the file / dir does not exist, the result is implementation defined.

    :param path: the path to check
    :return: True if and only if the path is considered hidden on the system
    """

    if sys.platform == "windows":
        return bool(os.stat(path).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)
    basename = os.path.basename(path)
    # Let's allow "...and Justice For All" etc (#3916)
    return basename.startswith(".") and basename[1:2] != "."


def uri_is_valid(uri):
    """Returns True if the passed in text is a valid URI (file, http, etc.)

    Args:
        uri(text or bytes)
    Returns:
        bool
    """

    try:
        if isinstance(uri, bytes):
            uri.decode("ascii")
        elif not isinstance(uri, bytes):
            uri = uri.encode("ascii")
    except ValueError:
        return False

    parsed = urlparse(uri)
    if not parsed.scheme or not len(parsed.scheme) > 1:
        return False
    elif not (parsed.netloc or parsed.path):
        return False
    else:
        return True


class RootPathFile:
    """Simple container used for discerning a pathfile's 'root' directory
    and 'end' part. The variable depth of a pathfile's 'end' part renders
    os.path built-ins (basename etc.) useless for this purpose"""

    _root = ''  # 'root' of full file path
    _pathfile = ''  # full file path

    def __init__(self, root, pathfile):
        self._root = root
        self._pathfile = pathfile

    @property
    def root(self):
        return self._root

    @property
    def end(self):
        return self._pathfile[len(self._root) + len(os.sep):]

    @property
    def pathfile(self):
        return self._pathfile

    @property
    def end_escaped(self):
        escaped = [escape_filename(part)
                   for part in self.end.split(os.path.sep)]
        return os.path.sep.join(escaped)

    @property
    def pathfile_escaped(self):
        return os.path.sep.join([self.root, self.end_escaped])

    @property
    def valid(self):
        valid = True
        if os.path.exists(self.pathfile):
            return valid
        else:
            try:
                with io.open(self.pathfile, "w", encoding='utf-8') as f:
                    f.close()  # do nothing
            except OSError:
                valid = False
            if os.path.exists(self.pathfile):
                os.remove(self.pathfile)
            return valid
