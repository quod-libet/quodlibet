# Copyright 2013 Christoph Reiter
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from senf import fsnative

from tests import TestCase

import quodlibet
from quodlibet import config
from quodlibet.const import Version


class TQuodlibet(TestCase):

    def setUp(self):
        config.init()

    def tearDown(self):
        config.quit()

    def test_first_session(self):
        self.assertTrue(quodlibet.is_first_session("quodlibet"))
        self.assertTrue(quodlibet.is_first_session("quodlibet"))
        quodlibet.finish_first_session("exfalso")
        self.assertTrue(quodlibet.is_first_session("quodlibet"))
        quodlibet.finish_first_session("quodlibet")
        self.assertFalse(quodlibet.is_first_session("quodlibet"))

    def test_dirs(self):
        self.assertTrue(isinstance(quodlibet.get_base_dir(), fsnative))
        self.assertTrue(isinstance(quodlibet.get_image_dir(), fsnative))
        self.assertTrue(isinstance(quodlibet.get_config_dir(), fsnative))
        self.assertTrue(isinstance(quodlibet.get_cache_dir(), fsnative))
        self.assertTrue(isinstance(quodlibet.get_data_dir(), fsnative))

    def test_get_build_description(self):
        quodlibet.get_build_description()

    def test_get_build_version(self):
        ver = quodlibet.get_build_version()
        self.assertTrue(isinstance(ver, tuple))


class TVersion(TestCase):

    def test_message(self):
        v = Version("foo", 1, 2, message="bla")
        self.assertRaises(ImportError, v.check, (1, 1))
        try:
            v.check((1, 1))
        except ImportError as e:
            self.assertTrue("bla" in str(e))
