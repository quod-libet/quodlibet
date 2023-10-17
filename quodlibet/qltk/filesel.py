# Copyright 2004-2005 Joe Wreschnig, Michael Urman, Iñigo Serna
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import errno
from urllib.parse import urlsplit
from re import split
from functools import total_ordering

from gi.repository import Gtk, GObject, Gdk, Gio, Pango
from senf import uri2fsn, fsnative, fsn2text, bytes2fsn

from quodlibet import formats, print_d, util
from quodlibet import qltk
from quodlibet import _

from quodlibet.util import windows
from quodlibet.qltk.getstring import GetStringDialog
from quodlibet.qltk.views import AllTreeView, RCMHintedTreeView, \
    MultiDragTreeView
from quodlibet.qltk.views import TreeViewColumn
from quodlibet.qltk.x import ScrolledWindow, Paned
from quodlibet.qltk.models import ObjectStore, ObjectTreeStore
from quodlibet.qltk import Icons
from quodlibet.util.path import listdir, \
    xdg_get_user_dirs, get_home_dir, xdg_get_config_home
from quodlibet.util import connect_obj


def search_func(model, column, key, iter_, handledirs):
    check = model.get_value(iter_, 0)
    if check is None:
        return True
    elif not handledirs or os.sep not in key:
        check = os.path.basename(check) or os.sep
    return key not in check.lower() and key not in check


def is_image(filename):
    IMAGES = [".jpg", ".png", ".jpeg"]
    for ext in IMAGES:
        if filename.lower().endswith(ext):
            return True
    return False


def filesel_filter(filename):
    if formats.filter(filename):
        return True
    else:
        return is_image(filename)


def _get_win_favorites():
    """Returns a list of paths for commonly used directories.

    e.g. My Music, Desktop etc.
    """

    assert os.name == "nt"

    folders = []

    funcs = [windows.get_desktop_dir, windows.get_personal_dir,
             windows.get_music_dir]

    for func in funcs:
        path = func()
        if path is not None:
            folders.append(path)

    # try to extract the favorites listed in explorer and add them
    # if not already present
    links = windows.get_links_dir()
    if links is not None:
        try:
            link_entries = os.listdir(links)
        except OSError:
            link_entries = []

        for entry in link_entries:
            if entry.endswith(".lnk"):
                try:
                    target = windows.get_link_target(
                        os.path.join(links, entry))
                except WindowsError:
                    pass
                else:
                    if target:
                        # RecentPlaces.lnk resolves
                        # to an empty string for example
                        folders.append(target)

    # remove duplicated entries
    filtered = []
    for path in folders:
        if path not in filtered:
            filtered.append(path)

    return filtered


def get_favorites():
    """A list of paths of commonly used folders (Desktop,..)

    Paths don't have to exist.
    """

    if os.name == "nt":
        return _get_win_favorites()
    else:
        paths = [get_home_dir()]

        xfg_user_dirs = xdg_get_user_dirs()
        for key in ["XDG_DESKTOP_DIR", "XDG_DOWNLOAD_DIR", "XDG_MUSIC_DIR"]:
            if key in xfg_user_dirs:
                path = xfg_user_dirs[key]
                if path not in paths:
                    paths.append(path)

        return paths


def get_drives():
    """A list of accessible drives"""

    paths = []
    for mount in Gio.VolumeMonitor.get().get_mounts():
        path = mount.get_root().get_path()
        if path is not None:
            paths.append(path)
    if os.name != "nt":
        paths.append("/")
    return sorted(paths)


def parse_gtk_bookmarks(data):
    """
    Args:
        data (bytes)
    Retruns:
        List[fsnative]
    Raises:
        ValueError
    """

    assert isinstance(data, bytes)

    paths = []
    for line in data.splitlines():
        parts = line.split()
        if not parts:
            continue
        folder_url = parts[0]
        paths.append(bytes2fsn(urlsplit(folder_url)[2], "utf-8"))
    return paths


