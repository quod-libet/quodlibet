# -*- coding: utf-8 -*-
# Copyright 2016 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

import os
from concurrent.futures import ProcessPoolExecutor

import pytest
import quodlibet

os.environ["PYFLAKES_NODOCTEST"] = "1"
os.environ["PYFLAKES_BUILTINS"] = \
    "unichr,unicode,long,basestring,xrange,cmp,execfile,reload"

try:
    from pyflakes.scripts import pyflakes
except ImportError:
    pyflakes = None

from tests import TestCase
from tests.helper import capture_output


def iter_py_files(root):
    for base, dirs, files in os.walk(root):
        for file_ in files:
            path = os.path.join(base, file_)
            if os.path.splitext(path)[1] == ".py":
                yield path


def _check_file(f):
    with capture_output() as (o, e):
        pyflakes.checkPath(f)
    return o.getvalue().splitlines()


def check_files(files, ignore=[]):
    lines = []
    with ProcessPoolExecutor(None) as pool:
        for res in pool.map(_check_file, files):
            lines.extend(res)
    return sorted(lines)


@pytest.mark.quality
class TPyFlakes(TestCase):

    def test_all(self):
        assert pyflakes is not None, "pyflakes is missing"

        files = iter_py_files(
            os.path.dirname(os.path.abspath(quodlibet.__path__[0])))
        errors = check_files(files)
        if errors:
            raise Exception("\n".join(errors))
