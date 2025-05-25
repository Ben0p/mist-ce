# Licensed to the Apache Software Foundation (ASF) under one or more§
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys
import logging
import tempfile
from unittest import mock
from unittest.mock import patch

import pytest

import libcloud
from libcloud import _init_once, reset_debug
from libcloud.base import DriverTypeNotFoundError
from libcloud.test import unittest
from libcloud.common.base import Connection, LibcloudConnection
from libcloud.utils.loggingconnection import LoggingConnection

try:
    import paramiko  # NOQA

    have_paramiko = True
except ImportError:
    have_paramiko = False

_, TEMP_LOGFILE_PATH = tempfile.mkstemp()


class TestUtils(unittest.TestCase):
    def setUp(self):
        # Reset debug level
        reset_debug()

        # Reset paramiko log level and handlers
        if have_paramiko:
            paramiko_logger = logging.getLogger("paramiko")
            paramiko_logger.handlers = []
            paramiko_logger.setLevel(logging.INFO)

    @mock.patch.dict(os.environ, {"LIBCLOUD_DEBUG": ""}, clear=True)
    @pytest.mark.serial
    def test_init_once_and_no_debug_mode(self):
        if have_paramiko:
            paramiko_logger = logging.getLogger("paramiko")
            paramiko_log_level = paramiko_logger.getEffectiveLevel()
            self.assertEqual(paramiko_log_level, logging.INFO)

        self.assertIsNone(LoggingConnection.log)
        self.assertEqual(Connection.conn_class, LibcloudConnection)

        # Debug mode is disabled
        _init_once()

        self.assertIsNone(LoggingConnection.log)
        self.assertEqual(Connection.conn_class, LibcloudConnection)

        if have_paramiko:
            paramiko_log_level = paramiko_logger.getEffectiveLevel()
            self.assertEqual(paramiko_log_level, logging.INFO)

    @mock.patch.dict(os.environ, {"LIBCLOUD_DEBUG": TEMP_LOGFILE_PATH}, clear=True)
    @pytest.mark.serial
    def test_init_once_and_debug_mode(self):
        if have_paramiko:
            paramiko_logger = logging.getLogger("paramiko")
            paramiko_log_level = paramiko_logger.getEffectiveLevel()
            self.assertEqual(paramiko_log_level, logging.INFO)

        self.assertIsNone(LoggingConnection.log)
        self.assertEqual(Connection.conn_class, LibcloudConnection)

        # Debug mode is enabled
        _init_once()

        self.assertTrue(LoggingConnection.log is not None)
        self.assertEqual(Connection.conn_class, LoggingConnection)

        if have_paramiko:
            paramiko_log_level = paramiko_logger.getEffectiveLevel()
            self.assertEqual(paramiko_log_level, logging.DEBUG)

    def test_factory(self):
        driver = libcloud.get_driver(libcloud.DriverType.COMPUTE, libcloud.DriverType.COMPUTE.EC2)
        self.assertEqual(driver.__name__, "EC2NodeDriver")

    def test_raises_error(self):
        with self.assertRaises(DriverTypeNotFoundError):
            libcloud.get_driver("potato", "potato")

    @patch.object(libcloud.requests, "__version__", "2.6.0")
    @patch.object(libcloud.requests.packages.chardet, "__version__", "2.2.1")
    def test_init_once_detects_bad_yum_install_requests(self, *args):
        expected_msg = "Known bad version of requests detected"
        with self.assertRaisesRegex(AssertionError, expected_msg):
            _init_once()

    @patch.object(libcloud.requests, "__version__", "2.6.0")
    @patch.object(libcloud.requests.packages.chardet, "__version__", "2.3.0")
    def test_init_once_correct_chardet_version(self, *args):
        _init_once()


if __name__ == "__main__":
    sys.exit(unittest.main())