def get_gtk_bookmarks():
    """A list of paths from the GTK+ bookmarks.
    The paths don't have to exist.

    Returns:
        List[fsnative]
    """

    if os.name == "nt":
        return []

    path = os.path.join(xdg_get_config_home(), "gtk-3.0", "bookmarks")
    folders = []
    try:
        with open(path, "rb") as f:
            folders = parse_gtk_bookmarks(f.read())
    except (EnvironmentError, ValueError):
        pass

    return folders


class DirectoryTree(RCMHintedTreeView, MultiDragTreeView):
    """A tree view showing multiple folder hierarchies"""

    def __init__(self, initial=None, folders=None):
        """
        initial -- the path to select/scroll to
        folders -- a list of paths to show in the tree view, None
                   will result in a separator.
        """

        model = ObjectTreeStore()

        def compare(m: Gtk.TreeModel, a: Gtk.TreeIter, b: Gtk.TreeIter, _):

            # Do not sort the top level directories
            a_path = m.get_path(a)
            if a_path is not None and a_path.get_depth() == 1:
                return 0

            @total_ordering
            class KeyElem:
                def __init__(self, v):
                    self.v = v

                @classmethod
                def parse(cls, v: str):
                    return cls(int(v) if v.isnumeric() else v.lower())

                def __eq__(self, other):
                    return self.v == other.v

                def __le__(self, other):
                    # If both keys are the same type (int or string), compare normally
                    if isinstance(other.v, type(self.v)):
                        return self.v <= other.v

                    # Otherwise, ints come first
                    return isinstance(self.v, int)

            def string_to_key(s):
                # Break up the string into parsed components
                k = [KeyElem.parse(v) for v in split(r"(\d+)", s)]

                # Add the original string to the end, to preserve case information
                # (When using lexicographical comparison, this acts as a fallback)
                k.append(KeyElem(s))
                return k

            # Otherwise, files are sorted by their paths
            a_key = string_to_key(m.get_value(a, 0))
            b_key = string_to_key(m.get_value(b, 0))
            return 1 if a_key > b_key else -1

        model.set_sort_func(0, compare)
        model.set_sort_column_id(0, Gtk.SortType.ASCENDING)
        super().__init__(model=model)

        if initial is not None:
            assert isinstance(initial, fsnative)

        column = TreeViewColumn(title=_("Folders"))
        column.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
        render = Gtk.CellRendererPixbuf()
        render.set_property('icon-name', Icons.FOLDER)
        render.props.xpad = 3
        column.pack_start(render, False)
        render = Gtk.CellRendererText()
        if self.supports_hints():
            render.set_property('ellipsize', Pango.EllipsizeMode.END)
        column.pack_start(render, True)

        def cell_data(column, cell, model, iter_, userdata):
            value = model.get_value(iter_)
            if value is not None:
                text = fsn2text(os.path.basename(value) or value)
                cell.set_property('text', text)

        column.set_cell_data_func(render, cell_data)

        self.append_column(column)
        self.set_search_equal_func(search_func, True)
        self.set_search_column(0)

        if folders is None:
            folders = []

        for path in folders:
            niter = model.append(None, [path])
            if path is not None:
                assert isinstance(path, fsnative)
                model.append(niter, [fsnative(u"dummy")])

        self.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.connect(
            'test-expand-row', DirectoryTree.__expanded, model)

        self.set_row_separator_func(
            lambda model, iter_, data: model.get_value(iter_) is None, None)

        if initial:
            self.go_to(initial)

        menu = self._create_menu()
        connect_obj(self, 'popup-menu', self._popup_menu, menu)

        # Allow to drag and drop files from outside
        targets = [
            ("text/uri-list", 0, 42)
        ]
        targets = [Gtk.TargetEntry.new(*t) for t in targets]
        self.drag_dest_set(Gtk.DestDefaults.ALL, targets, Gdk.DragAction.COPY)
        self.connect('drag-data-received', self.__drag_data_received)

    def _create_menu(self):
        menu = Gtk.Menu()
        m = qltk.MenuItem(_(u"_New Folder…"), Icons.DOCUMENT_NEW)
        m.connect('activate', self.__mkdir)
        menu.append(m)
        m = qltk.MenuItem(_("_Delete"), Icons.EDIT_DELETE)
        m.connect('activate', self.__rmdir)
        menu.append(m)
        m = qltk.MenuItem(_("_Refresh"), Icons.VIEW_REFRESH)
        m.connect('activate', self.__refresh)
        menu.append(m)
        m = qltk.MenuItem(_("_Select all Sub-Folders"), Icons.FOLDER)
        m.connect('activate', self.__expand)
        menu.append(m)
        menu.show_all()
        return menu

    def get_selected_paths(self):
        """A list of fs paths"""

        selection = self.get_selection()
        model, paths = selection.get_selected_rows()
        return [model[p][0] for p in paths]

    def go_to(self, path_to_go):
        assert isinstance(path_to_go, fsnative)

        # The path should be normalized in normal situations.
        # On some systems and special environments (pipenv) there might be
        # a non-normalized path at least during tests, though.
        path_to_go = os.path.normpath(path_to_go)

        model = self.get_model()

        # Find the top level row which has the largest common
        # path with the path we want to go to
        roots = dict((p, i) for (i, p) in model.iterrows(None))
        head, tail = path_to_go, fsnative(u"")
        to_find = []
        while head and head not in roots:
            new_head, tail = os.path.split(head)
            # this can happen for invalid paths on Windows
            if head == new_head:
                break
            head = new_head
            to_find.append(tail)
        if head not in roots:
            return
        start_iter = roots[head]

        # expand until we find the right directory or the last valid one
        # and select/scroll to it
        def search(view, model, iter_, to_find):
            tree_path = model.get_path(iter_)

            # we are where we want, select and scroll
            if not to_find:
                view.set_cursor(tree_path)
                view.scroll_to_cell(tree_path)
                return

            # expand the row
            view.expand_row(tree_path, False)

            next_ = to_find.pop(-1)
            for sub_iter, path in model.iterrows(iter_):
                if os.path.basename(path) == next_:
                    search(view, model, sub_iter, to_find)
                    break
            else:
                # we haven't found the right sub folder, select the parent
                # and stop
                search(view, model, iter_, [])

        search(self, model, start_iter, to_find)

    def __drag_data_received(self, widget, drag_ctx, x, y, data, info, time):
        if info == 42:
            uris = data.get_uris()
            if uris:
                try:
                    filename = uri2fsn(uris[0])
                except ValueError:
                    pass
                else:
                    self.go_to(filename)
                    Gtk.drag_finish(drag_ctx, True, False, time)
                    return
        Gtk.drag_finish(drag_ctx, False, False, time)

    def _popup_menu(self, menu):
        model, paths = self.get_selection().get_selected_rows()

        directories = [model[path][0] for path in paths]
        menu_items = menu.get_children()
        delete = menu_items[1]
        try:
            is_empty = not any(len(os.listdir(d)) for d in directories)
            delete.set_sensitive(is_empty)
        except OSError as err:
            if err.errno == errno.ENOENT:
                model.remove(model.get_iter(paths[0]))
            return False
        new_folder = menu_items[0]
        new_folder.set_sensitive(len(paths) == 1)

        selection = self.get_selection()
        selection.unselect_all()
        for path in paths:
            selection.select_path(path)
        return self.popup_menu(menu, 0, Gtk.get_current_event_time())

    def __mkdir(self, button):
        model, paths = self.get_selection().get_selected_rows()
        if len(paths) != 1:
            return

        path = paths[0]
        directory = model[path][0]

        dir_ = GetStringDialog(
            None, _("New Folder"), _("Enter a name for the new folder:")).run()

        if not dir_:
            return

        fullpath = os.path.realpath(os.path.join(directory, dir_))

        try:
            os.makedirs(fullpath)
        except EnvironmentError as err:
            error = f"{util.bold(err.filename)}: {util.escape(err.strerror)}"
            qltk.ErrorMessage(
                None, _("Unable to create folder"), error, escape_desc=False).run()
            return

        self.emit('test-expand-row', model.get_iter(path), path)
        self.expand_row(path, False)

    def __rmdir(self, button):
        model, paths = self.get_selection().get_selected_rows()

        directories = [model[path][0] for path in paths]
        print_d("Deleting %d empty directories" % len(directories))
        for directory in directories:
            try:
                os.rmdir(directory)
            except EnvironmentError as err:
                error = f"{util.bold(err.filename)}: {err.strerror}"
                qltk.ErrorMessage(
                    None, _("Unable to delete folder"), error, escape_desc=False).run()
                return

        ppath = Gtk.TreePath(paths[0][:-1])
        expanded = self.row_expanded(ppath)
        self.emit('test-expand-row', model.get_iter(ppath), ppath)
        if expanded:
            self.expand_row(ppath, False)

    def __expand(self, button):
        selection = self.get_selection()
        model, paths = selection.get_selected_rows()

        for path in paths:
            iter_ = model.get_iter(path)
            self.expand_row(path, False)
            last = self.__select_children(iter_, model, selection)
            selection.select_range(path, last)

    def __select_children(self, iter_, model, selection):
        nchildren = model.iter_n_children(iter_)
        last = model.get_path(iter_)

        for i in range(nchildren):
            child = model.iter_nth_child(iter_, i)
            self.expand_row(model.get_path(child), False)
            last = self.__select_children(child, model, selection)
        return last

    def __refresh(self, button):
        model, rows = self.get_selection().get_selected_rows()
        expanded = set()
        self.map_expanded_rows(
            lambda s, iter, data: expanded.add(model[iter][0]), None)
        needs_expanding = []
        for row in rows:
            if self.row_expanded(row):
                self.emit('test-expand-row', model.get_iter(row), row)
                self.expand_row(row, False)
                needs_expanding.append(row)
        while len(needs_expanding) > 0:
            child = model.iter_children(model.get_iter(needs_expanding.pop()))
            while child is not None:
                if model[child][0] in expanded:
                    path = model.get_path(child)
                    self.emit('test-expand-row', child, path)
                    self.expand_row(path, False)
                    needs_expanding.append(path)
                child = model.iter_next(child)

    def __expanded(self, iter, path, model):
        window = self.get_window()
        if window:
            window.set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))
            Gtk.main_iteration_do(False)
        try:
            try:
                if model is None:
                    return
                while model.iter_has_child(iter):
                    model.remove(model.iter_children(iter))
                folder = model[iter][0]
                for path in listdir(folder):
                    try:
                        if not os.path.isdir(path):
                            continue
                        for filename in listdir(path):
                            if os.path.isdir(filename):
                                niter = model.append(iter, [path])
                                model.append(niter, ["dummy"])
                                break
                        else:
                            model.append(iter, [path])
                    except OSError:
                        pass
                if not model.iter_has_child(iter):
                    return True
            except OSError:
                pass
        finally:
            if window:
                window.set_cursor(None)


