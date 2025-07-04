# Licensed to the Apache Software Foundation (ASF) under one or more
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
import copy
import hmac
import math
import hashlib
import os.path  # pylint: disable-msg=W0404
from io import BytesIO
from hashlib import sha1
from unittest import mock
from unittest.mock import Mock, PropertyMock

import libcloud.utils.files
from libcloud.test import MockHttp  # pylint: disable-msg=E0611
from libcloud.test import unittest, make_response, generate_random_data
from libcloud.utils.py3 import StringIO, b, httplib, urlquote
from libcloud.utils.files import exhaust_iterator
from libcloud.common.types import MalformedResponseError
from libcloud.storage.base import CHUNK_SIZE, Object, Container
from libcloud.storage.types import (
    ObjectDoesNotExistError,
    ObjectHashMismatchError,
    ContainerIsNotEmptyError,
    InvalidContainerNameError,
    ContainerDoesNotExistError,
    ContainerAlreadyExistsError,
)
from libcloud.test.storage.base import BaseRangeDownloadMockHttp
from libcloud.test.file_fixtures import StorageFileFixtures  # pylint: disable-msg=E0611
from libcloud.storage.drivers.cloudfiles import CloudFilesStorageDriver


class CloudFilesTests(unittest.TestCase):
    driver_klass = CloudFilesStorageDriver
    driver_args = ("dummy", "dummy")
    driver_kwargs = {}
    region = "ord"

    def setUp(self):
        self.driver_klass.connectionCls.conn_class = CloudFilesMockHttp
        CloudFilesMockHttp.type = None

        driver_kwargs = self.driver_kwargs.copy()
        driver_kwargs["region"] = self.region
        self.driver = self.driver_klass(*self.driver_args, **driver_kwargs)

        # normally authentication happens lazily, but we force it here
        self.driver.connection._populate_hosts_and_request_paths()
        self._remove_test_file()

    def tearDown(self):
        self._remove_test_file()

    def test_invalid_ex_force_service_region(self):
        driver = CloudFilesStorageDriver("driver", "dummy", ex_force_service_region="invalid")

        try:
            driver.list_containers()
        except Exception as e:
            self.assertEqual(e.value, "Could not find specified endpoint")
        else:
            self.fail("Exception was not thrown")

    def test_ex_force_service_region(self):
        driver = CloudFilesStorageDriver("driver", "dummy", ex_force_service_region="ORD")
        driver.list_containers()

    def test_force_auth_token_kwargs(self):
        base_url = "https://cdn2.clouddrive.com/v1/MossoCloudFS"
        kwargs = {
            "ex_force_auth_token": "some-auth-token",
            "ex_force_base_url": base_url,
        }
        driver = CloudFilesStorageDriver("driver", "dummy", **kwargs)
        driver.list_containers()

        self.assertEqual(kwargs["ex_force_auth_token"], driver.connection.auth_token)
        self.assertEqual("cdn2.clouddrive.com", driver.connection.host)
        self.assertEqual("/v1/MossoCloudFS", driver.connection.request_path)

    def test_force_auth_url_kwargs(self):
        kwargs = {
            "ex_force_auth_version": "2.0",
            "ex_force_auth_url": "https://identity.api.rackspace.com",
        }
        driver = CloudFilesStorageDriver("driver", "dummy", **kwargs)

        self.assertEqual(kwargs["ex_force_auth_url"], driver.connection._ex_force_auth_url)
        self.assertEqual(kwargs["ex_force_auth_version"], driver.connection._auth_version)

    def test_invalid_json_throws_exception(self):
        CloudFilesMockHttp.type = "MALFORMED_JSON"
        try:
            self.driver.list_containers()
        except MalformedResponseError:
            pass
        else:
            self.fail("Exception was not thrown")

    def test_service_catalog(self):
        url = "https://storage4.%s1.clouddrive.com/v1/MossoCloudFS" % (self.region)
        self.assertEqual(url, self.driver.connection.get_endpoint())

        self.driver.connection.cdn_request = True
        self.assertEqual(
            "https://cdn.clouddrive.com/v1/MossoCloudFS",
            self.driver.connection.get_endpoint(),
        )
        self.driver.connection.cdn_request = False

    def test_get_endpoint_internalurl(self):
        self.driver.connection.use_internal_url = True
        url = (
            "https://snet-storage101.%s1.clouddrive.com/v1/MossoCloudFS_11111-111111111-1111111111-1111111"
            % (self.region)
        )
        self.assertEqual(url, self.driver.connection.get_endpoint())

    def test_list_containers(self):
        CloudFilesMockHttp.type = "EMPTY"
        containers = self.driver.list_containers()
        self.assertEqual(len(containers), 0)

        CloudFilesMockHttp.type = None
        containers = self.driver.list_containers()
        self.assertEqual(len(containers), 3)

        container = [c for c in containers if c.name == "container2"][0]
        self.assertEqual(container.extra["object_count"], 120)
        self.assertEqual(container.extra["size"], 340084450)

    def test_list_container_objects(self):
        CloudFilesMockHttp.type = "EMPTY"
        container = Container(name="test_container", extra={}, driver=self.driver)
        objects = self.driver.list_container_objects(container=container)
        self.assertEqual(len(objects), 0)

        CloudFilesMockHttp.type = None
        objects = self.driver.list_container_objects(container=container)
        self.assertEqual(len(objects), 4)

        obj = [o for o in objects if o.name == "foo test 1"][0]
        self.assertEqual(obj.hash, "16265549b5bda64ecdaa5156de4c97cc")
        self.assertEqual(obj.size, 1160520)
        self.assertEqual(obj.container.name, "test_container")

    def test_list_container_object_name_encoding(self):
        CloudFilesMockHttp.type = "EMPTY"
        container = Container(name="test container 1", extra={}, driver=self.driver)
        objects = self.driver.list_container_objects(container=container)
        self.assertEqual(len(objects), 0)

    def test_list_container_objects_with_prefix(self):
        CloudFilesMockHttp.type = "EMPTY"
        container = Container(name="test_container", extra={}, driver=self.driver)
        objects = self.driver.list_container_objects(container=container, prefix="test_prefix1")
        self.assertEqual(len(objects), 0)

        CloudFilesMockHttp.type = None
        objects = self.driver.list_container_objects(container=container, prefix="test_prefix2")
        self.assertEqual(len(objects), 4)

        obj = [o for o in objects if o.name == "foo test 1"][0]
        self.assertEqual(obj.hash, "16265549b5bda64ecdaa5156de4c97cc")
        self.assertEqual(obj.size, 1160520)
        self.assertEqual(obj.container.name, "test_container")

    def test_list_container_objects_iterator(self):
        CloudFilesMockHttp.type = "ITERATOR"
        container = Container(name="test_container", extra={}, driver=self.driver)
        objects = self.driver.list_container_objects(container=container)
        self.assertEqual(len(objects), 5)

        obj = [o for o in objects if o.name == "foo-test-1"][0]
        self.assertEqual(obj.hash, "16265549b5bda64ecdaa5156de4c97cc")
        self.assertEqual(obj.size, 1160520)
        self.assertEqual(obj.container.name, "test_container")

    def test_get_container(self):
        container = self.driver.get_container(container_name="test_container")
        self.assertEqual(container.name, "test_container")
        self.assertEqual(container.extra["object_count"], 800)
        self.assertEqual(container.extra["size"], 1234568)

    def test_get_container_not_found(self):
        try:
            self.driver.get_container(container_name="not_found")
        except ContainerDoesNotExistError:
            pass
        else:
            self.fail("Exception was not thrown")

    def test_get_object_success(self):
        obj = self.driver.get_object(container_name="test_container", object_name="test_object")
        self.assertEqual(obj.container.name, "test_container")
        self.assertEqual(obj.size, 555)
        self.assertEqual(obj.hash, "6b21c4a111ac178feacf9ec9d0c71f17")
        self.assertEqual(obj.extra["content_type"], "application/zip")
        self.assertEqual(obj.extra["last_modified"], "Tue, 25 Jan 2011 22:01:49 GMT")
        self.assertEqual(obj.meta_data["foo-bar"], "test 1")
        self.assertEqual(obj.meta_data["bar-foo"], "test 2")

    def test_get_object_object_name_encoding(self):
        obj = self.driver.get_object(container_name="test_container", object_name="~/test_object/")
        self.assertEqual(obj.name, "~/test_object/")

    def test_get_object_not_found(self):
        try:
            self.driver.get_object(container_name="test_container", object_name="not_found")
        except ObjectDoesNotExistError:
            pass
        else:
            self.fail("Exception was not thrown")

    def test_create_container_success(self):
        container = self.driver.create_container(container_name="test_create_container")
        self.assertTrue(isinstance(container, Container))
        self.assertEqual(container.name, "test_create_container")
        self.assertEqual(container.extra["object_count"], 0)

    def test_create_container_already_exists(self):
        CloudFilesMockHttp.type = "ALREADY_EXISTS"

        try:
            self.driver.create_container(container_name="test_create_container")
        except ContainerAlreadyExistsError:
            pass
        else:
            self.fail("Container already exists but an exception was not thrown")

    def test_create_container_invalid_name_too_long(self):
        name = "".join(["x" for x in range(0, 257)])
        try:
            self.driver.create_container(container_name=name)
        except InvalidContainerNameError:
            pass
        else:
            self.fail(
                "Invalid name was provided (name is too long)" ", but exception was not thrown"
            )

    def test_create_container_invalid_name_slashes_in_name(self):
        try:
            self.driver.create_container(container_name="test/slashes/")
        except InvalidContainerNameError:
            pass
        else:
            self.fail(
                "Invalid name was provided (name contains slashes)" ", but exception was not thrown"
            )

    def test_delete_container_success(self):
        container = Container(name="foo_bar_container", extra={}, driver=self)
        result = self.driver.delete_container(container=container)
        self.assertTrue(result)

    def test_delete_container_not_found(self):
        CloudFilesMockHttp.type = "NOT_FOUND"
        container = Container(name="foo_bar_container", extra={}, driver=self)
        try:
            self.driver.delete_container(container=container)
        except ContainerDoesNotExistError:
            pass
        else:
            self.fail("Container does not exist but an exception was not thrown")

    def test_delete_container_not_empty(self):
        CloudFilesMockHttp.type = "NOT_EMPTY"
        container = Container(name="foo_bar_container", extra={}, driver=self)
        try:
            self.driver.delete_container(container=container)
        except ContainerIsNotEmptyError:
            pass
        else:
            self.fail("Container is not empty but an exception was not thrown")

    def test_download_object_success(self):
        container = Container(name="foo_bar_container", extra={}, driver=self)
        obj = Object(
            name="foo_bar_object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=CloudFilesStorageDriver,
        )
        destination_path = os.path.abspath(__file__) + ".temp"
        result = self.driver.download_object(
            obj=obj,
            destination_path=destination_path,
            overwrite_existing=False,
            delete_on_failure=True,
        )
        self.assertTrue(result)

    def test_download_object_invalid_file_size(self):
        CloudFilesMockHttp.type = "INVALID_SIZE"
        container = Container(name="foo_bar_container", extra={}, driver=self)
        obj = Object(
            name="foo_bar_object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=CloudFilesStorageDriver,
        )
        destination_path = os.path.abspath(__file__) + ".temp"
        result = self.driver.download_object(
            obj=obj,
            destination_path=destination_path,
            overwrite_existing=False,
            delete_on_failure=True,
        )
        self.assertFalse(result)

    def test_download_object_success_not_found(self):
        CloudFilesMockHttp.type = "NOT_FOUND"
        container = Container(name="foo_bar_container", extra={}, driver=self)

        obj = Object(
            name="foo_bar_object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=CloudFilesStorageDriver,
        )
        destination_path = os.path.abspath(__file__) + ".temp"
        try:
            self.driver.download_object(
                obj=obj,
                destination_path=destination_path,
                overwrite_existing=False,
                delete_on_failure=True,
            )
        except ObjectDoesNotExistError:
            pass
        else:
            self.fail("Object does not exist but an exception was not thrown")

    def test_download_object_range_success(self):
        container = Container(name="foo_bar_container", extra={}, driver=self)
        obj = Object(
            name="foo_bar_object_range",
            size=10,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=CloudFilesStorageDriver,
        )
        destination_path = os.path.abspath(__file__) + ".temp"
        result = self.driver.download_object_range(
            obj=obj,
            destination_path=destination_path,
            start_bytes=5,
            end_bytes=7,
            overwrite_existing=False,
            delete_on_failure=True,
        )
        self.assertTrue(result)

        with open(destination_path) as fp:
            content = fp.read()

        self.assertEqual(content, "56")

    def test_download_object_as_stream(self):
        container = Container(name="foo_bar_container", extra={}, driver=self)
        obj = Object(
            name="foo_bar_object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=CloudFilesStorageDriver,
        )

        stream = self.driver.download_object_as_stream(obj=obj, chunk_size=None)
        self.assertTrue(hasattr(stream, "__iter__"))

    def test_download_object_as_stream_data_is_not_buffered_in_memory(self):
        # Test case which verifies that response.response attribute is not accessed
        # and as such, whole body response is not buffered into RAM

        # If content is consumed and response.content attribute accessed exception
        # will be thrown and test will fail
        mock_response = Mock(name="mock response")
        mock_response.headers = {}
        mock_response.status = 200
        msg1 = '"response" attribute was accessed but it shouldn\'t have been'
        msg2 = '"content" attribute was accessed but it shouldn\'t have been'
        type(mock_response).response = PropertyMock(
            name="mock response attribute", side_effect=Exception(msg1)
        )
        type(mock_response).content = PropertyMock(
            name="mock content attribute", side_effect=Exception(msg2)
        )
        mock_response.iter_content.return_value = StringIO("a" * 1000)

        self.driver.connection.request = Mock()
        self.driver.connection.request.return_value = mock_response

        container = Container(name="foo_bar_container", extra={}, driver=self.driver)
        obj = Object(
            name="foo_bar_object_NO_BUFFER",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=self.driver,
        )
        result = self.driver.download_object_as_stream(obj=obj)
        result = exhaust_iterator(result)
        result = result.decode("utf-8")

        self.assertEqual(result, "a" * 1000)

    def test_download_object_range_as_stream_success(self):
        container = Container(name="foo_bar_container", extra={}, driver=self)
        obj = Object(
            name="foo_bar_object_range",
            size=2,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=CloudFilesStorageDriver,
        )

        stream = self.driver.download_object_range_as_stream(
            start_bytes=5, end_bytes=7, obj=obj, chunk_size=None
        )
        self.assertTrue(hasattr(stream, "__iter__"))
        consumed_stream = "".join(chunk.decode("utf-8") for chunk in stream)
        self.assertEqual(consumed_stream, "56")
        self.assertEqual(len(consumed_stream), obj.size)

    def test_upload_object_success(self):
        def upload_file(
            self,
            object_name=None,
            content_type=None,
            request_path=None,
            request_method=None,
            headers=None,
            file_path=None,
            stream=None,
        ):
            return {
                "response": make_response(
                    201, headers={"etag": "0cc175b9c0f1b6a831c399e269772661"}
                ),
                "bytes_transferred": 1000,
                "data_hash": "0cc175b9c0f1b6a831c399e269772661",
            }

        old_func = CloudFilesStorageDriver._upload_object
        CloudFilesStorageDriver._upload_object = upload_file
        file_path = os.path.abspath(__file__)
        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "foo_test_upload"
        extra = {"meta_data": {"some-value": "foobar"}}
        obj = self.driver.upload_object(
            file_path=file_path,
            container=container,
            extra=extra,
            object_name=object_name,
        )
        self.assertEqual(obj.name, "foo_test_upload")
        self.assertEqual(obj.size, 1000)
        self.assertTrue("some-value" in obj.meta_data)
        CloudFilesStorageDriver._upload_object = old_func

    def test_upload_object_zero_size_object(self):
        def upload_file(
            self,
            object_name=None,
            content_type=None,
            request_path=None,
            request_method=None,
            headers=None,
            file_path=None,
            stream=None,
        ):
            return {
                "response": make_response(
                    201, headers={"etag": "0cc175b9c0f1b6a831c399e269772661"}
                ),
                "bytes_transferred": 0,
                "data_hash": "0cc175b9c0f1b6a831c399e269772661",
            }

        old_func = CloudFilesStorageDriver._upload_object
        CloudFilesStorageDriver._upload_object = upload_file

        old_request = self.driver.connection.request

        file_path = os.path.join(os.path.dirname(__file__), "__init__.py")
        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "empty"
        extra = {}

        def func(*args, **kwargs):
            self.assertEqual(kwargs["headers"]["Content-Length"], 0)
            func.called = True

            return old_request(*args, **kwargs)

        self.driver.connection.request = func
        func.called = False
        obj = self.driver.upload_object(
            file_path=file_path,
            container=container,
            extra=extra,
            object_name=object_name,
        )
        self.assertEqual(obj.name, "empty")
        self.assertEqual(obj.size, 0)
        CloudFilesStorageDriver._upload_object = old_func
        self.driver.connection.request = old_request

    def test_upload_object_invalid_hash(self):
        CloudFilesMockHttp.type = "INVALID_HASH"

        def upload_file(
            self,
            object_name=None,
            content_type=None,
            request_path=None,
            request_method=None,
            headers=None,
            file_path=None,
            stream=None,
        ):
            return {
                "response": make_response(
                    201, headers={"etag": "0cc175b9c0f1b6a831c399e269772661"}
                ),
                "bytes_transferred": 1000,
                "data_hash": "blah blah",
            }

        old_func = CloudFilesStorageDriver._upload_object
        CloudFilesStorageDriver._upload_object = upload_file
        file_path = os.path.abspath(__file__)
        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "foo_test_upload"
        try:
            self.driver.upload_object(
                file_path=file_path,
                container=container,
                object_name=object_name,
                verify_hash=True,
            )
        except ObjectHashMismatchError:
            pass
        else:
            self.fail("Invalid hash was returned but an exception was not thrown")
        finally:
            CloudFilesStorageDriver._upload_object = old_func

    def test_upload_object_no_content_type(self):
        def no_content_type(name):
            return None, None

        old_func = libcloud.utils.files.guess_file_mime_type
        libcloud.utils.files.guess_file_mime_type = no_content_type
        file_path = os.path.abspath(__file__)
        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "foo_test_upload"

        obj = self.driver.upload_object(
            file_path=file_path,
            verify_hash=False,
            container=container,
            object_name=object_name,
        )

        self.assertEqual(obj.name, object_name)
        libcloud.utils.files.guess_file_mime_type = old_func

    def test_upload_object_inexistent_file(self):
        def dummy_content_type(name):
            return "application/zip", None

        old_func = libcloud.utils.files.guess_file_mime_type
        libcloud.utils.files.guess_file_mime_type = dummy_content_type

        file_path = os.path.abspath(__file__ + ".inexistent")
        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "foo_test_upload"
        try:
            self.driver.upload_object(
                file_path=file_path, container=container, object_name=object_name
            )
        except OSError:
            pass
        else:
            self.fail("Inexistent but an exception was not thrown")
        finally:
            libcloud.utils.files.guess_file_mime_type = old_func

    def test_upload_object_via_stream(self):
        def dummy_content_type(name):
            return "application/zip", None

        old_func = libcloud.utils.files.guess_file_mime_type
        libcloud.utils.files.guess_file_mime_type = dummy_content_type

        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "foo_test_stream_data"
        iterator = BytesIO(b("235"))
        try:
            self.driver.upload_object_via_stream(
                container=container, object_name=object_name, iterator=iterator
            )
        finally:
            libcloud.utils.files.guess_file_mime_type = old_func

    def test_upload_object_via_stream_stream_seek_at_end(self):
        def dummy_content_type(name):
            return "application/zip", None

        old_func = libcloud.utils.files.guess_file_mime_type
        libcloud.utils.files.guess_file_mime_type = dummy_content_type

        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "foo_test_stream_data_seek"
        iterator = BytesIO(b("123456789"))
        iterator.seek(10)

        self.assertEqual(iterator.tell(), 10)

        try:
            self.driver.upload_object_via_stream(
                container=container, object_name=object_name, iterator=iterator
            )
        finally:
            libcloud.utils.files.guess_file_mime_type = old_func

    def test_delete_object_success(self):
        container = Container(name="foo_bar_container", extra={}, driver=self)
        obj = Object(
            name="foo_bar_object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=CloudFilesStorageDriver,
        )
        status = self.driver.delete_object(obj=obj)
        self.assertTrue(status)

    def test_delete_object_not_found(self):
        CloudFilesMockHttp.type = "NOT_FOUND"
        container = Container(name="foo_bar_container", extra={}, driver=self)
        obj = Object(
            name="foo_bar_object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=CloudFilesStorageDriver,
        )
        try:
            self.driver.delete_object(obj=obj)
        except ObjectDoesNotExistError:
            pass
        else:
            self.fail("Object does not exist but an exception was not thrown")

    def test_ex_get_meta_data(self):
        meta_data = self.driver.ex_get_meta_data()
        self.assertTrue(isinstance(meta_data, dict))
        self.assertTrue("object_count" in meta_data)
        self.assertTrue("container_count" in meta_data)
        self.assertTrue("bytes_used" in meta_data)
        self.assertTrue("temp_url_key" in meta_data)

    def test_ex_purge_object_from_cdn(self):
        CloudFilesMockHttp.type = "PURGE_SUCCESS"
        container = Container(name="foo_bar_container", extra={}, driver=self.driver)
        obj = Object(
            name="object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=self,
        )

        self.assertTrue(self.driver.ex_purge_object_from_cdn(obj=obj))

    def test_ex_purge_object_from_cdn_with_email(self):
        CloudFilesMockHttp.type = "PURGE_SUCCESS_EMAIL"
        container = Container(name="foo_bar_container", extra={}, driver=self.driver)
        obj = Object(
            name="object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=self,
        )
        self.assertTrue(self.driver.ex_purge_object_from_cdn(obj=obj, email="test@test.com"))

    @mock.patch("os.path.getsize")
    def test_ex_multipart_upload_object_for_small_files(self, getsize_mock):
        getsize_mock.return_value = 0

        old_func = CloudFilesStorageDriver.upload_object
        mocked_upload_object = mock.Mock(return_value="test")
        CloudFilesStorageDriver.upload_object = mocked_upload_object

        file_path = os.path.abspath(__file__)
        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "foo_test_upload"
        obj = self.driver.ex_multipart_upload_object(
            file_path=file_path, container=container, object_name=object_name
        )
        CloudFilesStorageDriver.upload_object = old_func

        self.assertTrue(mocked_upload_object.called)
        self.assertEqual(obj, "test")

    def test_ex_multipart_upload_object_success(self):
        _upload_object_part = CloudFilesStorageDriver._upload_object_part
        _upload_object_manifest = CloudFilesStorageDriver._upload_object_manifest

        mocked__upload_object_part = mock.Mock(return_value="test_part")
        mocked__upload_object_manifest = mock.Mock(return_value="test_manifest")

        CloudFilesStorageDriver._upload_object_part = mocked__upload_object_part
        CloudFilesStorageDriver._upload_object_manifest = mocked__upload_object_manifest

        parts = 5
        file_path = os.path.abspath(__file__)
        chunk_size = int(math.ceil(float(os.path.getsize(file_path)) / parts))
        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "foo_test_upload"
        self.driver.ex_multipart_upload_object(
            file_path=file_path,
            container=container,
            object_name=object_name,
            chunk_size=chunk_size,
        )

        CloudFilesStorageDriver._upload_object_part = _upload_object_part
        CloudFilesStorageDriver._upload_object_manifest = _upload_object_manifest

        self.assertEqual(mocked__upload_object_part.call_count, parts)
        self.assertTrue(mocked__upload_object_manifest.call_count, 1)

    def test__upload_object_part(self):
        _put_object = CloudFilesStorageDriver._put_object
        mocked__put_object = mock.Mock(return_value="test")
        CloudFilesStorageDriver._put_object = mocked__put_object

        part_number = 7
        object_name = "test_object"
        expected_name = object_name + "/%08d" % part_number
        container = Container(name="foo_bar_container", extra={}, driver=self)

        self.driver._upload_object_part(container, object_name, part_number, None)

        CloudFilesStorageDriver._put_object = _put_object

        func_kwargs = tuple(mocked__put_object.call_args)[1]
        self.assertEqual(func_kwargs["object_name"], expected_name)
        self.assertEqual(func_kwargs["container"], container)

    def test_upload_object_via_stream_with_cors_headers(self):
        """
        Test we can add some ``Cross-origin resource sharing`` headers
        to the request about to be sent.
        """
        cors_headers = {
            "Access-Control-Allow-Origin": "http://mozilla.com",
            "Origin": "http://storage.clouddrive.com",
        }
        expected_headers = {
            # Automatically added headers
            "Content-Type": "application/octet-stream"
        }
        expected_headers.update(cors_headers)

        def intercept_request(request_path, method=None, data=None, headers=None, raw=True):
            # What we're actually testing
            self.assertDictEqual(expected_headers, headers)

            raise NotImplementedError("oops")

        self.driver.connection.request = intercept_request

        container = Container(name="CORS", extra={}, driver=self.driver)

        try:
            self.driver.upload_object_via_stream(
                iterator=iter(b"blob data like an image or video"),
                container=container,
                object_name="test_object",
                headers=cors_headers,
            )
        except NotImplementedError:
            # Don't care about the response we'd have to mock anyway
            # as long as we intercepted the request and checked its headers
            pass
        else:
            self.fail(
                "Expected NotImplementedError to be thrown to "
                "verify we actually checked the expected headers"
            )

    def test_upload_object_via_stream_python3_bytes_error(self):
        container = Container(name="py3", extra={}, driver=self.driver)
        bytes_blob = b"blob data like an image or video"

        # This is mostly to check we didn't discover other errors along the way
        mocked_response = container.upload_object_via_stream(
            iterator=iter(bytes_blob),
            object_name="img_or_vid",
        )
        self.assertEqual(len(bytes_blob), mocked_response.size)

    @unittest.skip("Skipping as chunking is disabled in 2.0rc1")
    def test_upload_object_via_stream_chunked_encoding(self):
        # Create enough bytes it should get split into two chunks
        bytes_blob = "".join(["\0" for _ in range(CHUNK_SIZE + 1)])
        hex_chunk_size = ("%X" % CHUNK_SIZE).encode("utf8")
        expected = [
            # Chunk 1
            hex_chunk_size + b"\r\n",
            bytes(bytes_blob[:CHUNK_SIZE].encode("utf8")),
            b"\r\n",
            # Chunk 2
            b"1\r\n",
            bytes(bytes_blob[CHUNK_SIZE:].encode("utf8")),
            b"\r\n",
            # If chunked, also send a final message
            b"0\r\n\r\n",
        ]
        logged_data = []

        class InterceptResponse(MockHttp):
            def __init__(self, connection, response=None):
                super().__init__(connection=connection, response=response)
                old_send = self.connection.connection.send

                def intercept_send(data):
                    old_send(data)
                    logged_data.append(data)

                self.connection.connection.send = intercept_send

            def _v1_MossoCloudFS_py3_img_or_vid2(self, method, url, body, headers):
                headers = {"etag": "d79fb00c27b50494a463e680d459c90c"}
                headers.update(self.base_headers)
                _201 = httplib.CREATED

                return _201, "", headers, httplib.responses[_201]

        self.driver_klass.connectionCls.rawResponseCls = InterceptResponse

        container = Container(name="py3", extra={}, driver=self.driver)
        container.upload_object_via_stream(
            iterator=iter(bytes_blob),
            object_name="img_or_vid2",
        )
        self.assertListEqual(expected, logged_data)

    def test__upload_object_manifest(self):
        hash_function = self.driver._get_hash_function()
        hash_function.update(b(""))
        data_hash = hash_function.hexdigest()

        fake_response = type("CloudFilesResponse", (), {"headers": {"etag": data_hash}})

        _request = self.driver.connection.request
        mocked_request = mock.Mock(return_value=fake_response)
        self.driver.connection.request = mocked_request

        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "test_object"

        self.driver._upload_object_manifest(container, object_name)

        func_args, func_kwargs = tuple(mocked_request.call_args)

        self.driver.connection.request = _request

        self.assertEqual(func_args[0], "/" + container.name + "/" + object_name)
        self.assertEqual(
            func_kwargs["headers"]["X-Object-Manifest"],
            container.name + "/" + object_name + "/",
        )
        self.assertEqual(func_kwargs["method"], "PUT")

    def test__upload_object_manifest_wrong_hash(self):
        fake_response = type("CloudFilesResponse", (), {"headers": {"etag": "0000000"}})

        _request = self.driver.connection.request
        mocked_request = mock.Mock(return_value=fake_response)
        self.driver.connection.request = mocked_request

        container = Container(name="foo_bar_container", extra={}, driver=self)
        object_name = "test_object"

        try:
            self.driver._upload_object_manifest(container, object_name)
        except ObjectHashMismatchError:
            pass
        else:
            self.fail("Exception was not thrown")
        finally:
            self.driver.connection.request = _request

    def test_create_container_put_object_name_encoding(self):
        def upload_file(
            self,
            object_name=None,
            content_type=None,
            request_path=None,
            request_method=None,
            headers=None,
            file_path=None,
            stream=None,
        ):
            return {
                "response": make_response(
                    201, headers={"etag": "0cc175b9c0f1b6a831c399e269772661"}
                ),
                "bytes_transferred": 1000,
                "data_hash": "0cc175b9c0f1b6a831c399e269772661",
            }

        old_func = CloudFilesStorageDriver._upload_object
        CloudFilesStorageDriver._upload_object = upload_file

        container_name = "speci@l_name"
        object_name = "m@obj€ct"
        file_path = os.path.abspath(__file__)

        container = self.driver.create_container(container_name=container_name)
        self.assertEqual(container.name, container_name)

        obj = self.driver.upload_object(
            file_path=file_path, container=container, object_name=object_name
        )
        self.assertEqual(obj.name, object_name)
        CloudFilesStorageDriver._upload_object = old_func

    def test_ex_enable_static_website(self):
        container = Container(name="foo_bar_container", extra={}, driver=self)
        result = self.driver.ex_enable_static_website(container=container, index_file="index.html")
        self.assertTrue(result)

    def test_ex_set_error_page(self):
        container = Container(name="foo_bar_container", extra={}, driver=self)
        result = self.driver.ex_set_error_page(container=container, file_name="error.html")
        self.assertTrue(result)

    def test_ex_set_account_metadata_temp_url_key(self):
        result = self.driver.ex_set_account_metadata_temp_url_key("a key")
        self.assertTrue(result)

    @mock.patch("libcloud.storage.drivers.cloudfiles.time")
    def test_ex_get_object_temp_url(self, time):
        time.return_value = 0
        self.driver.ex_get_meta_data = mock.Mock()
        self.driver.ex_get_meta_data.return_value = {
            "container_count": 1,
            "object_count": 1,
            "bytes_used": 1,
            "temp_url_key": "foo",
        }
        container = Container(name="foo_bar_container", extra={}, driver=self)
        obj = Object(
            name="foo_bar_object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=self,
        )
        hmac_body = "{}\n{}\n{}".format(
            "GET",
            60,
            "/v1/MossoCloudFS/foo_bar_container/foo_bar_object",
        )
        sig = hmac.new(b("foo"), b(hmac_body), sha1).hexdigest()
        ret = self.driver.ex_get_object_temp_url(obj, "GET")
        temp_url = (
            "https://storage4.%s1.clouddrive.com/v1/MossoCloudFS/"
            "foo_bar_container/foo_bar_object?temp_url_expires=60&temp_url_sig=%s"
            % (self.region, sig)
        )

        self.assertEqual("".join(sorted(ret)), "".join(sorted(temp_url)))

    def test_ex_get_object_temp_url_no_key_raises_key_error(self):
        self.driver.ex_get_meta_data = mock.Mock()
        self.driver.ex_get_meta_data.return_value = {
            "container_count": 1,
            "object_count": 1,
            "bytes_used": 1,
            "temp_url_key": None,
        }
        container = Container(name="foo_bar_container", extra={}, driver=self)
        obj = Object(
            name="foo_bar_object",
            size=1000,
            hash=None,
            extra={},
            container=container,
            meta_data=None,
            driver=self,
        )
        self.assertRaises(KeyError, self.driver.ex_get_object_temp_url, obj, "GET")

    def _remove_test_file(self):
        file_path = os.path.abspath(__file__) + ".temp"

        try:
            os.unlink(file_path)
        except OSError:
            pass