class FileSelector(Paned):
    """A file selector widget consisting of a folder tree
    and a file list below.
    """

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_LAST, None,
                    (Gtk.TreeSelection,))
    }

    def __init__(self, initial=None, filter=filesel_filter, folders=None):
        """
        initial -- a path to a file which should be shown initially
        filter -- a function which filters paths shown in the file list
        folders -- list of shown folders in the directory tree
        """

        super().__init__(
            orientation=Gtk.Orientation.VERTICAL)
        self.__filter = filter

        if initial is not None:
            assert isinstance(initial, fsnative)

        if initial and os.path.isfile(initial):
            initial = os.path.dirname(initial)
        dirlist = DirectoryTree(initial, folders=folders)

        model = ObjectStore()
        filelist = AllTreeView(model=model)
        filelist.connect("draw", self.__restore_scroll_pos_on_draw)

        column = TreeViewColumn(title=_("Songs"))
        column.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
        render = Gtk.CellRendererPixbuf()
        render.props.xpad = 3

        def cell_icon(column, cell, model, iter_, userdata):
            value = model.get_value(iter_)
            if is_image(value):
                cell.set_property('icon-name', Icons.IMAGE_X_GENERIC)
            else:
                cell.set_property('icon-name', Icons.AUDIO_X_GENERIC)

        column.set_cell_data_func(render, cell_icon)

        column.pack_start(render, False)
        render = Gtk.CellRendererText()
        if filelist.supports_hints():
            render.set_property('ellipsize', Pango.EllipsizeMode.END)
        column.pack_start(render, True)

        def cell_data(column, cell, model, iter_, userdata):
            value = model.get_value(iter_)
            cell.set_property('text', fsn2text(os.path.basename(value)))

        column.set_cell_data_func(render, cell_data)

        filelist.append_column(column)
        filelist.set_rules_hint(True)
        filelist.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        filelist.set_search_equal_func(search_func, False)
        filelist.set_search_column(0)

        self.__sig = filelist.get_selection().connect(
            'changed', self.__changed)

        dirlist.get_selection().connect(
            'changed', self.__dir_selection_changed, filelist)
        dirlist.get_selection().emit('changed')

        def select_all_files(view, path, col, fileselection):
            view.expand_row(path, False)
            fileselection.select_all()

        dirlist.connect('row-activated', select_all_files,
                        filelist.get_selection())

        sw = ScrolledWindow()
        sw.add(dirlist)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)
        self.pack1(sw, resize=True)

        sw = ScrolledWindow()
        sw.add(filelist)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)
        self.pack2(sw, resize=True)

    def go_to(self, *args, **kwargs):
        dirlist = self.get_child1().get_child()
        dirlist.go_to(*args, **kwargs)

    def get_selected_paths(self):
        """A list of fs paths"""

        filelist = self.get_child2().get_child()
        selection = filelist.get_selection()
        model, paths = selection.get_selected_rows()
        return [model[p][0] for p in paths]

    def rescan(self):
        """Refill the file list for the current directory selection"""

        dirlist = self.get_child1().get_child()
        filelist = self.get_child2().get_child()

        dir_selection = dirlist.get_selection()
        self.__dir_selection_changed(dir_selection, filelist)

    def __changed(self, selection):
        # forward file list selection changed signals
        self.emit('changed', selection)

    def __dir_selection_changed(self, selection, filelist):
        # dir selection changed, refill the file list

        fselect = filelist.get_selection()
        fselect.handler_block(self.__sig)
        fmodel, frows = fselect.get_selected_rows()
        selected = [fmodel[row][0] for row in frows]

        fmodel = filelist.get_model()
        fmodel.clear()
        dmodel, rows = selection.get_selected_rows()
        dirs = [dmodel[row][0] for row in rows]
        for dir_ in dirs:
            try:
                files = filter(self.__filter, listdir(dir_))
                for file_ in sorted(files):
                    filename = os.path.join(dir_, file_)
                    if (os.access(filename, os.R_OK) and
                        not os.path.isdir(filename)):
                        fmodel.append([filename])
            except OSError:
                pass

        for iter_, filename in fmodel.iterrows():
            if filename in selected:
                fselect.select_iter(iter_)

        fselect.handler_unblock(self.__sig)
        fselect.emit('changed')
        self._saved_scroll_pos = filelist.get_vadjustment().get_value()

    def __restore_scroll_pos_on_draw(self, treeview, context):
        if self._saved_scroll_pos:
            vadj = treeview.get_vadjustment()
            vadj.set_value(self._saved_scroll_pos)
            self._saved_scroll_pos = None


def _get_main_folders():
    def filter_exists(paths):
        return [p for p in paths if os.path.isdir(p)]

    folders = []

    favs = filter_exists(get_favorites())
    if favs:
        folders += favs

    drives = filter_exists(get_drives())
    if folders and drives:
        folders += [None]
    if drives:
        folders += drives

    bookmarks = filter_exists(get_gtk_bookmarks())
    if folders and bookmarks:
        folders += [None]
    if bookmarks:
        folders += bookmarks

    return folders


class MainFileSelector(FileSelector):
    """The main file selector used in EF.

    Shows a useful list of directories in the directory tree.
    """

    def __init__(self, initial=None):
        folders = _get_main_folders()
        super().__init__(
            initial, filesel_filter, folders=folders)


class MainDirectoryTree(DirectoryTree):
    """The main directory tree used in QL.

    Shows a useful list of directories.
    """

    def __init__(self, initial=None, folders=None):
        if folders is None:
            folders = []

        main = _get_main_folders()
        if folders and main:
            folders += [None]
        if main:
            folders += main

        super().__init__(
            initial=initial, folders=folders)