class CloudFilesDeprecatedUSTests(CloudFilesTests):
    driver_klass = CloudFilesStorageDriver
    region = "ord"


class CloudFilesDeprecatedUKTests(CloudFilesTests):
    driver_klass = CloudFilesStorageDriver
    region = "lon"


class CloudFilesMockHttp(BaseRangeDownloadMockHttp, unittest.TestCase):
    fixtures = StorageFileFixtures("cloudfiles")
    base_headers = {"content-type": "application/json; charset=UTF-8"}

    # fake auth token response
    def _v2_0_tokens(self, method, url, body, headers):
        headers = copy.deepcopy(self.base_headers)
        body = self.fixtures.load("_v2_0__auth.json")

        return (httplib.OK, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_MALFORMED_JSON(self, method, url, body, headers):
        # test_invalid_json_throws_exception
        body = 'broken: json /*"'

        return (
            httplib.NO_CONTENT,
            body,
            self.base_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_MossoCloudFS_EMPTY(self, method, url, body, headers):
        return (
            httplib.NO_CONTENT,
            body,
            self.base_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_MossoCloudFS(self, method, url, body, headers):
        headers = copy.deepcopy(self.base_headers)

        if method == "GET":
            # list_containers
            body = self.fixtures.load("list_containers.json")
            status_code = httplib.OK
        elif method == "HEAD":
            # get_meta_data
            body = self.fixtures.load("meta_data.json")
            status_code = httplib.NO_CONTENT
            headers.update(
                {
                    "x-account-container-count": "10",
                    "x-account-object-count": "400",
                    "x-account-bytes-used": "1234567",
                }
            )
        elif method == "POST":
            body = ""
            status_code = httplib.NO_CONTENT

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_not_found(self, method, url, body, headers):
        # test_get_object_not_found

        if method == "HEAD":
            body = ""
        else:
            raise ValueError("Invalid method")

        return (
            httplib.NOT_FOUND,
            body,
            self.base_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_MossoCloudFS_test_container_EMPTY(self, method, url, body, headers):
        body = self.fixtures.load("list_container_objects_empty.json")

        return (httplib.OK, body, self.base_headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_test_20container_201_EMPTY(self, method, url, body, headers):
        body = self.fixtures.load("list_container_objects_empty.json")

        return (httplib.OK, body, self.base_headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_test_container(self, method, url, body, headers):
        headers = copy.deepcopy(self.base_headers)

        if method == "GET":
            # list_container_objects

            if url.find("marker") == -1:
                body = self.fixtures.load("list_container_objects.json")
                status_code = httplib.OK
            else:
                body = ""
                status_code = httplib.NO_CONTENT
        elif method == "HEAD":
            # get_container
            body = self.fixtures.load("list_container_objects_empty.json")
            status_code = httplib.NO_CONTENT
            headers.update({"x-container-object-count": "800", "x-container-bytes-used": "1234568"})

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_test_container_ITERATOR(self, method, url, body, headers):
        headers = copy.deepcopy(self.base_headers)
        # list_container_objects

        if url.find("foo-test-3") != -1:
            body = self.fixtures.load("list_container_objects_not_exhausted2.json")
            status_code = httplib.OK
        elif url.find("foo-test-5") != -1:
            body = ""
            status_code = httplib.NO_CONTENT
        else:
            # First request
            body = self.fixtures.load("list_container_objects_not_exhausted1.json")
            status_code = httplib.OK

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_test_container_not_found(self, method, url, body, headers):
        # test_get_container_not_found

        if method == "HEAD":
            body = ""
        else:
            raise ValueError("Invalid method")

        return (
            httplib.NOT_FOUND,
            body,
            self.base_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_MossoCloudFS_test_container_test_object(self, method, url, body, headers):
        headers = copy.deepcopy(self.base_headers)

        if method == "HEAD":
            # get_object
            body = self.fixtures.load("list_container_objects_empty.json")
            status_code = httplib.NO_CONTENT
            headers.update(
                {
                    "content-length": "555",
                    "last-modified": "Tue, 25 Jan 2011 22:01:49 GMT",
                    "etag": "6b21c4a111ac178feacf9ec9d0c71f17",
                    "x-object-meta-foo-bar": "test 1",
                    "x-object-meta-bar-foo": "test 2",
                    "content-type": "application/zip",
                }
            )

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_test_container__7E_test_object(self, method, url, body, headers):
        headers = copy.deepcopy(self.base_headers)

        if method == "HEAD":
            # get_object_name_encoding
            body = self.fixtures.load("list_container_objects_empty.json")
            status_code = httplib.NO_CONTENT
            headers.update(
                {
                    "content-length": "555",
                    "last-modified": "Tue, 25 Jan 2011 22:01:49 GMT",
                    "etag": "6b21c4a111ac178feacf9ec9d0c71f17",
                    "x-object-meta-foo-bar": "test 1",
                    "x-object-meta-bar-foo": "test 2",
                    "content-type": "application/zip",
                }
            )

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_test_create_container(self, method, url, body, headers):
        # test_create_container_success
        headers = copy.deepcopy(self.base_headers)
        body = self.fixtures.load("list_container_objects_empty.json")
        headers = copy.deepcopy(self.base_headers)
        headers.update({"content-length": "18", "date": "Mon, 28 Feb 2011 07:52:57 GMT"})
        status_code = httplib.CREATED

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_speci_40l_name(self, method, url, body, headers):
        # test_create_container_put_object_name_encoding
        # Verify that the name is properly url encoded
        container_name = "speci@l_name"
        encoded_container_name = urlquote(container_name)
        self.assertTrue(encoded_container_name in url)

        headers = copy.deepcopy(self.base_headers)
        body = self.fixtures.load("list_container_objects_empty.json")
        headers = copy.deepcopy(self.base_headers)
        headers.update({"content-length": "18", "date": "Mon, 28 Feb 2011 07:52:57 GMT"})
        status_code = httplib.CREATED

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_test_create_container_ALREADY_EXISTS(self, method, url, body, headers):
        # test_create_container_already_exists
        headers = copy.deepcopy(self.base_headers)
        body = self.fixtures.load("list_container_objects_empty.json")
        headers.update({"content-type": "text/plain"})
        status_code = httplib.ACCEPTED

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container(self, method, url, body, headers):
        if method == "DELETE":
            # test_delete_container_success
            body = self.fixtures.load("list_container_objects_empty.json")
            headers = self.base_headers
            status_code = httplib.NO_CONTENT
        elif method == "POST":
            # test_ex_enable_static_website
            body = ""
            headers = self.base_headers
            status_code = httplib.ACCEPTED

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_object_PURGE_SUCCESS(self, method, url, body, headers):
        if method == "DELETE":
            # test_ex_purge_from_cdn
            headers = self.base_headers
            status_code = httplib.NO_CONTENT

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_object_PURGE_SUCCESS_EMAIL(
        self, method, url, body, headers
    ):
        if method == "DELETE":
            # test_ex_purge_from_cdn_with_email
            self.assertEqual(headers["X-Purge-Email"], "test@test.com")
            headers = self.base_headers
            status_code = httplib.NO_CONTENT

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_NOT_FOUND(self, method, url, body, headers):
        if method == "DELETE":
            # test_delete_container_not_found
            body = self.fixtures.load("list_container_objects_empty.json")
            headers = self.base_headers
            status_code = httplib.NOT_FOUND

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_NOT_EMPTY(self, method, url, body, headers):
        if method == "DELETE":
            # test_delete_container_not_empty
            body = self.fixtures.load("list_container_objects_empty.json")
            headers = self.base_headers
            status_code = httplib.CONFLICT

        return (status_code, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_foo_bar_object(self, method, url, body, headers):
        if method == "DELETE":
            # test_delete_object_success
            body = self.fixtures.load("list_container_objects_empty.json")
            headers = self.base_headers
            status_code = httplib.NO_CONTENT

            return (status_code, body, headers, httplib.responses[httplib.OK])
        elif method == "GET":
            body = generate_random_data(1000)

            return (httplib.OK, body, self.base_headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_foo_bar_object_range(self, method, url, body, headers):
        if method == "GET":
            # test_download_object_range_success
            body = "0123456789123456789"

            self.assertTrue("Range" in headers)
            self.assertEqual(headers["Range"], "bytes=5-6")

            start_bytes, end_bytes = self._get_start_and_end_bytes_from_range_str(
                headers["Range"], body
            )

            return (
                httplib.PARTIAL_CONTENT,
                body[start_bytes : end_bytes + 1],
                self.base_headers,
                httplib.responses[httplib.PARTIAL_CONTENT],
            )

    def _v1_MossoCloudFS_py3_img_or_vid(self, method, url, body, headers):
        headers = {"etag": "e2378cace8712661ce7beec3d9362ef6"}
        headers.update(self.base_headers)

        return httplib.CREATED, "", headers, httplib.responses[httplib.CREATED]

    def _v1_MossoCloudFS_foo_bar_container_foo_test_upload(self, method, url, body, headers):
        # test_object_upload_success

        body = ""
        headers = {}
        headers.update(self.base_headers)
        headers["etag"] = "hash343hhash89h932439jsaa89"

        return (httplib.CREATED, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_speci_40l_name_m_40obj_E2_82_ACct(self, method, url, body, headers):
        # test_create_container_put_object_name_encoding
        # Verify that the name is properly url encoded
        object_name = "m@obj€ct"
        urlquote(object_name)

        headers = copy.deepcopy(self.base_headers)
        body = ""
        headers["etag"] = "hash343hhash89h932439jsaa89"

        return (httplib.CREATED, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_empty(self, method, url, body, headers):
        # test_upload_object_zero_size_object
        body = ""
        headers = {}
        headers.update(self.base_headers)
        headers["etag"] = "hash343hhash89h932439jsaa89"

        return (httplib.CREATED, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_foo_test_upload_INVALID_HASH(
        self, method, url, body, headers
    ):
        # test_object_upload_invalid_hash
        body = ""
        headers = {}
        headers.update(self.base_headers)
        headers["etag"] = "foobar"

        return (httplib.CREATED, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_foo_bar_object_INVALID_SIZE(
        self, method, url, body, headers
    ):
        # test_download_object_invalid_file_size
        body = generate_random_data(100)

        return (httplib.OK, body, self.base_headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_foo_bar_object_NOT_FOUND(
        self, method, url, body, headers
    ):
        body = ""

        return (
            httplib.NOT_FOUND,
            body,
            self.base_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_MossoCloudFS_foo_bar_container_foo_test_stream_data(self, method, url, body, headers):
        # test_upload_object_via_stream_success
        hasher = hashlib.md5()  # nosec
        hasher.update(b"235")
        hash_value = hasher.hexdigest()

        headers = {}
        headers.update(self.base_headers)
        headers["etag"] = hash_value
        body = "test"

        return (httplib.CREATED, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_foo_test_stream_data_seek(
        self, method, url, body, headers
    ):
        # test_upload_object_via_stream_stream_seek_at_end
        hasher = hashlib.md5()  # nosec
        hasher.update(b"123456789")
        hash_value = hasher.hexdigest()

        headers = {}
        headers.update(self.base_headers)
        headers["etag"] = hash_value
        body = "test"

        return (httplib.CREATED, body, headers, httplib.responses[httplib.OK])

    def _v1_MossoCloudFS_foo_bar_container_foo_bar_object_NO_BUFFER(
        self, method, url, body, headers
    ):
        # test_download_object_data_is_not_buffered_in_memory
        headers = {}
        headers.update(self.base_headers)
        headers["etag"] = "577ef1154f3240ad5b9b413aa7346a1e"
        body = generate_random_data(1000)

        return (httplib.OK, body, headers, httplib.responses[httplib.OK])


if __name__ == "__main__":
    sys.exit(unittest.main())
