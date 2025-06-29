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
import datetime
import unittest
from unittest import mock
from unittest.mock import Mock, patch

import pytest
import requests_mock

from libcloud.test import XML_HEADERS, MockHttp
from libcloud.pricing import set_pricing, clear_pricing_data
from libcloud.utils.py3 import u, httplib, method_type
from libcloud.common.base import LibcloudConnection
from libcloud.common.types import LibcloudError, InvalidCredsError, MalformedResponseError
from libcloud.compute.base import Node, NodeSize, NodeImage
from libcloud.test.compute import TestCaseMixin
from libcloud.test.secrets import OPENSTACK_PARAMS
from libcloud.compute.types import (
    Provider,
    StorageVolumeState,
    VolumeSnapshotState,
    NodeImageMemberState,
    KeyPairDoesNotExistError,
)
from libcloud.utils.iso8601 import UTC
from libcloud.common.exceptions import BaseHTTPError
from libcloud.compute.providers import get_driver
from libcloud.test.file_fixtures import OpenStackFixtures, ComputeFileFixtures
from libcloud.common.openstack_identity import (
    AUTH_VERSIONS_WITH_EXPIRES,
    OpenStackAuthenticationCache,
)
from libcloud.compute.drivers.openstack import (
    OpenStackKeyPair,
    OpenStackNetwork,
    OpenStackException,
    OpenStack_2_NodeDriver,
    OpenStackSecurityGroup,
    OpenStack_2_ServerGroup,
    OpenStack_1_0_Connection,
    OpenStack_1_0_NodeDriver,
    OpenStack_1_1_NodeDriver,
    OpenStack_2_FloatingIpPool,
    OpenStackSecurityGroupRule,
    OpenStack_1_1_FloatingIpPool,
    OpenStack_2_PortInterfaceState,
    OpenStack_1_1_FloatingIpAddress,
)

try:
    import simplejson as json
except ImportError:
    import json


BASE_DIR = os.path.abspath(os.path.split(__file__)[0])


class OpenStackAuthTests(unittest.TestCase):
    def setUp(self):
        OpenStack_1_0_NodeDriver.connectionCls = OpenStack_1_0_Connection
        OpenStack_1_0_NodeDriver.connectionCls.conn_class = LibcloudConnection

    def test_auth_host_passed(self):
        forced_auth = "http://x.y.z.y:5000"
        d = OpenStack_1_0_NodeDriver(
            "user",
            "correct_password",
            ex_force_auth_version="2.0_password",
            ex_force_auth_url="http://x.y.z.y:5000",
            ex_tenant_name="admin",
        )
        self.assertEqual(d._ex_force_auth_url, forced_auth)

        with requests_mock.Mocker() as mock:
            body2 = ComputeFileFixtures("openstack").load("_v2_0__auth.json")

            mock.register_uri(
                "POST",
                "http://x.y.z.y:5000/v2.0/tokens",
                text=body2,
                headers={"content-type": "application/json; charset=UTF-8"},
            )
            d.connection._populate_hosts_and_request_paths()
            self.assertEqual(d.connection.host, "test_endpoint.com")

    def test_driver_instantiation_invalid_auth(self):
        with pytest.raises(LibcloudError):
            d = OpenStack_1_0_NodeDriver(
                "user",
                "correct_password",
                ex_force_auth_version="5.0",
                ex_force_auth_url="http://x.y.z.y:5000",
                ex_tenant_name="admin",
            )
            d.list_nodes()


class OpenStack_1_0_Tests(TestCaseMixin, unittest.TestCase):
    should_list_locations = False
    should_list_volumes = False

    driver_klass = OpenStack_1_0_NodeDriver
    driver_args = OPENSTACK_PARAMS
    driver_kwargs = {}
    # driver_kwargs = {'ex_force_auth_version': '1.0'}

    @classmethod
    def create_driver(self):
        if self is not OpenStack_1_0_FactoryMethodTests:
            self.driver_type = self.driver_klass

        return self.driver_type(*self.driver_args, **self.driver_kwargs)

    def setUp(self):
        # monkeypatch get_endpoint because the base openstack driver doesn't actually
        # work with old devstack but this class/tests are still used by the rackspace
        # driver
        def get_endpoint(*args, **kwargs):
            return "https://servers.api.rackspacecloud.com/v1.0/slug"

        self.driver_klass.connectionCls.get_endpoint = get_endpoint

        self.driver_klass.connectionCls.conn_class = OpenStackMockHttp
        self.driver_klass.connectionCls.auth_url = "https://auth.api.example.com"

        OpenStackMockHttp.type = None

        self.driver = self.create_driver()
        # normally authentication happens lazily, but we force it here
        self.driver.connection._populate_hosts_and_request_paths()
        clear_pricing_data()

    @patch("libcloud.common.openstack.OpenStackServiceCatalog")
    def test_populate_hosts_and_requests_path(self, _):
        tomorrow = datetime.datetime.today() + datetime.timedelta(1)
        cls = self.driver_klass.connectionCls

        count = 5

        # Test authentication and token reuse
        con = cls("username", "key")
        osa = con.get_auth_class()

        mocked_auth_method = Mock()
        osa.authenticate = mocked_auth_method

        # Valid token returned on first call, should be reused.

        for i in range(0, count):
            con._populate_hosts_and_request_paths()

            if i == 0:
                osa.auth_token = "1234"
                osa.auth_token_expires = tomorrow

        self.assertEqual(mocked_auth_method.call_count, 1)

        osa.auth_token = None
        osa.auth_token_expires = None

        # ex_force_auth_token provided, authenticate should never be called
        con = cls(
            "username",
            "key",
            ex_force_base_url="http://ponies",
            ex_force_auth_token="1234",
        )
        osa = con.get_auth_class()

        mocked_auth_method = Mock()
        osa.authenticate = mocked_auth_method

        for i in range(0, count):
            con._populate_hosts_and_request_paths()

        self.assertEqual(mocked_auth_method.call_count, 0)

    def test_auth_token_is_set(self):
        self.driver.connection._populate_hosts_and_request_paths()
        self.assertEqual(self.driver.connection.auth_token, "aaaaaaaaaaaa-bbb-cccccccccccccc")

    def test_auth_token_expires_is_set(self):
        self.driver.connection._populate_hosts_and_request_paths()

        expires = self.driver.connection.auth_token_expires
        self.assertEqual(expires.isoformat(), "2999-11-23T21:00:14-06:00")

    def test_auth(self):
        if self.driver.connection._auth_version == "2.0":
            return

        OpenStackMockHttp.type = "UNAUTHORIZED"
        try:
            self.driver = self.create_driver()
            self.driver.list_nodes()
        except InvalidCredsError as e:
            self.assertEqual(True, isinstance(e, InvalidCredsError))
        else:
            self.fail("test should have thrown")

    def test_auth_missing_key(self):
        if self.driver.connection._auth_version == "2.0":
            return

        OpenStackMockHttp.type = "UNAUTHORIZED_MISSING_KEY"
        try:
            self.driver = self.create_driver()
            self.driver.list_nodes()
        except MalformedResponseError as e:
            self.assertEqual(True, isinstance(e, MalformedResponseError))
        else:
            self.fail("test should have thrown")

    def test_auth_server_error(self):
        if self.driver.connection._auth_version == "2.0":
            return

        OpenStackMockHttp.type = "INTERNAL_SERVER_ERROR"
        try:
            self.driver = self.create_driver()
            self.driver.list_nodes()
        except MalformedResponseError as e:
            self.assertEqual(True, isinstance(e, MalformedResponseError))
        else:
            self.fail("test should have thrown")

    def test_ex_auth_cache_passed_to_identity_connection(self):
        kwargs = self.driver_kwargs.copy()
        kwargs["ex_auth_cache"] = OpenStackMockAuthCache()
        driver = self.driver_type(*self.driver_args, **kwargs)
        driver.list_nodes()
        self.assertEqual(kwargs["ex_auth_cache"], driver.connection.get_auth_class().auth_cache)

    def test_unauthorized_clears_cached_auth_context(self):
        auth_cache = OpenStackMockAuthCache()
        self.assertEqual(len(auth_cache), 0)

        kwargs = self.driver_kwargs.copy()
        kwargs["ex_auth_cache"] = auth_cache
        driver = self.driver_type(*self.driver_args, **kwargs)
        driver.list_nodes()

        # Token was cached
        self.assertEqual(len(auth_cache), 1)

        # Simulate token being revoked
        self.driver_klass.connectionCls.conn_class.type = "UNAUTHORIZED"
        with pytest.raises(BaseHTTPError):
            driver.list_nodes()

        # Token was evicted
        self.assertEqual(len(auth_cache), 0)

    def test_error_parsing_when_body_is_missing_message(self):
        OpenStackMockHttp.type = "NO_MESSAGE_IN_ERROR_BODY"
        try:
            self.driver.list_images()
        except Exception as e:
            self.assertEqual(True, isinstance(e, Exception))
        else:
            self.fail("test should have thrown")

    def test_list_locations(self):
        locations = self.driver.list_locations()
        self.assertEqual(len(locations), 1)

    def test_list_nodes(self):
        OpenStackMockHttp.type = "EMPTY"
        ret = self.driver.list_nodes()
        self.assertEqual(len(ret), 0)
        OpenStackMockHttp.type = None
        ret = self.driver.list_nodes()
        self.assertEqual(len(ret), 1)
        node = ret[0]
        self.assertEqual("67.23.21.33", node.public_ips[0])
        self.assertTrue("10.176.168.218" in node.private_ips)
        self.assertEqual(node.extra.get("flavorId"), "1")
        self.assertEqual(node.extra.get("imageId"), "11")
        self.assertEqual(type(node.extra.get("metadata")), type(dict()))

        OpenStackMockHttp.type = "METADATA"
        ret = self.driver.list_nodes()
        self.assertEqual(len(ret), 1)
        node = ret[0]
        self.assertEqual(type(node.extra.get("metadata")), type(dict()))
        self.assertEqual(node.extra.get("metadata").get("somekey"), "somevalue")
        OpenStackMockHttp.type = None

    def test_list_images(self):
        ret = self.driver.list_images()
        expected = {
            10: {
                "serverId": None,
                "status": "ACTIVE",
                "created": "2009-07-20T09:14:37-05:00",
                "updated": "2009-07-20T09:14:37-05:00",
                "progress": None,
                "minDisk": None,
                "minRam": None,
            },
            11: {
                "serverId": "91221",
                "status": "ACTIVE",
                "created": "2009-11-29T20:22:09-06:00",
                "updated": "2009-11-29T20:24:08-06:00",
                "progress": "100",
                "minDisk": "5",
                "minRam": "256",
            },
        }

        for ret_idx, extra in list(expected.items()):
            for key, value in list(extra.items()):
                self.assertEqual(ret[ret_idx].extra[key], value)

    def test_create_node(self):
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(name="racktest", image=image, size=size)
        self.assertEqual(node.name, "racktest")
        self.assertEqual(node.extra.get("password"), "racktestvJq7d3")

    def test_create_node_without_adminPass(self):
        OpenStackMockHttp.type = "NO_ADMIN_PASS"
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(name="racktest", image=image, size=size)
        self.assertEqual(node.name, "racktest")
        self.assertIsNone(node.extra.get("password"))

    def test_create_node_ex_shared_ip_group(self):
        OpenStackMockHttp.type = "EX_SHARED_IP_GROUP"
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(
            name="racktest", image=image, size=size, ex_shared_ip_group_id="12345"
        )
        self.assertEqual(node.name, "racktest")
        self.assertEqual(node.extra.get("password"), "racktestvJq7d3")

    def test_create_node_with_metadata(self):
        OpenStackMockHttp.type = "METADATA"
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        metadata = {"a": "b", "c": "d"}
        files = {"/file1": "content1", "/file2": "content2"}
        node = self.driver.create_node(
            name="racktest",
            image=image,
            size=size,
            ex_metadata=metadata,
            ex_files=files,
        )
        self.assertEqual(node.name, "racktest")
        self.assertEqual(node.extra.get("password"), "racktestvJq7d3")
        self.assertEqual(node.extra.get("metadata"), metadata)

    def test_reboot_node(self):
        node = Node(
            id=72258,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ret = node.reboot()
        self.assertTrue(ret is True)

    def test_destroy_node(self):
        node = Node(
            id=72258,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ret = node.destroy()
        self.assertTrue(ret is True)

    def test_ex_limits(self):
        limits = self.driver.ex_limits()
        self.assertTrue("rate" in limits)
        self.assertTrue("absolute" in limits)

    def test_create_image(self):
        node = Node(
            id=444222,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        image = self.driver.create_image(node, "imgtest")
        self.assertEqual(image.name, "imgtest")
        self.assertEqual(image.id, "12345")

    def test_delete_image(self):
        image = NodeImage(id=333111, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        ret = self.driver.delete_image(image)
        self.assertTrue(ret)

    def test_ex_list_ip_addresses(self):
        ret = self.driver.ex_list_ip_addresses(node_id=72258)
        self.assertEqual(2, len(ret.public_addresses))
        self.assertTrue("67.23.10.131" in ret.public_addresses)
        self.assertTrue("67.23.10.132" in ret.public_addresses)
        self.assertEqual(1, len(ret.private_addresses))
        self.assertTrue("10.176.42.16" in ret.private_addresses)

    def test_ex_list_ip_groups(self):
        ret = self.driver.ex_list_ip_groups()
        self.assertEqual(2, len(ret))
        self.assertEqual("1234", ret[0].id)
        self.assertEqual("Shared IP Group 1", ret[0].name)
        self.assertEqual("5678", ret[1].id)
        self.assertEqual("Shared IP Group 2", ret[1].name)
        self.assertTrue(ret[0].servers is None)

    def test_ex_list_ip_groups_detail(self):
        ret = self.driver.ex_list_ip_groups(details=True)

        self.assertEqual(2, len(ret))

        self.assertEqual("1234", ret[0].id)
        self.assertEqual("Shared IP Group 1", ret[0].name)
        self.assertEqual(2, len(ret[0].servers))
        self.assertEqual("422", ret[0].servers[0])
        self.assertEqual("3445", ret[0].servers[1])

        self.assertEqual("5678", ret[1].id)
        self.assertEqual("Shared IP Group 2", ret[1].name)
        self.assertEqual(3, len(ret[1].servers))
        self.assertEqual("23203", ret[1].servers[0])
        self.assertEqual("2456", ret[1].servers[1])
        self.assertEqual("9891", ret[1].servers[2])

    def test_ex_create_ip_group(self):
        ret = self.driver.ex_create_ip_group("Shared IP Group 1", "5467")
        self.assertEqual("1234", ret.id)
        self.assertEqual("Shared IP Group 1", ret.name)
        self.assertEqual(1, len(ret.servers))
        self.assertEqual("422", ret.servers[0])

    def test_ex_delete_ip_group(self):
        ret = self.driver.ex_delete_ip_group("5467")
        self.assertEqual(True, ret)

    def test_ex_share_ip(self):
        ret = self.driver.ex_share_ip("1234", "3445", "67.23.21.133")
        self.assertEqual(True, ret)

    def test_ex_unshare_ip(self):
        ret = self.driver.ex_unshare_ip("3445", "67.23.21.133")
        self.assertEqual(True, ret)

    def test_ex_resize(self):
        node = Node(
            id=444222,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        self.assertTrue(self.driver.ex_resize(node=node, size=size))

    def test_ex_confirm_resize(self):
        node = Node(
            id=444222,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        self.assertTrue(self.driver.ex_confirm_resize(node=node))

    def test_ex_revert_resize(self):
        node = Node(
            id=444222,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        self.assertTrue(self.driver.ex_revert_resize(node=node))

    def test_list_sizes(self):
        sizes = self.driver.list_sizes()
        self.assertEqual(len(sizes), 7, "Wrong sizes count")

        for size in sizes:
            self.assertTrue(isinstance(size.price, float), "Wrong size price type")

            if self.driver.api_name == "openstack":
                self.assertEqual(size.price, 0, "Size price should be zero by default")

    def test_list_sizes_with_specified_pricing(self):
        if self.driver.api_name != "openstack":
            return

        pricing = {str(i): i for i in range(1, 8)}

        set_pricing(driver_type="compute", driver_name="openstack", pricing=pricing)

        sizes = self.driver.list_sizes()
        self.assertEqual(len(sizes), 7, "Wrong sizes count")

        for size in sizes:
            self.assertTrue(isinstance(size.price, float), "Wrong size price type")
            self.assertEqual(float(size.price), float(pricing[size.id]))


class OpenStack_1_0_FactoryMethodTests(OpenStack_1_0_Tests):
    should_list_locations = False
    should_list_volumes = False

    driver_klass = OpenStack_1_0_NodeDriver
    driver_type = get_driver(Provider.OPENSTACK)
    driver_args = OPENSTACK_PARAMS + ("1.0",)

    def test_factory_method_invalid_version(self):
        try:
            self.driver_type(*(OPENSTACK_PARAMS + ("15.5",)))
        except NotImplementedError:
            pass
        else:
            self.fail("Exception was not thrown")


class OpenStackMockHttp(MockHttp, unittest.TestCase):
    fixtures = ComputeFileFixtures("openstack")
    auth_fixtures = OpenStackFixtures()
    json_content_headers = {"content-type": "application/json; charset=UTF-8"}

    # fake auth token response
    def _v1_0(self, method, url, body, headers):
        headers = {
            "x-server-management-url": "https://servers.api.rackspacecloud.com/v1.0/slug",
            "x-auth-token": "FE011C19-CF86-4F87-BE5D-9229145D7A06",
            "x-cdn-management-url": "https://cdn.clouddrive.com/v1/MossoCloudFS_FE011C19-CF86-4F87-BE5D-9229145D7A06",
            "x-storage-token": "FE011C19-CF86-4F87-BE5D-9229145D7A06",
            "x-storage-url": "https://storage4.clouddrive.com/v1/MossoCloudFS_FE011C19-CF86-4F87-BE5D-9229145D7A06",
        }

        return (httplib.NO_CONTENT, "", headers, httplib.responses[httplib.NO_CONTENT])

    def _v1_0_UNAUTHORIZED(self, method, url, body, headers):
        return (httplib.UNAUTHORIZED, "", {}, httplib.responses[httplib.UNAUTHORIZED])

    def _v1_0_INTERNAL_SERVER_ERROR(self, method, url, body, headers):
        return (
            httplib.INTERNAL_SERVER_ERROR,
            "<h1>500: Internal Server Error</h1>",
            {},
            httplib.responses[httplib.INTERNAL_SERVER_ERROR],
        )

    def _v1_0_slug_images_detail_NO_MESSAGE_IN_ERROR_BODY(self, method, url, body, headers):
        body = self.fixtures.load("300_multiple_choices.json")

        return (
            httplib.MULTIPLE_CHOICES,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_0_UNAUTHORIZED_MISSING_KEY(self, method, url, body, headers):
        headers = {
            "x-server-management-url": "https://servers.api.rackspacecloud.com/v1.0/slug",
            "x-auth-tokenx": "FE011C19-CF86-4F87-BE5D-9229145D7A06",
            "x-cdn-management-url": "https://cdn.clouddrive.com/v1/MossoCloudFS_FE011C19-CF86-4F87-BE5D-9229145D7A06",
        }

        return (httplib.NO_CONTENT, "", headers, httplib.responses[httplib.NO_CONTENT])

    def _v2_0_tokens(self, method, url, body, headers):
        body = self.auth_fixtures.load("_v2_0__auth.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_0_slug_servers_detail_EMPTY(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_servers_detail_empty.xml")

        return (httplib.OK, body, XML_HEADERS, httplib.responses[httplib.OK])

    def _v1_0_slug_servers_detail(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_servers_detail.xml")

        return (httplib.OK, body, XML_HEADERS, httplib.responses[httplib.OK])

    def _v1_0_slug_servers_detail_METADATA(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_servers_detail_metadata.xml")

        return (httplib.OK, body, XML_HEADERS, httplib.responses[httplib.OK])

    def _v1_0_slug_servers_detail_UNAUTHORIZED(self, method, url, body, headers):
        return (httplib.UNAUTHORIZED, "", {}, httplib.responses[httplib.UNAUTHORIZED])

    def _v1_0_slug_images_333111(self, method, url, body, headers):
        if method != "DELETE":
            raise NotImplementedError()
        # this is currently used for deletion of an image
        # as such it should not accept GET/POST

        return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])

    def _v1_0_slug_images(self, method, url, body, headers):
        if method != "POST":
            raise NotImplementedError()
        # this is currently used for creation of new image with
        # POST request, don't handle GET to avoid possible confusion
        body = self.fixtures.load("v1_slug_images_post.xml")

        return (
            httplib.ACCEPTED,
            body,
            XML_HEADERS,
            httplib.responses[httplib.ACCEPTED],
        )

    def _v1_0_slug_images_detail(self, method, url, body, headers):
        if method != "GET":
            raise ValueError("Invalid method: %s" % (method))

        body = self.fixtures.load("v1_slug_images_detail.xml")

        return (httplib.OK, body, XML_HEADERS, httplib.responses[httplib.OK])

    def _v1_0_slug_images_detail_invalid_next(self, method, url, body, headers):
        if method != "GET":
            raise ValueError("Invalid method: %s" % (method))

        body = self.fixtures.load("v1_slug_images_detail.xml")

        return (httplib.OK, body, XML_HEADERS, httplib.responses[httplib.OK])

    def _v1_0_slug_servers(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_servers.xml")

        return (
            httplib.ACCEPTED,
            body,
            XML_HEADERS,
            httplib.responses[httplib.ACCEPTED],
        )

    def _v1_0_slug_servers_NO_ADMIN_PASS(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_servers_no_admin_pass.xml")

        return (
            httplib.ACCEPTED,
            body,
            XML_HEADERS,
            httplib.responses[httplib.ACCEPTED],
        )

    def _v1_0_slug_servers_EX_SHARED_IP_GROUP(self, method, url, body, headers):
        # test_create_node_ex_shared_ip_group
        # Verify that the body contains sharedIpGroupId XML element
        body = u(body)
        self.assertTrue(body.find('sharedIpGroupId="12345"') != -1)
        body = self.fixtures.load("v1_slug_servers.xml")

        return (
            httplib.ACCEPTED,
            body,
            XML_HEADERS,
            httplib.responses[httplib.ACCEPTED],
        )

    def _v1_0_slug_servers_METADATA(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_servers_metadata.xml")

        return (
            httplib.ACCEPTED,
            body,
            XML_HEADERS,
            httplib.responses[httplib.ACCEPTED],
        )

    def _v1_0_slug_servers_72258_action(self, method, url, body, headers):
        if method != "POST" or body[:8] != "<reboot ":
            raise NotImplementedError()
        # only used by reboot() right now, but we will need to parse body
        # someday !!!!

        return (httplib.ACCEPTED, "", {}, httplib.responses[httplib.ACCEPTED])

    def _v1_0_slug_limits(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_limits.xml")

        return (
            httplib.ACCEPTED,
            body,
            XML_HEADERS,
            httplib.responses[httplib.ACCEPTED],
        )

    def _v1_0_slug_servers_72258(self, method, url, body, headers):
        if method != "DELETE":
            raise NotImplementedError()
        # only used by destroy node()

        return (httplib.ACCEPTED, "", {}, httplib.responses[httplib.ACCEPTED])

    def _v1_0_slug_servers_72258_ips(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_servers_ips.xml")

        return (httplib.OK, body, XML_HEADERS, httplib.responses[httplib.OK])

    def _v1_0_slug_shared_ip_groups_5467(self, method, url, body, headers):
        if method != "DELETE":
            raise NotImplementedError()

        return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])

    def _v1_0_slug_shared_ip_groups(self, method, url, body, headers):
        fixture = (
            "v1_slug_shared_ip_group.xml" if method == "POST" else "v1_slug_shared_ip_groups.xml"
        )
        body = self.fixtures.load(fixture)

        return (httplib.OK, body, XML_HEADERS, httplib.responses[httplib.OK])

    def _v1_0_slug_shared_ip_groups_detail(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_shared_ip_groups_detail.xml")

        return (httplib.OK, body, XML_HEADERS, httplib.responses[httplib.OK])

    def _v1_0_slug_servers_3445_ips_public_67_23_21_133(self, method, url, body, headers):
        return (httplib.ACCEPTED, "", {}, httplib.responses[httplib.ACCEPTED])

    def _v1_0_slug_servers_444222_action(self, method, url, body, headers):
        body = u(body)

        if body.find("resize") != -1:
            # test_ex_resize_server

            if body.find("personality") != -1:
                return httplib.BAD_REQUEST
            else:
                return (
                    httplib.ACCEPTED,
                    "",
                    headers,
                    httplib.responses[httplib.NO_CONTENT],
                )
        elif body.find("confirmResize") != -1:
            # test_ex_confirm_resize

            return (
                httplib.NO_CONTENT,
                "",
                headers,
                httplib.responses[httplib.NO_CONTENT],
            )
        elif body.find("revertResize") != -1:
            # test_ex_revert_resize

            return (
                httplib.NO_CONTENT,
                "",
                headers,
                httplib.responses[httplib.NO_CONTENT],
            )

    def _v1_0_slug_flavors_detail(self, method, url, body, headers):
        body = self.fixtures.load("v1_slug_flavors_detail.xml")
        headers = {"date": "Tue, 14 Jun 2011 09:43:55 GMT", "content-length": "529"}
        headers.update(XML_HEADERS)

        return (httplib.OK, body, headers, httplib.responses[httplib.OK])

    def _v1_1_auth(self, method, url, body, headers):
        body = self.auth_fixtures.load("_v1_1__auth.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_auth_UNAUTHORIZED(self, method, url, body, headers):
        body = self.auth_fixtures.load("_v1_1__auth_unauthorized.json")

        return (
            httplib.UNAUTHORIZED,
            body,
            self.json_content_headers,
            httplib.responses[httplib.UNAUTHORIZED],
        )

    def _v1_1_auth_UNAUTHORIZED_MISSING_KEY(self, method, url, body, headers):
        body = self.auth_fixtures.load("_v1_1__auth_mssing_token.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_auth_INTERNAL_SERVER_ERROR(self, method, url, body, headers):
        return (
            httplib.INTERNAL_SERVER_ERROR,
            "<h1>500: Internal Server Error</h1>",
            {"content-type": "text/html"},
            httplib.responses[httplib.INTERNAL_SERVER_ERROR],
        )


class OpenStack_1_1_Tests(unittest.TestCase, TestCaseMixin):
    should_list_locations = False
    should_list_volumes = True

    driver_klass = OpenStack_1_1_NodeDriver
    driver_type = OpenStack_1_1_NodeDriver
    driver_args = OPENSTACK_PARAMS
    driver_kwargs = {"ex_force_auth_version": "2.0"}

    @classmethod
    def create_driver(self):
        if self is not OpenStack_1_1_FactoryMethodTests:
            self.driver_type = self.driver_klass

        return self.driver_type(*self.driver_args, **self.driver_kwargs)

    def setUp(self):
        self.driver_klass.connectionCls.conn_class = OpenStack_2_0_MockHttp
        self.driver_klass.connectionCls.auth_url = "https://auth.api.example.com"

        OpenStackMockHttp.type = None
        OpenStack_1_1_MockHttp.type = None
        OpenStack_2_0_MockHttp.type = None

        self.driver = self.create_driver()

        # normally authentication happens lazily, but we force it here
        self.driver.connection._populate_hosts_and_request_paths()
        clear_pricing_data()
        self.node = self.driver.list_nodes()[1]

    def _force_reauthentication(self):
        """
        Trash current auth token so driver will be forced to re-authenticate
        on next request.
        """
        self.driver.connection._ex_force_base_url = "http://ex_force_base_url.com:666/forced_url"
        self.driver.connection.auth_token = None
        self.driver.connection.auth_token_expires = None
        self.driver.connection._osa.auth_token = None
        self.driver.connection._osa.auth_token_expires = None

    def test_auth_token_is_set(self):
        self._force_reauthentication()
        self.driver.connection._populate_hosts_and_request_paths()

        self.assertEqual(self.driver.connection.auth_token, "aaaaaaaaaaaa-bbb-cccccccccccccc")

    def test_auth_token_expires_is_set(self):
        self._force_reauthentication()
        self.driver.connection._populate_hosts_and_request_paths()

        expires = self.driver.connection.auth_token_expires
        self.assertEqual(expires.isoformat(), "2999-11-23T21:00:14-06:00")

    def test_ex_force_base_url(self):
        # change base url and trash the current auth token so we can
        # re-authenticate
        self.driver.connection._ex_force_base_url = "http://ex_force_base_url.com:666/forced_url"
        self.driver.connection.auth_token = None
        self.driver.connection._populate_hosts_and_request_paths()

        # assert that we use the base url and not the auth url
        self.assertEqual(self.driver.connection.host, "ex_force_base_url.com")
        self.assertEqual(self.driver.connection.port, 666)
        self.assertEqual(self.driver.connection.request_path, "/forced_url")

    def test_get_endpoint_populates_host_port_and_request_path(self):
        # simulate a subclass overriding this method
        self.driver.connection.get_endpoint = (
            lambda: "http://endpoint_auth_url.com:1555/service_url"
        )
        self.driver.connection.auth_token = None
        self.driver.connection._ex_force_base_url = None
        self.driver.connection._populate_hosts_and_request_paths()

        # assert that we use the result of get endpoint
        self.assertEqual(self.driver.connection.host, "endpoint_auth_url.com")
        self.assertEqual(self.driver.connection.port, 1555)
        self.assertEqual(self.driver.connection.request_path, "/service_url")

    def test_set_auth_token_populates_host_port_and_request_path(self):
        # change base url and trash the current auth token so we can
        # re-authenticate
        self.driver.connection._ex_force_base_url = (
            "http://some_other_ex_force_base_url.com:1222/some-service"
        )
        self.driver.connection.auth_token = "preset-auth-token"
        self.driver.connection._populate_hosts_and_request_paths()

        # assert that we use the base url and not the auth url
        self.assertEqual(self.driver.connection.host, "some_other_ex_force_base_url.com")
        self.assertEqual(self.driver.connection.port, 1222)
        self.assertEqual(self.driver.connection.request_path, "/some-service")

    def test_auth_token_without_base_url_raises_exception(self):
        kwargs = {
            "ex_force_auth_version": "2.0",
            "ex_force_auth_token": "preset-auth-token",
        }
        try:
            self.driver_type(*self.driver_args, **kwargs)
            self.fail("Expected failure setting auth token without base url")
        except LibcloudError:
            pass
        else:
            self.fail("Expected failure setting auth token without base url")

    def test_ex_force_auth_token_passed_to_connection(self):
        base_url = "https://servers.api.rackspacecloud.com/v1.1/slug"
        kwargs = {
            "ex_force_auth_version": "2.0",
            "ex_force_auth_token": "preset-auth-token",
            "ex_force_base_url": base_url,
        }

        driver = self.driver_type(*self.driver_args, **kwargs)
        driver.list_nodes()

        self.assertEqual(kwargs["ex_force_auth_token"], driver.connection.auth_token)
        self.assertEqual("servers.api.rackspacecloud.com", driver.connection.host)
        self.assertEqual("/v1.1/slug", driver.connection.request_path)
        self.assertEqual(443, driver.connection.port)

    def test_ex_auth_cache_passed_to_identity_connection(self):
        kwargs = self.driver_kwargs.copy()
        kwargs["ex_auth_cache"] = OpenStackMockAuthCache()
        driver = self.driver_type(*self.driver_args, **kwargs)
        driver.connection.get_auth_class()
        driver.list_nodes()
        self.assertEqual(kwargs["ex_auth_cache"], driver.connection.get_auth_class().auth_cache)

    def test_unauthorized_clears_cached_auth_context(self):
        auth_cache = OpenStackMockAuthCache()
        self.assertEqual(len(auth_cache), 0)

        kwargs = self.driver_kwargs.copy()
        kwargs["ex_auth_cache"] = auth_cache
        driver = self.driver_type(*self.driver_args, **kwargs)
        driver.list_nodes()

        # Token was cached
        self.assertEqual(len(auth_cache), 1)

        # Simulate token being revoked
        self.driver_klass.connectionCls.conn_class.type = "UNAUTHORIZED"
        with pytest.raises(BaseHTTPError):
            driver.list_nodes()

        # Token was evicted
        self.assertEqual(len(auth_cache), 0)

    def test_list_nodes(self):
        nodes = self.driver.list_nodes()
        self.assertEqual(len(nodes), 2)
        node = nodes[0]

        self.assertEqual("12065", node.id)

        # test public IPv4
        self.assertTrue("12.16.18.28" in node.public_ips)
        self.assertTrue("50.57.94.35" in node.public_ips)

        # fixed public ip
        self.assertTrue("1.1.1.1" in node.public_ips)

        # floating public ip
        self.assertTrue("2.2.2.2" in node.public_ips)

        # test public IPv6
        self.assertTrue("2001:4801:7808:52:16:3eff:fe47:788a" in node.public_ips)

        # test private IPv4
        self.assertTrue("10.182.64.34" in node.private_ips)

        # fixed private ip
        self.assertTrue("10.3.3.3" in node.private_ips)

        # floating private ip
        self.assertTrue("192.168.3.3" in node.private_ips)
        self.assertTrue("172.16.1.1" in node.private_ips)

        # test private IPv6
        self.assertTrue("fec0:4801:7808:52:16:3eff:fe60:187d" in node.private_ips)

        # test creation date
        self.assertEqual(node.created_at, datetime.datetime(2011, 10, 11, 0, 51, 39, tzinfo=UTC))

        self.assertEqual(node.extra.get("flavorId"), "2")
        self.assertEqual(node.extra.get("imageId"), "7")
        self.assertEqual(node.extra.get("metadata"), {})
        self.assertEqual(node.extra["updated"], "2011-10-11T00:50:04Z")
        self.assertEqual(node.extra["created"], "2011-10-11T00:51:39Z")
        self.assertEqual(node.extra.get("userId"), "rs-reach")
        self.assertEqual(
            node.extra.get("hostId"),
            "912566d83a13fbb357ea" "3f13c629363d9f7e1ba3f" "925b49f3d2ab725",
        )
        self.assertEqual(node.extra.get("disk_config"), "AUTO")
        self.assertEqual(node.extra.get("task_state"), "spawning")
        self.assertEqual(node.extra.get("vm_state"), "active")
        self.assertEqual(node.extra.get("power_state"), 1)
        self.assertEqual(node.extra.get("progress"), 25)
        self.assertEqual(node.extra.get("fault")["id"], 1234)
        self.assertTrue(node.extra.get("service_name") is not None)
        self.assertTrue(node.extra.get("uri") is not None)

    def test_list_nodes_no_image_id_attribute(self):
        # Regression test for LIBCLOD-455
        self.driver_klass.connectionCls.conn_class.type = "ERROR_STATE_NO_IMAGE_ID"

        nodes = self.driver.list_nodes()
        self.assertIsNone(nodes[0].extra["imageId"])

    def test_list_volumes(self):
        volumes = self.driver.list_volumes()
        self.assertEqual(len(volumes), 2)
        volume = volumes[0]

        self.assertEqual("cd76a3a1-c4ce-40f6-9b9f-07a61508938d", volume.id)
        self.assertEqual("test_volume_2", volume.name)
        self.assertEqual(StorageVolumeState.AVAILABLE, volume.state)
        self.assertEqual(2, volume.size)
        self.assertEqual(
            volume.extra,
            {
                "description": "",
                "attachments": [
                    {
                        "id": "cd76a3a1-c4ce-40f6-9b9f-07a61508938d",
                        "device": "/dev/vdb",
                        "serverId": "12065",
                        "volumeId": "cd76a3a1-c4ce-40f6-9b9f-07a61508938d",
                    }
                ],
                "snapshot_id": None,
                "state": "available",
                "location": "nova",
                "volume_type": "None",
                "metadata": {},
                "created_at": "2013-06-24T11:20:13.000000",
            },
        )

        # also test that unknown state resolves to StorageVolumeState.UNKNOWN
        volume = volumes[1]
        self.assertEqual("cfcec3bc-b736-4db5-9535-4c24112691b5", volume.id)
        self.assertEqual("test_volume", volume.name)
        self.assertEqual(50, volume.size)
        self.assertEqual(StorageVolumeState.UNKNOWN, volume.state)
        self.assertEqual(
            volume.extra,
            {
                "description": "some description",
                "attachments": [],
                "snapshot_id": "01f48111-7866-4cd2-986a-e92683c4a363",
                "state": "some-unknown-state",
                "location": "nova",
                "volume_type": "None",
                "metadata": {},
                "created_at": "2013-06-21T12:39:02.000000",
            },
        )

    def test_list_sizes(self):
        sizes = self.driver.list_sizes()
        self.assertEqual(len(sizes), 8, "Wrong sizes count")

        for size in sizes:
            self.assertTrue(
                size.price is None or isinstance(size.price, float),
                "Wrong size price type",
            )
            self.assertTrue(isinstance(size.ram, int))
            self.assertTrue(isinstance(size.vcpus, int))
            self.assertTrue(isinstance(size.disk, int))
            self.assertTrue(isinstance(size.swap, int))
            self.assertTrue(isinstance(size.ephemeral_disk, int) or size.ephemeral_disk is None)
            self.assertTrue(isinstance(size.extra, dict))

            if size.id == "1":
                self.assertEqual(size.ephemeral_disk, 40)
                self.assertEqual(
                    size.extra,
                    {
                        "policy_class": "standard_flavor",
                        "class": "standard1",
                        "disk_io_index": "2",
                        "number_of_data_disks": "0",
                        "disabled": False,
                    },
                )

        self.assertEqual(sizes[0].vcpus, 8)

    def test_list_sizes_with_specified_pricing(self):
        pricing = {str(i): i * 5.0 for i in range(1, 9)}

        set_pricing(driver_type="compute", driver_name=self.driver.api_name, pricing=pricing)

        sizes = self.driver.list_sizes()
        self.assertEqual(len(sizes), 8, "Wrong sizes count")

        for size in sizes:
            self.assertTrue(isinstance(size.price, float), "Wrong size price type")

            self.assertEqual(size.price, pricing[size.id], "Size price should match")

    def test_list_images(self):
        images = self.driver.list_images()
        self.assertEqual(len(images), 13, "Wrong images count")

        image = images[0]
        self.assertEqual(image.id, "13")
        self.assertEqual(image.name, "Windows 2008 SP2 x86 (B24)")
        self.assertEqual(image.extra["updated"], "2011-08-06T18:14:02Z")
        self.assertEqual(image.extra["created"], "2011-08-06T18:13:11Z")
        self.assertEqual(image.extra["status"], "ACTIVE")
        self.assertEqual(image.extra["metadata"]["os_type"], "windows")
        self.assertEqual(image.extra["serverId"], "52415800-8b69-11e0-9b19-734f335aa7b3")
        self.assertEqual(image.extra["minDisk"], 0)
        self.assertEqual(image.extra["minRam"], 0)

    def test_create_node(self):
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(name="racktest", image=image, size=size)
        self.assertEqual(node.id, "26f7fbee-8ce1-4c28-887a-bfe8e4bb10fe")
        self.assertEqual(node.name, "racktest")
        self.assertEqual(node.extra["password"], "racktestvJq7d3")
        self.assertEqual(node.extra["metadata"]["My Server Name"], "Apache1")

    def test_create_node_with_ex_keyname_and_ex_userdata(self):
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(
            name="racktest",
            image=image,
            size=size,
            ex_keyname="devstack",
            ex_userdata="sample data",
        )
        self.assertEqual(node.id, "26f7fbee-8ce1-4c28-887a-bfe8e4bb10fe")
        self.assertEqual(node.name, "racktest")
        self.assertEqual(node.extra["password"], "racktestvJq7d3")
        self.assertEqual(node.extra["metadata"]["My Server Name"], "Apache1")
        self.assertEqual(node.extra["key_name"], "devstack")

    def test_create_node_with_availability_zone(self):
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(
            name="racktest", image=image, size=size, ex_availability_zone="testaz"
        )
        self.assertEqual(node.id, "26f7fbee-8ce1-4c28-887a-bfe8e4bb10fe")
        self.assertEqual(node.name, "racktest")
        self.assertEqual(node.extra["password"], "racktestvJq7d3")
        self.assertEqual(node.extra["metadata"]["My Server Name"], "Apache1")
        self.assertEqual(node.extra["availability_zone"], "testaz")

    def test_create_node_with_ex_disk_config(self):
        OpenStack_1_1_MockHttp.type = "EX_DISK_CONFIG"
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(
            name="racktest", image=image, size=size, ex_disk_config="AUTO"
        )
        self.assertEqual(node.id, "26f7fbee-8ce1-4c28-887a-bfe8e4bb10fe")
        self.assertEqual(node.name, "racktest")
        self.assertEqual(node.extra["disk_config"], "AUTO")

    def test_create_node_with_ex_config_drive(self):
        OpenStack_1_1_MockHttp.type = "EX_CONFIG_DRIVE"
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(
            name="racktest", image=image, size=size, ex_config_drive=True
        )
        self.assertEqual(node.id, "26f7fbee-8ce1-4c28-887a-bfe8e4bb10fe")
        self.assertEqual(node.name, "racktest")
        self.assertTrue(node.extra["config_drive"])

    def test_create_node_from_bootable_volume(self):
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)

        node = self.driver.create_node(
            name="racktest",
            size=size,
            ex_blockdevicemappings=[
                {
                    "boot_index": 0,
                    "uuid": "ee7ee330-b454-4414-8e9f-c70c558dd3af",
                    "source_type": "volume",
                    "destination_type": "volume",
                    "delete_on_termination": False,
                }
            ],
        )

        self.assertEqual(node.id, "26f7fbee-8ce1-4c28-887a-bfe8e4bb10fe")
        self.assertEqual(node.name, "racktest")
        self.assertEqual(node.extra["password"], "racktestvJq7d3")
        self.assertEqual(node.extra["metadata"]["My Server Name"], "Apache1")

    def test_create_node_with_ex_files(self):
        OpenStack_2_0_MockHttp.type = "EX_FILES"
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        files = {"/file1": "content1", "/file2": "content2"}
        node = self.driver.create_node(name="racktest", image=image, size=size, ex_files=files)
        self.assertEqual(node.id, "26f7fbee-8ce1-4c28-887a-bfe8e4bb10fe")
        self.assertEqual(node.name, "racktest")
        OpenStack_2_0_MockHttp.type = "EX_FILES_NONE"
        node = self.driver.create_node(name="racktest", image=image, size=size)
        self.assertEqual(node.id, "26f7fbee-8ce1-4c28-887a-bfe8e4bb10fe")
        self.assertEqual(node.name, "racktest")

    def test_destroy_node(self):
        self.assertTrue(self.node.destroy())

    def test_reboot_node(self):
        self.assertTrue(self.node.reboot())

    def test_create_volume(self):
        volume = self.driver.create_volume(1, "test")
        self.assertEqual(volume.name, "test")
        self.assertEqual(volume.size, 1)

    def test_create_volume_passes_location_to_request_only_if_not_none(self):
        with patch.object(self.driver.connection, "request") as mock_request:
            self.driver.create_volume(1, "test", location="mylocation")
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertEqual(kwargs["data"]["volume"]["availability_zone"], "mylocation")

    def test_create_volume_does_not_pass_location_to_request_if_none(self):
        with patch.object(self.driver.connection, "request") as mock_request:
            self.driver.create_volume(1, "test")
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertFalse("availability_zone" in kwargs["data"]["volume"])

    def test_create_volume_passes_volume_type_to_request_only_if_not_none(self):
        with patch.object(self.driver.connection, "request") as mock_request:
            self.driver.create_volume(1, "test", ex_volume_type="myvolumetype")
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertEqual(kwargs["data"]["volume"]["volume_type"], "myvolumetype")

    def test_create_volume_does_not_pass_volume_type_to_request_if_none(self):
        with patch.object(self.driver.connection, "request") as mock_request:
            self.driver.create_volume(1, "test")
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertFalse("volume_type" in kwargs["data"]["volume"])

    def test_destroy_volume(self):
        volume = self.driver.ex_get_volume("cd76a3a1-c4ce-40f6-9b9f-07a61508938d")
        self.assertEqual(self.driver.destroy_volume(volume), True)

    def test_attach_volume(self):
        node = self.driver.list_nodes()[0]
        volume = self.driver.ex_get_volume("cd76a3a1-c4ce-40f6-9b9f-07a61508938d")
        self.assertEqual(self.driver.attach_volume(node, volume, "/dev/sdb"), True)

    def test_attach_volume_device_auto(self):
        node = self.driver.list_nodes()[0]
        volume = self.driver.ex_get_volume("cd76a3a1-c4ce-40f6-9b9f-07a61508938d")

        OpenStack_2_0_MockHttp.type = "DEVICE_AUTO"

        self.assertEqual(self.driver.attach_volume(node, volume, "auto"), True)

    def test_detach_volume(self):
        node = self.driver.list_nodes()[0]
        volume = self.driver.ex_get_volume("cd76a3a1-c4ce-40f6-9b9f-07a61508938d")
        self.assertEqual(self.driver.attach_volume(node, volume, "/dev/sdb"), True)
        self.assertEqual(self.driver.detach_volume(volume), True)

    def test_ex_set_password(self):
        self.assertTrue(self.driver.ex_set_password(self.node, "New1&53jPass"))

    def test_ex_rebuild(self):
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        success = self.driver.ex_rebuild(self.node, image=image)
        self.assertTrue(success)

    def test_ex_rebuild_with_ex_disk_config(self):
        image = NodeImage(id=58, name="Ubuntu 10.10 (intrepid)", driver=self.driver)
        node = Node(
            id=12066,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        success = self.driver.ex_rebuild(node, image=image, ex_disk_config="MANUAL")
        self.assertTrue(success)

    def test_ex_rebuild_with_ex_config_drive(self):
        image = NodeImage(id=58, name="Ubuntu 10.10 (intrepid)", driver=self.driver)
        node = Node(
            id=12066,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        success = self.driver.ex_rebuild(
            node, image=image, ex_disk_config="MANUAL", ex_config_drive=True
        )
        self.assertTrue(success)

    def test_ex_resize(self):
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        try:
            self.driver.ex_resize(self.node, size)
        except Exception as e:
            self.fail("An error was raised: " + repr(e))

    def test_ex_confirm_resize(self):
        try:
            self.driver.ex_confirm_resize(self.node)
        except Exception as e:
            self.fail("An error was raised: " + repr(e))

    def test_ex_revert_resize(self):
        try:
            self.driver.ex_revert_resize(self.node)
        except Exception as e:
            self.fail("An error was raised: " + repr(e))

    def test_create_image(self):
        image = self.driver.create_image(self.node, "new_image")
        self.assertEqual(image.name, "new_image")
        self.assertEqual(image.id, "4949f9ee-2421-4c81-8b49-13119446008b")

    def test_ex_set_server_name(self):
        old_node = Node(
            id="12064",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        new_node = self.driver.ex_set_server_name(old_node, "Bob")
        self.assertEqual("Bob", new_node.name)

    def test_ex_set_metadata(self):
        old_node = Node(
            id="12063",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        metadata = {"Image Version": "2.1", "Server Label": "Web Head 1"}
        returned_metadata = self.driver.ex_set_metadata(old_node, metadata)
        self.assertEqual(metadata, returned_metadata)

    def test_ex_get_metadata(self):
        node = Node(
            id="12063",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )

        metadata = {"Image Version": "2.1", "Server Label": "Web Head 1"}
        returned_metadata = self.driver.ex_get_metadata(node)
        self.assertEqual(metadata, returned_metadata)

    def test_ex_update_node(self):
        old_node = Node(
            id="12064",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )

        new_node = self.driver.ex_update_node(old_node, name="Bob")

        self.assertTrue(new_node)
        self.assertEqual("Bob", new_node.name)
        self.assertEqual("50.57.94.30", new_node.public_ips[0])

    def test_ex_get_node_details(self):
        node_id = "12064"
        node = self.driver.ex_get_node_details(node_id)
        self.assertEqual(node.id, "12064")
        self.assertEqual(node.name, "lc-test")

    def test_ex_get_node_details_returns_none_if_node_does_not_exist(self):
        node = self.driver.ex_get_node_details("does-not-exist")
        self.assertTrue(node is None)

    def test_ex_get_node_details_microversion_2_47(self):
        node_id = "12064247"
        node = self.driver.ex_get_node_details(node_id)
        self.assertEqual(node.id, "12064247")
        self.assertEqual(node.name, "lc-test")
        self.assertEqual(node.extra["flavor_details"]["vcpus"], 2)

    def test_ex_get_size(self):
        size_id = "7"
        size = self.driver.ex_get_size(size_id)
        self.assertEqual(size.id, size_id)
        self.assertEqual(size.name, "15.5GB slice")

    def test_ex_get_size_extra_specs(self):
        size_id = "7"
        extra_specs = self.driver.ex_get_size_extra_specs(size_id)
        self.assertEqual(extra_specs, {"hw:cpu_policy": "shared", "hw:numa_nodes": "1"})

    def test_get_image(self):
        image_id = "13"
        image = self.driver.get_image(image_id)
        self.assertEqual(image.id, image_id)
        self.assertEqual(image.name, "Windows 2008 SP2 x86 (B24)")
        self.assertIsNone(image.extra["serverId"])
        self.assertEqual(image.extra["minDisk"], "5")
        self.assertEqual(image.extra["minRam"], "256")
        self.assertIsNone(image.extra["visibility"])

    def test_delete_image(self):
        image = NodeImage(
            id="26365521-8c62-11f9-2c33-283d153ecc3a",
            name="My Backup",
            driver=self.driver,
        )
        result = self.driver.delete_image(image)
        self.assertTrue(result)

    def test_extract_image_id_from_url(self):
        url = "http://127.0.0.1/v1.1/68/images/1d4a8ea9-aae7-4242-a42d-5ff4702f2f14"
        url_two = "http://127.0.0.1/v1.1/68/images/13"
        image_id = self.driver._extract_image_id_from_url(url)
        image_id_two = self.driver._extract_image_id_from_url(url_two)
        self.assertEqual(image_id, "1d4a8ea9-aae7-4242-a42d-5ff4702f2f14")
        self.assertEqual(image_id_two, "13")

    def test_ex_rescue_with_password(self):
        node = Node(
            id=12064,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        n = self.driver.ex_rescue(node, "foo")
        self.assertEqual(n.extra["password"], "foo")

    def test_ex_rescue_no_password(self):
        node = Node(
            id=12064,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        n = self.driver.ex_rescue(node)
        self.assertEqual(n.extra["password"], "foo")

    def test_ex_unrescue(self):
        node = Node(
            id=12064,
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        result = self.driver.ex_unrescue(node)
        self.assertTrue(result)

    def test_ex_get_node_security_groups(self):
        node = Node(
            id="1c01300f-ef97-4937-8f03-ac676d6234be",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        security_groups = self.driver.ex_get_node_security_groups(node)
        self.assertEqual(len(security_groups), 2, "Wrong security groups count")

        security_group = security_groups[1]
        self.assertEqual(security_group.id, 4)
        self.assertEqual(security_group.tenant_id, "68")
        self.assertEqual(security_group.name, "ftp")
        self.assertEqual(security_group.description, "FTP Client-Server - Open 20-21 ports")
        self.assertEqual(security_group.rules[0].id, 1)
        self.assertEqual(security_group.rules[0].parent_group_id, 4)
        self.assertEqual(security_group.rules[0].ip_protocol, "tcp")
        self.assertEqual(security_group.rules[0].from_port, 20)
        self.assertEqual(security_group.rules[0].to_port, 21)
        self.assertEqual(security_group.rules[0].ip_range, "0.0.0.0/0")

    def test_ex_list_security_groups(self):
        security_groups = self.driver.ex_list_security_groups()
        self.assertEqual(len(security_groups), 2, "Wrong security groups count")

        security_group = security_groups[1]
        self.assertEqual(security_group.id, 4)
        self.assertEqual(security_group.tenant_id, "68")
        self.assertEqual(security_group.name, "ftp")
        self.assertEqual(security_group.description, "FTP Client-Server - Open 20-21 ports")
        self.assertEqual(security_group.rules[0].id, 1)
        self.assertEqual(security_group.rules[0].parent_group_id, 4)
        self.assertEqual(security_group.rules[0].ip_protocol, "tcp")
        self.assertEqual(security_group.rules[0].from_port, 20)
        self.assertEqual(security_group.rules[0].to_port, 21)
        self.assertEqual(security_group.rules[0].ip_range, "0.0.0.0/0")

    def test_ex_create_security_group(self):
        name = "test"
        description = "Test Security Group"
        security_group = self.driver.ex_create_security_group(name, description)

        self.assertEqual(security_group.id, 6)
        self.assertEqual(security_group.tenant_id, "68")
        self.assertEqual(security_group.name, name)
        self.assertEqual(security_group.description, description)
        self.assertEqual(len(security_group.rules), 0)

    def test_ex_delete_security_group(self):
        security_group = OpenStackSecurityGroup(
            id=6, tenant_id=None, name=None, description=None, driver=self.driver
        )
        result = self.driver.ex_delete_security_group(security_group)
        self.assertTrue(result)

    def test_ex_create_security_group_rule(self):
        security_group = OpenStackSecurityGroup(
            id=6, tenant_id=None, name=None, description=None, driver=self.driver
        )
        security_group_rule = self.driver.ex_create_security_group_rule(
            security_group, "tcp", 14, 16, "0.0.0.0/0"
        )

        self.assertEqual(security_group_rule.id, 2)
        self.assertEqual(security_group_rule.parent_group_id, 6)
        self.assertEqual(security_group_rule.ip_protocol, "tcp")
        self.assertEqual(security_group_rule.from_port, 14)
        self.assertEqual(security_group_rule.to_port, 16)
        self.assertEqual(security_group_rule.ip_range, "0.0.0.0/0")
        self.assertIsNone(security_group_rule.tenant_id)

    def test_ex_delete_security_group_rule(self):
        security_group_rule = OpenStackSecurityGroupRule(
            id=2,
            parent_group_id=None,
            ip_protocol=None,
            from_port=None,
            to_port=None,
            driver=self.driver,
        )
        result = self.driver.ex_delete_security_group_rule(security_group_rule)
        self.assertTrue(result)

    def test_list_key_pairs(self):
        keypairs = self.driver.list_key_pairs()
        self.assertEqual(len(keypairs), 2, "Wrong keypairs count")
        keypair = keypairs[1]
        self.assertEqual(keypair.name, "key2")
        self.assertEqual(keypair.fingerprint, "5d:66:33:ae:99:0f:fb:cb:86:f2:bc:ae:53:99:b6:ed")
        self.assertTrue(len(keypair.public_key) > 10)
        self.assertIsNone(keypair.private_key)

    def test_get_key_pair(self):
        key_pair = self.driver.get_key_pair(name="test-key-pair")

        self.assertEqual(key_pair.name, "test-key-pair")

    def test_get_key_pair_doesnt_exist(self):
        self.assertRaises(KeyPairDoesNotExistError, self.driver.get_key_pair, name="doesnt-exist")

    def test_create_key_pair(self):
        name = "key0"
        keypair = self.driver.create_key_pair(name=name)
        self.assertEqual(keypair.name, name)

        self.assertEqual(keypair.fingerprint, "80:f8:03:a7:8e:c1:c3:b1:7e:c5:8c:50:04:5e:1c:5b")
        self.assertTrue(len(keypair.public_key) > 10)
        self.assertTrue(len(keypair.private_key) > 10)

    def test_import_key_pair_from_file(self):
        name = "key3"
        path = os.path.join(os.path.dirname(__file__), "fixtures", "misc", "test_rsa.pub")

        with open(path) as fp:
            pub_key = fp.read()

        keypair = self.driver.import_key_pair_from_file(name=name, key_file_path=path)
        self.assertEqual(keypair.name, name)
        self.assertEqual(keypair.fingerprint, "97:10:a6:e7:92:65:7e:69:fe:e6:81:8f:39:3c:8f:5a")
        self.assertEqual(keypair.public_key, pub_key)
        self.assertIsNone(keypair.private_key)

    def test_import_key_pair_from_string(self):
        name = "key3"
        path = os.path.join(os.path.dirname(__file__), "fixtures", "misc", "test_rsa.pub")

        with open(path) as fp:
            pub_key = fp.read()

        keypair = self.driver.import_key_pair_from_string(name=name, key_material=pub_key)
        self.assertEqual(keypair.name, name)
        self.assertEqual(keypair.fingerprint, "97:10:a6:e7:92:65:7e:69:fe:e6:81:8f:39:3c:8f:5a")
        self.assertEqual(keypair.public_key, pub_key)
        self.assertIsNone(keypair.private_key)

    def test_delete_key_pair(self):
        keypair = OpenStackKeyPair(
            name="key1", fingerprint=None, public_key=None, driver=self.driver
        )
        result = self.driver.delete_key_pair(key_pair=keypair)
        self.assertTrue(result)

    def test_ex_list_floating_ip_pools(self):
        ret = self.driver.ex_list_floating_ip_pools()
        self.assertEqual(ret[0].name, "public")
        self.assertEqual(ret[1].name, "foobar")

    def test_ex_attach_floating_ip_to_node(self):
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(name="racktest", image=image, size=size)
        node.id = 4242
        ip = "42.42.42.42"

        self.assertTrue(self.driver.ex_attach_floating_ip_to_node(node, ip))

    def test_detach_floating_ip_from_node(self):
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(name="racktest", image=image, size=size)
        node.id = 4242
        ip = "42.42.42.42"

        self.assertTrue(self.driver.ex_detach_floating_ip_from_node(node, ip))

    def test_OpenStack_1_1_FloatingIpPool_list_floating_ips(self):
        pool = OpenStack_1_1_FloatingIpPool("foo", self.driver.connection)
        ret = pool.list_floating_ips()

        self.assertEqual(ret[0].id, "09ea1784-2f81-46dc-8c91-244b4df75bde")
        self.assertEqual(ret[0].pool, pool)
        self.assertEqual(ret[0].ip_address, "10.3.1.42")
        self.assertIsNone(ret[0].node_id)
        self.assertEqual(ret[1].id, "04c5336a-0629-4694-ba30-04b0bdfa88a4")
        self.assertEqual(ret[1].pool, pool)
        self.assertEqual(ret[1].ip_address, "10.3.1.1")
        self.assertEqual(ret[1].node_id, "fcfc96da-19e2-40fd-8497-f29da1b21143")

    def test_OpenStack_1_1_FloatingIpPool_get_floating_ip(self):
        pool = OpenStack_1_1_FloatingIpPool("foo", self.driver.connection)
        ret = pool.get_floating_ip("10.3.1.42")

        self.assertEqual(ret.id, "09ea1784-2f81-46dc-8c91-244b4df75bde")
        self.assertEqual(ret.pool, pool)
        self.assertEqual(ret.ip_address, "10.3.1.42")
        self.assertIsNone(ret.node_id)

        ret = pool.get_floating_ip("1.2.3.4")
        self.assertIsNone(ret)

    def test_OpenStack_1_1_FloatingIpPool_create_floating_ip(self):
        pool = OpenStack_1_1_FloatingIpPool("foo", self.driver.connection)
        ret = pool.create_floating_ip()

        self.assertEqual(ret.id, "09ea1784-2f81-46dc-8c91-244b4df75bde")
        self.assertEqual(ret.pool, pool)
        self.assertEqual(ret.ip_address, "10.3.1.42")
        self.assertIsNone(ret.node_id)

    def test_OpenStack_1_1_FloatingIpPool_delete_floating_ip(self):
        pool = OpenStack_1_1_FloatingIpPool("foo", self.driver.connection)
        ip = OpenStack_1_1_FloatingIpAddress("foo-bar-id", "42.42.42.42", pool)

        self.assertTrue(pool.delete_floating_ip(ip))

    def test_OpenStack_1_1_FloatingIpAddress_delete(self):
        pool = OpenStack_1_1_FloatingIpPool("foo", self.driver.connection)
        pool.delete_floating_ip = Mock()
        ip = OpenStack_1_1_FloatingIpAddress("foo-bar-id", "42.42.42.42", pool)

        ip.pool.delete_floating_ip()

        self.assertEqual(pool.delete_floating_ip.call_count, 1)

    def test_OpenStack_2_FloatingIpPool_list_floating_ips(self):
        pool = OpenStack_2_FloatingIpPool(1, "foo", self.driver.connection)
        ret = pool.list_floating_ips()

        self.assertEqual(ret[0].id, "09ea1784-2f81-46dc-8c91-244b4df75bde")
        self.assertEqual(ret[0].get_pool(), pool)
        self.assertEqual(ret[0].ip_address, "10.3.1.42")
        self.assertEqual(ret[0].get_node_id(), None)
        self.assertEqual(ret[1].id, "04c5336a-0629-4694-ba30-04b0bdfa88a4")
        self.assertEqual(ret[1].get_pool(), pool)
        self.assertEqual(ret[1].ip_address, "10.3.1.1")
        self.assertEqual(ret[1].get_node_id(), "fcfc96da-19e2-40fd-8497-f29da1b21143")
        self.assertEqual(ret[2].id, "123c5336a-0629-4694-ba30-04b0bdfa88a4")
        self.assertEqual(ret[2].get_pool(), pool)
        self.assertEqual(ret[2].ip_address, "10.3.1.2")
        self.assertEqual(ret[2].get_node_id(), "cb4fba64-19e2-40fd-8497-f29da1b21143")
        self.assertEqual(ret[3].id, "123c5336a-0629-4694-ba30-04b0bdfa88a4")
        self.assertEqual(ret[3].get_pool(), pool)
        self.assertEqual(ret[3].ip_address, "10.3.1.3")
        self.assertEqual(ret[3].get_node_id(), "cb4fba64-19e2-40fd-8497-f29da1b21143")

    def test_OpenStack_2_FloatingIpPool_get_floating_ip(self):
        pool = OpenStack_2_FloatingIpPool(1, "foo", self.driver.connection)
        ret = pool.get_floating_ip("10.3.1.42")

        self.assertEqual(ret.id, "09ea1784-2f81-46dc-8c91-244b4df75bde")
        self.assertEqual(ret.pool, pool)
        self.assertEqual(ret.ip_address, "10.3.1.42")
        self.assertEqual(ret.node_id, None)

    def test_OpenStack_2_FloatingIpPool_create_floating_ip(self):
        pool = OpenStack_2_FloatingIpPool(1, "foo", self.driver.connection)
        ret = pool.create_floating_ip()

        self.assertEqual(ret.id, "09ea1784-2f81-46dc-8c91-244b4df75bde")
        self.assertEqual(ret.pool, pool)
        self.assertEqual(ret.ip_address, "10.3.1.42")
        self.assertEqual(ret.node_id, None)

    def test_OpenStack_2_FloatingIpPool_delete_floating_ip(self):
        pool = OpenStack_2_FloatingIpPool(1, "foo", self.driver.connection)
        ip = OpenStack_1_1_FloatingIpAddress("foo-bar-id", "42.42.42.42", pool)

        self.assertTrue(pool.delete_floating_ip(ip))

    def test_OpenStack_2_FloatingIpAddress_delete(self):
        pool = OpenStack_2_FloatingIpPool(1, "foo", self.driver.connection)
        pool.delete_floating_ip = Mock()
        ip = OpenStack_1_1_FloatingIpAddress("foo-bar-id", "42.42.42.42", pool)

        ip.pool.delete_floating_ip()

        self.assertEqual(pool.delete_floating_ip.call_count, 1)

    def test_ex_get_metadata_for_node(self):
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(name="foo", image=image, size=size)

        metadata = self.driver.ex_get_metadata_for_node(node)
        self.assertEqual(metadata["My Server Name"], "Apache1")
        self.assertEqual(len(metadata), 1)

    def test_ex_pause_node(self):
        node = Node(
            id="12063",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ret = self.driver.ex_pause_node(node)
        self.assertTrue(ret is True)

    def test_ex_unpause_node(self):
        node = Node(
            id="12063",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ret = self.driver.ex_unpause_node(node)
        self.assertTrue(ret is True)

    def test_ex_stop_node(self):
        node = Node(
            id="12063",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ret = self.driver.ex_stop_node(node)
        self.assertTrue(ret is True)

    def test_ex_start_node(self):
        node = Node(
            id="12063",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ret = self.driver.ex_start_node(node)
        self.assertTrue(ret is True)

    def test_ex_suspend_node(self):
        node = Node(
            id="12063",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ret = self.driver.ex_suspend_node(node)
        self.assertTrue(ret is True)

    def test_ex_resume_node(self):
        node = Node(
            id="12063",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ret = self.driver.ex_resume_node(node)
        self.assertTrue(ret is True)

    def test_ex_get_console_output(self):
        node = Node(
            id="12086",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        resp = self.driver.ex_get_console_output(node)
        expected_output = "FAKE CONSOLE OUTPUT\nANOTHER\nLAST LINE"
        self.assertEqual(resp["output"], expected_output)

    def test_ex_list_snapshots(self):
        if self.driver_type.type == "rackspace":
            self.conn_class.type = "RACKSPACE"

        snapshots = self.driver.ex_list_snapshots()
        self.assertEqual(len(snapshots), 3)
        self.assertEqual(snapshots[0].created, datetime.datetime(2012, 2, 29, 3, 50, 7, tzinfo=UTC))
        self.assertEqual(snapshots[0].extra["created"], "2012-02-29T03:50:07Z")
        self.assertEqual(snapshots[0].extra["name"], "snap-001")
        self.assertEqual(snapshots[0].name, "snap-001")
        self.assertEqual(snapshots[0].state, VolumeSnapshotState.AVAILABLE)

        # invalid date is parsed as None
        assert snapshots[2].created is None

    def test_ex_get_snapshot(self):
        if self.driver_type.type == "rackspace":
            self.conn_class.type = "RACKSPACE"

        snapshot = self.driver.ex_get_snapshot("3fbbcccf-d058-4502-8844-6feeffdf4cb5")
        self.assertEqual(snapshot.created, datetime.datetime(2012, 2, 29, 3, 50, 7, tzinfo=UTC))
        self.assertEqual(snapshot.extra["created"], "2012-02-29T03:50:07Z")
        self.assertEqual(snapshot.extra["name"], "snap-001")
        self.assertEqual(snapshot.name, "snap-001")
        self.assertEqual(snapshot.state, VolumeSnapshotState.AVAILABLE)

    def test_list_volume_snapshots(self):
        volume = self.driver.list_volumes()[0]

        # rackspace needs a different mocked response for snapshots, but not for volumes

        if self.driver_type.type == "rackspace":
            self.conn_class.type = "RACKSPACE"

        snapshots = self.driver.list_volume_snapshots(volume)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].id, "4fbbdccf-e058-6502-8844-6feeffdf4cb5")

    def test_create_volume_snapshot(self):
        volume = self.driver.list_volumes()[0]

        if self.driver_type.type == "rackspace":
            self.conn_class.type = "RACKSPACE"

        ret = self.driver.create_volume_snapshot(
            volume, "Test Volume", ex_description="This is a test", ex_force=True
        )
        self.assertEqual(ret.id, "3fbbcccf-d058-4502-8844-6feeffdf4cb5")

    def test_ex_create_snapshot(self):
        volume = self.driver.list_volumes()[0]

        if self.driver_type.type == "rackspace":
            self.conn_class.type = "RACKSPACE"

        ret = self.driver.ex_create_snapshot(
            volume, "Test Volume", description="This is a test", force=True
        )
        self.assertEqual(ret.id, "3fbbcccf-d058-4502-8844-6feeffdf4cb5")

    def test_ex_create_snapshot_does_not_post_optional_parameters_if_none(self):
        volume = self.driver.list_volumes()[0]
        with patch.object(self.driver, "_to_snapshot"):
            with patch.object(self.driver.connection, "request") as mock_request:
                self.driver.create_volume_snapshot(
                    volume, name=None, ex_description=None, ex_force=True
                )

        name, args, kwargs = mock_request.mock_calls[0]
        self.assertFalse("display_name" in kwargs["data"]["snapshot"])
        self.assertFalse("display_description" in kwargs["data"]["snapshot"])

    def test_destroy_volume_snapshot(self):
        if self.driver_type.type == "rackspace":
            self.conn_class.type = "RACKSPACE"

        snapshot = self.driver.ex_list_snapshots()[0]
        ret = self.driver.destroy_volume_snapshot(snapshot)
        self.assertTrue(ret)

    def test_ex_delete_snapshot(self):
        if self.driver_type.type == "rackspace":
            self.conn_class.type = "RACKSPACE"

        snapshot = self.driver.ex_list_snapshots()[0]
        ret = self.driver.ex_delete_snapshot(snapshot)
        self.assertTrue(ret)


class OpenStack_2_Tests(OpenStack_1_1_Tests):
    driver_klass = OpenStack_2_NodeDriver
    driver_type = OpenStack_2_NodeDriver
    driver_kwargs = {
        "ex_force_auth_version": "2.0",
        "ex_force_auth_url": "https://auth.api.example.com",
    }

    def setUp(self):
        super().setUp()
        self.driver_klass.image_connectionCls.conn_class = OpenStack_2_0_MockHttp
        self.driver_klass.image_connectionCls.auth_url = "https://auth.api.example.com"
        # normally authentication happens lazily, but we force it here
        self.driver.image_connection._populate_hosts_and_request_paths()

        self.driver_klass.network_connectionCls.conn_class = OpenStack_2_0_MockHttp
        self.driver_klass.network_connectionCls.auth_url = "https://auth.api.example.com"
        # normally authentication happens lazily, but we force it here
        self.driver.network_connection._populate_hosts_and_request_paths()

        self.driver_klass.volumev2_connectionCls.conn_class = OpenStack_2_0_MockHttp
        self.driver_klass.volumev2_connectionCls.auth_url = "https://auth.api.example.com"
        # normally authentication happens lazily, but we force it here
        self.driver.volumev2_connection._populate_hosts_and_request_paths()

        self.driver_klass.volumev3_connectionCls.conn_class = OpenStack_2_0_MockHttp
        self.driver_klass.volumev3_connectionCls.auth_url = "https://auth.api.example.com"
        # normally authentication happens lazily, but we force it here
        self.driver.volumev3_connection._populate_hosts_and_request_paths()

    def test__paginated_request_single_page(self):
        snapshots = self.driver._paginated_request(
            "/snapshots/detail", "snapshots", self.driver._get_volume_connection()
        )["snapshots"]

        self.assertEqual(len(snapshots), 3)
        self.assertEqual(snapshots[0]["name"], "snap-001")

    def test__paginated_request_two_pages(self):
        snapshots = self.driver._paginated_request(
            "/snapshots/detail?unit_test=paginate",
            "snapshots",
            self.driver._get_volume_connection(),
        )["snapshots"]

        self.assertEqual(len(snapshots), 6)
        self.assertEqual(snapshots[0]["name"], "snap-101")
        self.assertEqual(snapshots[3]["name"], "snap-001")

    def test_list_images_with_pagination_invalid_response_no_infinite_loop(self):
        # "next" attribute matches the current page, but it shouldn't result in
        # an infinite loop
        OpenStack_2_0_MockHttp.type = "invalid_next"
        ret = self.driver.list_images()
        self.assertEqual(len(ret), 2)

    # NOTE: We use a smaller limit to speed tests up.
    @mock.patch("libcloud.compute.drivers.openstack.PAGINATION_LIMIT", 10)
    def test__paginated_request_raises_if_stuck_in_a_loop(self):
        with pytest.raises(OpenStackException):
            self.driver._paginated_request(
                "/snapshots/detail?unit_test=pagination_loop",
                "snapshots",
                self.driver._get_volume_connection(),
            )

    def test_ex_force_auth_token_passed_to_connection(self):
        base_url = "https://servers.api.rackspacecloud.com/v1.1/slug"
        kwargs = {
            "ex_force_auth_version": "2.0",
            "ex_force_auth_token": "preset-auth-token",
            "ex_force_auth_url": "https://auth.api.example.com",
            "ex_force_base_url": base_url,
        }

        driver = self.driver_type(*self.driver_args, **kwargs)
        driver.list_nodes()

        self.assertEqual(kwargs["ex_force_auth_token"], driver.connection.auth_token)
        self.assertEqual("servers.api.rackspacecloud.com", driver.connection.host)
        self.assertEqual("/v1.1/slug", driver.connection.request_path)
        self.assertEqual(443, driver.connection.port)

    def test_get_image(self):
        image_id = "f24a3c1b-d52a-4116-91da-25b3eee8f55e"
        image = self.driver.get_image(image_id)
        self.assertEqual(image.id, image_id)
        self.assertEqual(image.name, "hypernode")
        self.assertIsNone(image.extra["serverId"])
        self.assertEqual(image.extra["minDisk"], 40)
        self.assertEqual(image.extra["minRam"], 0)
        self.assertEqual(image.extra["visibility"], "shared")

    def test_list_images(self):
        images = self.driver.list_images()
        self.assertEqual(len(images), 3, "Wrong images count")

        image = images[0]
        self.assertEqual(image.id, "f24a3c1b-d52a-4116-91da-25b3eee8f55e")
        self.assertEqual(image.name, "hypernode")
        self.assertEqual(image.extra["updated"], "2017-11-28T10:19:49Z")
        self.assertEqual(image.extra["created"], "2017-09-11T13:00:05Z")
        self.assertEqual(image.extra["status"], "active")
        self.assertEqual(image.extra["os_type"], "linux")
        self.assertEqual(image.extra["os_version"], "16.04")
        self.assertEqual(image.extra["os_distro"], "ubuntu")
        self.assertIsNone(image.extra["serverId"])
        self.assertEqual(image.extra["minDisk"], 40)
        self.assertEqual(image.extra["minRam"], 0)

    def test_ex_update_image(self):
        image_id = "f24a3c1b-d52a-4116-91da-25b3eee8f55e"
        data = {"op": "replace", "path": "/visibility", "value": "shared"}
        image = self.driver.ex_update_image(image_id, data)
        self.assertEqual(image.name, "hypernode")
        self.assertIsNone(image.extra["serverId"])
        self.assertEqual(image.extra["minDisk"], 40)
        self.assertEqual(image.extra["minRam"], 0)
        self.assertEqual(image.extra["visibility"], "shared")

    def test_ex_list_image_members(self):
        image_id = "d9a9cd9a-278a-444c-90a6-d24b8c688a63"
        image_member_id = "016926dff12345e8b10329f24c99745b"
        image_members = self.driver.ex_list_image_members(image_id)
        self.assertEqual(len(image_members), 30, "Wrong image member count")

        image_member = image_members[0]
        self.assertEqual(image_member.id, image_member_id)
        self.assertEqual(image_member.image_id, image_id)
        self.assertEqual(image_member.state, NodeImageMemberState.ACCEPTED)
        self.assertEqual(image_member.created, "2017-01-12T12:31:50Z")
        self.assertEqual(image_member.extra["updated"], "2017-01-12T12:31:54Z")
        self.assertEqual(image_member.extra["schema"], "/v2/schemas/member")

    def test_ex_create_image_member(self):
        image_id = "9af1a54e-a1b2-4df8-b747-4bec97abc799"
        image_member_id = "e2151b1fe02d4a8a2d1f5fc331522c0a"
        image_member = self.driver.ex_create_image_member(image_id, image_member_id)

        self.assertEqual(image_member.id, image_member_id)
        self.assertEqual(image_member.image_id, image_id)
        self.assertEqual(image_member.state, NodeImageMemberState.PENDING)
        self.assertEqual(image_member.created, "2018-03-02T14:19:38Z")
        self.assertEqual(image_member.extra["updated"], "2018-03-02T14:19:38Z")
        self.assertEqual(image_member.extra["schema"], "/v2/schemas/member")

    def test_ex_get_image_member(self):
        image_id = "d9a9cd9a-278a-444c-90a6-d24b8c688a63"
        image_member_id = "016926dff12345e8b10329f24c99745b"
        image_member = self.driver.ex_get_image_member(image_id, image_member_id)

        self.assertEqual(image_member.id, image_member_id)
        self.assertEqual(image_member.image_id, image_id)
        self.assertEqual(image_member.state, NodeImageMemberState.ACCEPTED)
        self.assertEqual(image_member.created, "2017-01-12T12:31:50Z")
        self.assertEqual(image_member.extra["updated"], "2017-01-12T12:31:54Z")
        self.assertEqual(image_member.extra["schema"], "/v2/schemas/member")

    def test_ex_accept_image_member(self):
        image_id = "8af1a54e-a1b2-4df8-b747-4bec97abc799"
        image_member_id = "e2151b1fe02d4a8a2d1f5fc331522c0a"
        image_member = self.driver.ex_accept_image_member(image_id, image_member_id)

        self.assertEqual(image_member.id, image_member_id)
        self.assertEqual(image_member.image_id, image_id)
        self.assertEqual(image_member.state, NodeImageMemberState.ACCEPTED)
        self.assertEqual(image_member.created, "2018-03-02T14:19:38Z")
        self.assertEqual(image_member.extra["updated"], "2018-03-02T14:20:37Z")
        self.assertEqual(image_member.extra["schema"], "/v2/schemas/member")

    def test_ex_list_networks(self):
        networks = self.driver.ex_list_networks()
        network = networks[0]

        self.assertEqual(len(networks), 2)
        self.assertEqual(network.name, "net1")
        self.assertEqual(network.extra["subnets"], ["54d6f61d-db07-451c-9ab3-b9609b6b6f0b"])

    def test_ex_get_network(self):
        network = self.driver.ex_get_network("cc2dad14-827a-feea-416b-f13e50511a0a")

        self.assertEqual(network.id, "cc2dad14-827a-feea-416b-f13e50511a0a")
        self.assertTrue(isinstance(network, OpenStackNetwork))
        self.assertEqual(network.name, "net2")
        self.assertEqual(network.extra["is_default"], False)
        self.assertEqual(network.extra["tags"], ["tag1,tag2"])

        network = self.driver.ex_get_network("e4e207ac-6707-432b-82b9-244f6859c394")

        self.assertEqual(network.id, "e4e207ac-6707-432b-82b9-244f6859c394")
        self.assertTrue(isinstance(network, OpenStackNetwork))
        self.assertEqual(network.name, "net2")
        self.assertNotIn("tags", network.extra)

    def test_ex_list_subnets(self):
        subnets = self.driver.ex_list_subnets()
        subnet = subnets[0]

        self.assertEqual(len(subnets), 2)
        self.assertEqual(subnet.name, "private-subnet")
        self.assertEqual(subnet.cidr, "10.0.0.0/24")

    def test_ex_create_subnet(self):
        network = self.driver.ex_list_networks()[0]
        subnet = self.driver.ex_create_subnet(
            "name", network, "10.0.0.0/24", ip_version=4, dns_nameservers=["10.0.0.01"]
        )

        self.assertEqual(subnet.name, "name")
        self.assertEqual(subnet.cidr, "10.0.0.0/24")

    def test_ex_delete_subnet(self):
        subnet = self.driver.ex_list_subnets()[0]
        self.assertTrue(self.driver.ex_delete_subnet(subnet=subnet))

    def test_ex_update_subnet(self):
        subnet = self.driver.ex_list_subnets()[0]
        subnet = self.driver.ex_update_subnet(subnet, name="net2")
        self.assertEqual(subnet.name, "name")

    def test_ex_list_network(self):
        networks = self.driver.ex_list_networks()
        network = networks[0]

        self.assertEqual(len(networks), 2)
        self.assertEqual(network.name, "net1")

    def test_ex_create_network(self):
        network = self.driver.ex_create_network(name="net1", cidr="127.0.0.0/24")
        self.assertEqual(network.name, "net1")

    def test_ex_delete_network(self):
        network = self.driver.ex_list_networks()[0]
        self.assertTrue(self.driver.ex_delete_network(network=network))

    def test_ex_list_ports(self):
        ports = self.driver.ex_list_ports()

        port = ports[0]
        self.assertEqual(port.id, "126da55e-cfcb-41c8-ae39-a26cb8a7e723")
        self.assertEqual(port.state, OpenStack_2_PortInterfaceState.BUILD)
        self.assertEqual(port.created, "2018-07-04T14:38:18Z")
        self.assertEqual(port.extra["network_id"], "123c8a8c-6427-4e8f-a805-2035365f4d43")
        self.assertEqual(port.extra["project_id"], "abcdec85bee34bb0a44ab8255eb36abc")
        self.assertEqual(port.extra["tenant_id"], "abcdec85bee34bb0a44ab8255eb36abc")
        self.assertEqual(port.extra["name"], "")

    def test_ex_create_port(self):
        network = OpenStackNetwork(
            id="123c8a8c-6427-4e8f-a805-2035365f4d43",
            name="test-network",
            cidr="1.2.3.4",
            driver=self.driver,
        )
        port = self.driver.ex_create_port(
            network=network,
            description="Some port description",
            name="Some port name",
            admin_state_up=True,
        )

        self.assertEqual(port.id, "126da55e-cfcb-41c8-ae39-a26cb8a7e723")
        self.assertEqual(port.state, OpenStack_2_PortInterfaceState.BUILD)
        self.assertEqual(port.created, "2018-07-04T14:38:18Z")
        self.assertEqual(port.extra["network_id"], "123c8a8c-6427-4e8f-a805-2035365f4d43")
        self.assertEqual(port.extra["project_id"], "abcdec85bee34bb0a44ab8255eb36abc")
        self.assertEqual(port.extra["tenant_id"], "abcdec85bee34bb0a44ab8255eb36abc")
        self.assertEqual(port.extra["admin_state_up"], True)
        self.assertEqual(port.extra["name"], "Some port name")
        self.assertEqual(port.extra["description"], "Some port description")

    def test_ex_get_port(self):
        port = self.driver.ex_get_port("126da55e-cfcb-41c8-ae39-a26cb8a7e723")

        self.assertEqual(port.id, "126da55e-cfcb-41c8-ae39-a26cb8a7e723")
        self.assertEqual(port.state, OpenStack_2_PortInterfaceState.BUILD)
        self.assertEqual(port.created, "2018-07-04T14:38:18Z")
        self.assertEqual(port.extra["network_id"], "123c8a8c-6427-4e8f-a805-2035365f4d43")
        self.assertEqual(port.extra["project_id"], "abcdec85bee34bb0a44ab8255eb36abc")
        self.assertEqual(port.extra["tenant_id"], "abcdec85bee34bb0a44ab8255eb36abc")
        self.assertEqual(port.extra["name"], "Some port name")

    def test_ex_delete_port(self):
        ports = self.driver.ex_list_ports()
        port = ports[0]

        ret = self.driver.ex_delete_port(port)

        self.assertTrue(ret)

    def test_ex_update_port(self):
        port = self.driver.ex_get_port("126da55e-cfcb-41c8-ae39-a26cb8a7e723")
        ret = self.driver.ex_update_port(port, port_security_enabled=False)
        self.assertEqual(ret.extra["name"], "Some port name")

    def test_ex_update_port_allowed_address_pairs(self):
        allowed_address_pairs = [{"ip_address": "1.2.3.4"}, {"ip_address": "2.3.4.5"}]
        port = self.driver.ex_get_port("126da55e-cfcb-41c8-ae39-a26cb8a7e723")
        ret = self.driver.ex_update_port(port, allowed_address_pairs=allowed_address_pairs)
        self.assertEqual(ret.extra["allowed_address_pairs"], allowed_address_pairs)

    def test_detach_port_interface(self):
        node = Node(
            id="1c01300f-ef97-4937-8f03-ac676d6234be",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ports = self.driver.ex_list_ports()
        port = ports[0]

        ret = self.driver.ex_detach_port_interface(node, port)

        self.assertTrue(ret)

    def test_attach_port_interface(self):
        node = Node(
            id="1c01300f-ef97-4937-8f03-ac676d6234be",
            name=None,
            state=None,
            public_ips=None,
            private_ips=None,
            driver=self.driver,
        )
        ports = self.driver.ex_list_ports()
        port = ports[0]

        ret = self.driver.ex_attach_port_interface(node, port)

        self.assertTrue(ret)

    def test_list_volumes(self):
        volumes = self.driver.list_volumes()
        self.assertEqual(len(volumes), 2)
        volume = volumes[0]

        self.assertEqual("6edbc2f4-1507-44f8-ac0d-eed1d2608d38", volume.id)
        self.assertEqual("test-volume-attachments", volume.name)
        self.assertEqual(StorageVolumeState.INUSE, volume.state)
        self.assertEqual(2, volume.size)
        self.assertEqual(
            volume.extra,
            {
                "description": "",
                "attachments": [
                    {
                        "attachment_id": "3b4db356-253d-4fab-bfa0-e3626c0b8405",
                        "id": "6edbc2f4-1507-44f8-ac0d-eed1d2608d38",
                        "device": "/dev/vdb",
                        "server_id": "f4fda93b-06e0-4743-8117-bc8bcecd651b",
                        "volume_id": "6edbc2f4-1507-44f8-ac0d-eed1d2608d38",
                    }
                ],
                "snapshot_id": None,
                "state": "in-use",
                "location": "nova",
                "volume_type": "lvmdriver-1",
                "metadata": {},
                "created_at": "2013-06-24T11:20:13.000000",
            },
        )

        # also test that unknown state resolves to StorageVolumeState.UNKNOWN
        volume = volumes[1]
        self.assertEqual("cfcec3bc-b736-4db5-9535-4c24112691b5", volume.id)
        self.assertEqual("test_volume", volume.name)
        self.assertEqual(50, volume.size)
        self.assertEqual(StorageVolumeState.UNKNOWN, volume.state)
        self.assertEqual(
            volume.extra,
            {
                "description": "some description",
                "attachments": [],
                "snapshot_id": "01f48111-7866-4cd2-986a-e92683c4a363",
                "state": "some-unknown-state",
                "location": "nova",
                "volume_type": None,
                "metadata": {},
                "created_at": "2013-06-21T12:39:02.000000",
            },
        )

    def test_create_volume_passes_location_to_request_only_if_not_none(self):
        with patch.object(self.driver._get_volume_connection(), "request") as mock_request:
            self.driver.create_volume(1, "test", location="mylocation")
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertEqual(kwargs["data"]["volume"]["availability_zone"], "mylocation")

    def test_create_volume_does_not_pass_location_to_request_if_none(self):
        with patch.object(self.driver._get_volume_connection(), "request") as mock_request:
            self.driver.create_volume(1, "test")
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertFalse("availability_zone" in kwargs["data"]["volume"])

    def test_create_volume_passes_volume_type_to_request_only_if_not_none(self):
        with patch.object(self.driver._get_volume_connection(), "request") as mock_request:
            self.driver.create_volume(1, "test", ex_volume_type="myvolumetype")
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertEqual(kwargs["data"]["volume"]["volume_type"], "myvolumetype")

    def test_create_volume_does_not_pass_volume_type_to_request_if_none(self):
        with patch.object(self.driver._get_volume_connection(), "request") as mock_request:
            self.driver.create_volume(1, "test")
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertFalse("volume_type" in kwargs["data"]["volume"])

    def test_create_volume_passes_image_ref_to_request_only_if_not_none(self):
        with patch.object(self.driver._get_volume_connection(), "request") as mock_request:
            self.driver.create_volume(
                1, "test", ex_image_ref="353c4bd2-b28f-4857-9b7b-808db4397d03"
            )
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertEqual(
                kwargs["data"]["volume"]["imageRef"],
                "353c4bd2-b28f-4857-9b7b-808db4397d03",
            )

    def test_create_volume_does_not_pass_image_ref_to_request_if_none(self):
        with patch.object(self.driver._get_volume_connection(), "request") as mock_request:
            self.driver.create_volume(1, "test")
            name, args, kwargs = mock_request.mock_calls[0]
            self.assertFalse("imageRef" in kwargs["data"]["volume"])

    def test_ex_create_snapshot_does_not_post_optional_parameters_if_none(self):
        volume = self.driver.list_volumes()[0]
        with patch.object(self.driver, "_to_snapshot"):
            with patch.object(self.driver._get_volume_connection(), "request") as mock_request:
                self.driver.create_volume_snapshot(
                    volume, name=None, ex_description=None, ex_force=True
                )

        name, args, kwargs = mock_request.mock_calls[0]
        self.assertFalse("display_name" in kwargs["data"]["snapshot"])
        self.assertFalse("display_description" in kwargs["data"]["snapshot"])

    def test_ex_list_routers(self):
        routers = self.driver.ex_list_routers()
        router = routers[0]

        self.assertEqual(len(routers), 2)
        self.assertEqual(router.name, "router2")
        self.assertEqual(router.status, "ACTIVE")
        self.assertEqual(
            router.extra["routes"],
            [{"destination": "179.24.1.0/24", "nexthop": "172.24.3.99"}],
        )

    def test_ex_create_router(self):
        router = self.driver.ex_create_router("router1", admin_state_up=True)

        self.assertEqual(router.name, "router1")

    def test_ex_delete_router(self):
        router = self.driver.ex_list_routers()[1]
        self.assertTrue(self.driver.ex_delete_router(router=router))

    def test_manage_router_interfaces(self):
        router = self.driver.ex_list_routers()[1]
        port = self.driver.ex_list_ports()[0]
        subnet = self.driver.ex_list_subnets()[0]
        self.assertTrue(self.driver.ex_add_router_port(router, port))
        self.assertTrue(self.driver.ex_del_router_port(router, port))
        self.assertTrue(self.driver.ex_add_router_subnet(router, subnet))
        self.assertTrue(self.driver.ex_del_router_subnet(router, subnet))

    def test_detach_volume(self):
        node = self.driver.list_nodes()[0]
        volume = self.driver.ex_get_volume("abc6a3a1-c4ce-40f6-9b9f-07a61508938d")
        self.assertEqual(self.driver.attach_volume(node, volume, "/dev/sdb"), True)
        self.assertEqual(self.driver.detach_volume(volume), True)

    def test_ex_remove_security_group_from_node(self):
        security_group = OpenStackSecurityGroup("sgid", None, "sgname", "", self.driver)
        node = Node("1000", "node", None, [], [], self.driver)
        ret = self.driver.ex_remove_security_group_from_node(security_group, node)
        self.assertTrue(ret)

    def test_force_net_url(self):
        d = OpenStack_2_NodeDriver(
            "user",
            "correct_password",
            ex_force_auth_version="2.0_password",
            ex_force_auth_url="http://x.y.z.y:5000",
            ex_force_network_url="http://network.com:9696",
            ex_tenant_name="admin",
        )
        self.assertEqual(d._ex_force_base_url, None)

    def test_ex_get_quota_set(self):
        quota_set = self.driver.ex_get_quota_set("tenant_id")
        self.assertEqual(quota_set.cores.limit, 20)
        self.assertEqual(quota_set.cores.in_use, 1)
        self.assertEqual(quota_set.cores.reserved, 0)

    def test_ex_get_network_quota(self):
        quota_set = self.driver.ex_get_network_quotas("tenant_id")
        self.assertEqual(quota_set.floatingip.limit, 2)
        self.assertEqual(quota_set.floatingip.in_use, 1)
        self.assertEqual(quota_set.floatingip.reserved, 0)

    def test_ex_get_volume_quota(self):
        quota_set = self.driver.ex_get_volume_quotas("tenant_id")
        self.assertEqual(quota_set.gigabytes.limit, 1000)
        self.assertEqual(quota_set.gigabytes.in_use, 10)
        self.assertEqual(quota_set.gigabytes.reserved, 0)

    def test_ex_list_server_groups(self):
        server_groups = self.driver.ex_list_server_groups()
        self.assertEqual(len(server_groups), 2)
        self.assertEqual(server_groups[1].name, "server_group_name")

    def test_ex_get_server_group(self):
        server_group = self.driver.ex_get_server_group("616fb98f-46ca-475e-917e-2563e5a8cd19")
        self.assertEqual(server_group.name, "server_group_name")
        self.assertEqual(server_group.policy, "anti-affinity")

    def test_ex_del_server_group(self):
        server_group = OpenStack_2_ServerGroup(
            "616fb98f-46ca-475e-917e-2563e5a8cd19", "name", "anti-affinity"
        )
        res = self.driver.ex_del_server_group(server_group)
        self.assertTrue(res)

    def test_ex_add_server_group(self):
        server_group = self.driver.ex_add_server_group("server_group_name", "anti-affinity")
        self.assertEqual(server_group.name, "server_group_name")
        self.assertEqual(server_group.policy, "anti-affinity")

    def test_ex_list_floating_ips(self):
        ret = self.driver.ex_list_floating_ips()

        self.assertEqual(ret[0].id, "09ea1784-2f81-46dc-8c91-244b4df75bde")
        self.assertEqual(ret[0].get_pool(), None)
        self.assertEqual(ret[0].ip_address, "10.3.1.42")
        self.assertEqual(ret[0].get_node_id(), None)
        self.assertEqual(ret[1].id, "04c5336a-0629-4694-ba30-04b0bdfa88a4")
        self.assertEqual(ret[1].get_pool(), None)
        self.assertEqual(ret[1].ip_address, "10.3.1.1")
        self.assertEqual(ret[1].get_node_id(), "fcfc96da-19e2-40fd-8497-f29da1b21143")
        self.assertEqual(ret[2].id, "123c5336a-0629-4694-ba30-04b0bdfa88a4")
        self.assertEqual(ret[2].get_pool(), None)
        self.assertEqual(ret[2].ip_address, "10.3.1.2")
        self.assertEqual(ret[2].get_node_id(), "cb4fba64-19e2-40fd-8497-f29da1b21143")
        self.assertEqual(ret[3].id, "123c5336a-0629-4694-ba30-04b0bdfa88a4")
        self.assertEqual(ret[3].get_pool(), None)
        self.assertEqual(ret[3].ip_address, "10.3.1.3")
        self.assertEqual(ret[3].get_node_id(), "cb4fba64-19e2-40fd-8497-f29da1b21143")
        self.assertEqual(ret[4].id, "123c5336a-0629-4694-ba30-04b0bdfa88a4")
        self.assertEqual(ret[4].get_pool(), None)
        self.assertEqual(ret[4].ip_address, "10.3.1.5")
        self.assertEqual(ret[4].get_node_id(), "cb4fba64-19e2-40fd-8497-f29da1b21143")

    def test_ex_get_floating_ip(self):
        float_ip = self.driver.ex_get_floating_ip("10.0.0.1")

        self.assertEqual(float_ip.ip_address, "10.3.1.21")
        self.assertEqual(float_ip.id, "04c5336a-0629-4694-ba30-04b0bdfa88a4")

    def test_ex_create_floating_ip(self):
        ret = self.driver.ex_create_floating_ip("public")

        self.assertEqual(ret.id, "09ea1784-2f81-46dc-8c91-244b4df75bde")
        self.assertEqual(ret.pool.name, "public")
        self.assertEqual(ret.ip_address, "10.3.1.42")
        self.assertEqual(ret.node_id, None)

    def test_ex_delete_floating_ip(self):
        ip = OpenStack_1_1_FloatingIpAddress("foo-bar-id", "42.42.42.42", None)
        self.assertTrue(self.driver.ex_delete_floating_ip(ip))

    def test_ex_attach_floating_ip_to_node(self):
        image = NodeImage(id=11, name="Ubuntu 8.10 (intrepid)", driver=self.driver)
        size = NodeSize(1, "256 slice", None, None, None, None, driver=self.driver)
        node = self.driver.create_node(name="racktest", image=image, size=size)
        node.id = 4242
        ip = "42.42.42.42"
        port_id = "ce531f90-199f-48c0-816c-13e38010b442"

        self.assertTrue(self.driver.ex_attach_floating_ip_to_node(node, ip, port_id))


class OpenStack_1_1_FactoryMethodTests(OpenStack_1_1_Tests):
    should_list_locations = False
    should_list_volumes = True

    driver_klass = OpenStack_1_1_NodeDriver
    driver_type = get_driver(Provider.OPENSTACK)
    driver_args = OPENSTACK_PARAMS + ("1.1",)
    driver_kwargs = {"ex_force_auth_version": "2.0"}


class OpenStack_1_1_MockHttp(MockHttp, unittest.TestCase):
    fixtures = ComputeFileFixtures("openstack_v1_1")
    auth_fixtures = OpenStackFixtures()
    json_content_headers = {"content-type": "application/json; charset=UTF-8"}

    def _v2_0_tokens(self, method, url, body, headers):
        body = self.auth_fixtures.load("_v2_0__auth.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_0(self, method, url, body, headers):
        headers = {
            "x-auth-token": "FE011C19-CF86-4F87-BE5D-9229145D7A06",
            "x-server-management-url": "https://api.example.com/v1.1/slug",
        }

        return (httplib.NO_CONTENT, "", headers, httplib.responses[httplib.NO_CONTENT])

    def _v1_1_slug_servers_detail(self, method, url, body, headers):
        body = self.fixtures.load("_servers_detail.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_servers_detail_ERROR_STATE_NO_IMAGE_ID(self, method, url, body, headers):
        body = self.fixtures.load("_servers_detail_ERROR_STATE.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v2_1337_servers_detail_UNAUTHORIZED(self, method, url, body, headers):
        return (httplib.UNAUTHORIZED, "", {}, httplib.responses[httplib.UNAUTHORIZED])

    def _v2_1337_servers_does_not_exist(self, *args, **kwargs):
        return httplib.NOT_FOUND, None, {}, httplib.responses[httplib.NOT_FOUND]

    def _v1_1_slug_flavors_detail(self, method, url, body, headers):
        body = self.fixtures.load("_flavors_detail.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_images_detail(self, method, url, body, headers):
        body = self.fixtures.load("_images_detail.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_servers(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_servers_create.json")
        elif method == "GET":
            body = self.fixtures.load("_servers.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_servers_26f7fbee_8ce1_4c28_887a_bfe8e4bb10fe(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_servers_26f7fbee_8ce1_4c28_887a_bfe8e4bb10fe.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_servers_12065_action(self, method, url, body, headers):
        if method != "POST":
            self.fail("HTTP method other than POST to action URL")

        return (httplib.ACCEPTED, "", {}, httplib.responses[httplib.ACCEPTED])

    def _v1_1_slug_servers_12064_action(self, method, url, body, headers):
        if method != "POST":
            self.fail("HTTP method other than POST to action URL")

        if "createImage" in json.loads(body):
            return (
                httplib.ACCEPTED,
                "",
                {
                    "location": "http://127.0.0.1/v1.1/68/images/4949f9ee-2421-4c81-8b49-13119446008b"
                },
                httplib.responses[httplib.ACCEPTED],
            )
        elif "rescue" in json.loads(body):
            return (
                httplib.OK,
                '{"adminPass": "foo"}',
                {},
                httplib.responses[httplib.OK],
            )

        return (httplib.ACCEPTED, "", {}, httplib.responses[httplib.ACCEPTED])

    def _v1_1_slug_servers_12066_action(self, method, url, body, headers):
        if method != "POST":
            self.fail("HTTP method other than POST to action URL")

        if "rebuild" not in json.loads(body):
            self.fail("Did not get expected action (rebuild) in action URL")

        self.assertTrue(
            '"OS-DCF:diskConfig": "MANUAL"' in body,
            msg="Manual disk configuration option was not specified in rebuild body: " + body,
        )

        return (httplib.ACCEPTED, "", {}, httplib.responses[httplib.ACCEPTED])

    def _v1_1_slug_servers_12065(self, method, url, body, headers):
        if method == "DELETE":
            return (httplib.ACCEPTED, "", {}, httplib.responses[httplib.ACCEPTED])
        else:
            raise NotImplementedError()

    def _v1_1_slug_servers_12064(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_servers_12064.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "PUT":
            body = self.fixtures.load("_servers_12064_updated_name_bob.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "DELETE":
            return (httplib.ACCEPTED, "", {}, httplib.responses[httplib.ACCEPTED])
        else:
            raise NotImplementedError()

    def _v1_1_slug_servers_12062(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_servers_12064.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v1_1_slug_servers_12064247(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_servers_12064247.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v1_1_slug_servers_12063_metadata(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_servers_12063_metadata_two_keys.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "PUT":
            body = self.fixtures.load("_servers_12063_metadata_two_keys.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v1_1_slug_servers_EX_DISK_CONFIG(self, method, url, body, headers):
        if method == "POST":
            body = u(body)
            self.assertTrue(body.find('"OS-DCF:diskConfig": "AUTO"'))
            body = self.fixtures.load("_servers_create_disk_config.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v1_1_slug_servers_EX_FILES(self, method, url, body, headers):
        if method == "POST":
            body = u(body)
            personality = [
                {"path": "/file1", "contents": "Y29udGVudDE="},
                {"path": "/file2", "contents": "Y29udGVudDI="},
            ]
            self.assertEqual(json.loads(body)["server"]["personality"], personality)
            body = self.fixtures.load("_servers_create.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v1_1_slug_servers_EX_FILES_NONE(self, method, url, body, headers):
        if method == "POST":
            body = u(body)
            self.assertNotIn('"personality"', body)
            body = self.fixtures.load("_servers_create.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v1_1_slug_flavors_7(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_flavors_7.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v1_1_slug_images_13(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_images_13.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_images_f24a3c1b_d52a_4116_91da_25b3eee8f55e(self, method, url, body, headers):
        if method == "GET" or method == "PATCH":
            body = self.fixtures.load("_images_f24a3c1b-d52a-4116-91da-25b3eee8f55e.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_images_d9a9cd9a_278a_444c_90a6_d24b8c688a63_members(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_images_d9a9cd9a_278a_444c_90a6_d24b8c688a63_members.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_images_9af1a54e_a1b2_4df8_b747_4bec97abc799_members(
        self, method, url, body, headers
    ):
        if method == "POST":
            body = self.fixtures.load("_images_9af1a54e_a1b2_4df8_b747_4bec97abc799_members.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_images_8af1a54e_a1b2_4df8_b747_4bec97abc799_members_e2151b1fe02d4a8a2d1f5fc331522c0a(
        self, method, url, body, headers
    ):
        if method == "PUT":
            body = self.fixtures.load("_images_8af1a54e_a1b2_4df8_b747_4bec97abc799_members.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_images_d9a9cd9a_278a_444c_90a6_d24b8c688a63_members_016926dff12345e8b10329f24c99745b(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load(
                "_images_d9a9cd9a_278a_444c_90a6_d24b8c688a63_members_016926dff12345e8b10329f24c99745b.json"
            )

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_images(self, method, url, body, headers):
        if method == "GET":
            # 2nd (and last) page of images

            if "marker=e7a40226-3523-4f0f-87d8-d8dc91bbf4a3" in url:
                body = self.fixtures.load("_images_v2_page2.json")
            else:
                # first page of images
                body = self.fixtures.load("_images_v2.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_images_invalid_next(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_images_v2_invalid_next.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v1_1_slug_images_26365521_8c62_11f9_2c33_283d153ecc3a(self, method, url, body, headers):
        if method == "DELETE":
            return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])
        else:
            raise NotImplementedError()

    def _v1_1_slug_images_4949f9ee_2421_4c81_8b49_13119446008b(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_images_4949f9ee_2421_4c81_8b49_13119446008b.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_images_4949f9ee_2421_4c81_8b49_13119446008b(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_images_f24a3c1b-d52a-4116-91da-25b3eee8f55d.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_0_ports(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_ports_v2.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "POST":
            body = self.fixtures.load("_port_v2.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_v2_0_ports_126da55e_cfcb_41c8_ae39_a26cb8a7e723(self, method, url, body, headers):
        if method == "DELETE":
            return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])
        elif method == "GET":
            body = self.fixtures.load("_port_v2.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "PUT":
            if body:
                body = self.fixtures.load("_port_v2.json")

                return (
                    httplib.OK,
                    body,
                    self.json_content_headers,
                    httplib.responses[httplib.OK],
                )
            else:
                return (
                    httplib.INTERNAL_SERVER_ERROR,
                    "",
                    {},
                    httplib.responses[httplib.INTERNAL_SERVER_ERROR],
                )
        else:
            raise NotImplementedError()

    def _v2_1337_servers_12065_os_volume_attachments_DEVICE_AUTO(self, method, url, body, headers):
        # test_attach_volume_device_auto

        if method == "POST":
            if "rackspace" not in self.__class__.__name__.lower():
                body = json.loads(body)
                self.assertEqual(body["volumeAttachment"]["device"], None)

            return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])
        else:
            raise NotImplementedError()

    def _v2_1337_servers_1c01300f_ef97_4937_8f03_ac676d6234be_os_interface_126da55e_cfcb_41c8_ae39_a26cb8a7e723(
        self, method, url, body, headers
    ):
        if method == "DELETE":
            return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])
        else:
            raise NotImplementedError()

    def _v2_1337_servers_1c01300f_ef97_4937_8f03_ac676d6234be_os_interface(
        self, method, url, body, headers
    ):
        if method == "POST":
            return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])
        else:
            raise NotImplementedError()

    def _v2_1337_servers_26f7fbee_8ce1_4c28_887a_bfe8e4bb10fe_EX_FILES(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_servers_26f7fbee_8ce1_4c28_887a_bfe8e4bb10fe.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v2_1337_servers_26f7fbee_8ce1_4c28_887a_bfe8e4bb10fe_EX_FILES_NONE(
        self, method, url, body, headers
    ):
        return self._v2_1337_servers_26f7fbee_8ce1_4c28_887a_bfe8e4bb10fe_EX_FILES(
            method, url, body, headers
        )

    def _v1_1_slug_servers_1c01300f_ef97_4937_8f03_ac676d6234be_os_security_groups(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load(
                "_servers_1c01300f-ef97-4937-8f03-ac676d6234be_os-security-groups.json"
            )
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_security_groups(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_os_security_groups.json")
        elif method == "POST":
            body = self.fixtures.load("_os_security_groups_create.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_security_groups_6(self, method, url, body, headers):
        if method == "DELETE":
            return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])
        else:
            raise NotImplementedError()

    def _v1_1_slug_os_security_group_rules(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_os_security_group_rules_create.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_security_group_rules_2(self, method, url, body, headers):
        if method == "DELETE":
            return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])
        else:
            raise NotImplementedError()

    def _v1_1_slug_os_keypairs(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_os_keypairs.json")
        elif method == "POST":
            if "public_key" in body:
                body = self.fixtures.load("_os_keypairs_create_import.json")
            else:
                body = self.fixtures.load("_os_keypairs_create.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_keypairs_test_key_pair(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_os_keypairs_get_one.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_keypairs_doesnt_exist(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_os_keypairs_not_found.json")
        else:
            raise NotImplementedError()

        return (
            httplib.NOT_FOUND,
            body,
            self.json_content_headers,
            httplib.responses[httplib.NOT_FOUND],
        )

    def _v1_1_slug_os_keypairs_key1(self, method, url, body, headers):
        if method == "DELETE":
            return (httplib.ACCEPTED, "", {}, httplib.responses[httplib.ACCEPTED])
        else:
            raise NotImplementedError()

    def _v1_1_slug_os_volumes(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_os_volumes.json")
        elif method == "POST":
            body = self.fixtures.load("_os_volumes_create.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_volumes_cd76a3a1_c4ce_40f6_9b9f_07a61508938d(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_os_volumes_cd76a3a1_c4ce_40f6_9b9f_07a61508938d.json")
        elif method == "DELETE":
            body = ""
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_servers_12065_os_volume_attachments(self, method, url, body, headers):
        if method == "POST":
            if "rackspace" not in self.__class__.__name__.lower():
                body = json.loads(body)
                self.assertEqual(body["volumeAttachment"]["device"], "/dev/sdb")

            body = self.fixtures.load("_servers_12065_os_volume_attachments.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_servers_12065_os_volume_attachments_cd76a3a1_c4ce_40f6_9b9f_07a61508938d(
        self, method, url, body, headers
    ):
        if method == "DELETE":
            body = ""
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_floating_ip_pools(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_floating_ip_pools.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v1_1_slug_os_floating_ips_foo_bar_id(self, method, url, body, headers):
        if method == "DELETE":
            body = ""

            return (
                httplib.ACCEPTED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v1_1_slug_os_floating_ips(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_floating_ips.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "POST":
            body = self.fixtures.load("_floating_ip.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v1_1_slug_servers_4242_action(self, method, url, body, headers):
        if method == "POST":
            body = ""

            return (
                httplib.ACCEPTED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v1_1_slug_os_networks(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_os_networks.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "POST":
            body = self.fixtures.load("_os_networks_POST.json")

            return (
                httplib.ACCEPTED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        raise NotImplementedError()

    def _v1_1_slug_os_networks_f13e5051_feea_416b_827a_1a0acc2dad14(
        self, method, url, body, headers
    ):
        if method == "DELETE":
            body = ""

            return (
                httplib.ACCEPTED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        raise NotImplementedError()

    def _v1_1_slug_servers_72258_action(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_servers_suspend.json")

            return (
                httplib.ACCEPTED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v1_1_slug_servers_12063_action(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_servers_unpause.json")

            return (
                httplib.ACCEPTED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v1_1_slug_servers_12086_action(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_servers_12086_console_output.json")

            return (
                httplib.ACCEPTED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v1_1_slug_os_snapshots(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_os_snapshots.json")
        elif method == "POST":
            body = self.fixtures.load("_os_snapshots_create.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_snapshots_3fbbcccf_d058_4502_8844_6feeffdf4cb5(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_os_snapshot.json")
            status_code = httplib.OK
        elif method == "DELETE":
            body = ""
            status_code = httplib.NO_CONTENT
        else:
            raise NotImplementedError()

        return (
            status_code,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_snapshots_3fbbcccf_d058_4502_8844_6feeffdf4cb5_RACKSPACE(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_os_snapshot_rackspace.json")
            status_code = httplib.OK
        elif method == "DELETE":
            body = ""
            status_code = httplib.NO_CONTENT
        else:
            raise NotImplementedError()

        return (
            status_code,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v1_1_slug_os_snapshots_RACKSPACE(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_os_snapshots_rackspace.json")
        elif method == "POST":
            body = self.fixtures.load("_os_snapshots_create_rackspace.json")
        else:
            raise NotImplementedError()

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v2_1337_v2_0_networks(self, method, url, body, headers):
        if method == "GET":
            if "router:external=True" in url:
                body = self.fixtures.load("_v2_0__networks_public.json")

                return (
                    httplib.OK,
                    body,
                    self.json_content_headers,
                    httplib.responses[httplib.OK],
                )
            else:
                body = self.fixtures.load("_v2_0__networks.json")

                return (
                    httplib.OK,
                    body,
                    self.json_content_headers,
                    httplib.responses[httplib.OK],
                )
        elif method == "POST":
            body = self.fixtures.load("_v2_0__networks_POST.json")

            return (
                httplib.ACCEPTED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        raise NotImplementedError()

    def _v2_1337_v2_0_networks_cc2dad14_827a_feea_416b_f13e50511a0a(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_v2_0__network.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        raise NotImplementedError()

    def _v2_1337_v2_0_networks_e4e207ac_6707_432b_82b9_244f6859c394(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_v2_0__network_no_tags.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        raise NotImplementedError()

    def _v2_1337_v2_0_networks_d32019d3_bc6e_4319_9c1d_6722fc136a22(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_v2_0__networks_POST.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_subnets_08eae331_0402_425a_923c_34f7cfe39c1b(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_v2_0__subnet.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

        if method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "PUT":
            body = self.fixtures.load("_v2_0__subnet.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_subnets(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_v2_0__subnet.json")

            return (
                httplib.CREATED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            body = self.fixtures.load("_v2_0__subnets.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v3_1337_volumes_detail(self, method, url, body, headers):
        body = self.fixtures.load("_v2_0__volumes.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v3_1337_volumes(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_v2_0__volume.json")

            return (
                httplib.CREATED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v3_1337_volumes_cd76a3a1_c4ce_40f6_9b9f_07a61508938d(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_v2_0__volume.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

        if method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v3_1337_volumes_abc6a3a1_c4ce_40f6_9b9f_07a61508938d(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_v2_0__volume_abc6a3a1_c4ce_40f6_9b9f_07a61508938d.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

        if method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v3_1337_snapshots_detail(self, method, url, body, headers):
        if (
            "unit_test=paginate" in url and "marker" not in url
        ) or "unit_test=pagination_loop" in url:
            body = self.fixtures.load("_v2_0__snapshots_paginate_start.json")
        else:
            body = self.fixtures.load("_v2_0__snapshots.json")

        return (
            httplib.OK,
            body,
            self.json_content_headers,
            httplib.responses[httplib.OK],
        )

    def _v3_1337_snapshots(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_v2_0__snapshot.json")

            return (
                httplib.CREATED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v3_1337_snapshots_3fbbcccf_d058_4502_8844_6feeffdf4cb5(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_v2_0__snapshot.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

        if method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_security_groups(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_v2_0__security_group.json")

            return (
                httplib.CREATED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

        if method == "GET":
            body = self.fixtures.load("_v2_0__security_groups.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_security_groups_6(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_v2_0__security_group.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

        if method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_security_group_rules(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_v2_0__security_group_rule.json")

            return (
                httplib.CREATED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_security_group_rules_2(self, method, url, body, headers):
        if method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_floatingips(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_v2_0__floatingip.json")

            return (
                httplib.CREATED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

        if method == "GET":
            if "floating_network_id=" in url:
                body = self.fixtures.load("_v2_0__floatingips_net_id.json")
            elif "floating_ip_address" in url:
                body = self.fixtures.load("_v2_0__floatingips_ip_id.json")
            else:
                body = self.fixtures.load("_v2_0__floatingips.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_floatingips_foo_bar_id(self, method, url, body, headers):
        if method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_floatingips_09ea1784_2f81_46dc_8c91_244b4df75bde(
        self, method, url, body, headers
    ):
        if method == "PUT":
            self.assertIn(
                body,
                [
                    '{"floatingip": {"port_id": "ce531f90-199f-48c0-816c-13e38010b442"}}',
                    '{"floatingip": {"port_id": null}}',
                ],
            )
            body = ""

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_routers_f8a44de0_fc8e_45df_93c7_f79bf3b01c95(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_v2_0__router.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

        if method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_routers(self, method, url, body, headers):
        if method == "POST":
            body = self.fixtures.load("_v2_0__router.json")

            return (
                httplib.CREATED,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            body = self.fixtures.load("_v2_0__routers.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_routers_f8a44de0_fc8e_45df_93c7_f79bf3b01c95_add_router_interface(
        self, method, url, body, headers
    ):
        if method == "PUT":
            body = self.fixtures.load("_v2_0__router_interface.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_v2_0_routers_f8a44de0_fc8e_45df_93c7_f79bf3b01c95_remove_router_interface(
        self, method, url, body, headers
    ):
        if method == "PUT":
            body = self.fixtures.load("_v2_0__router_interface.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_os_quota_sets_tenant_id_detail(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_v2_0__quota_set.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_flavors_7_os_extra_specs(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_flavor_extra_specs.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        else:
            raise NotImplementedError()

    def _v2_1337_servers_1000_action(self, method, url, body, headers):
        if method != "POST" or body != '{"removeSecurityGroup": {"name": "sgname"}}':
            raise NotImplementedError(body)

        return httplib.ACCEPTED, None, {}, httplib.responses[httplib.ACCEPTED]

    def _v2_1337_v2_0_quotas_tenant_id_details_json(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_v2_0__network_quota.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v3_1337_os_quota_sets_tenant_id(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_v3_0__volume_quota.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_os_server_groups(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_v2_0__os_server_groups.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "POST":
            body = self.fixtures.load("_v2_0__os_server_group.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_os_server_groups_616fb98f_46ca_475e_917e_2563e5a8cd19(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("_v2_0__os_server_group.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )
        elif method == "DELETE":
            body = ""

            return (
                httplib.NO_CONTENT,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )

    def _v2_1337_servers_4242_os_interface(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_servers_os_intefaces.json")

            return (
                httplib.OK,
                body,
                self.json_content_headers,
                httplib.responses[httplib.OK],
            )


# This exists because the nova compute url in devstack has v2 in there but the v1.1 fixtures
# work fine.


class OpenStack_2_0_MockHttp(OpenStack_1_1_MockHttp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        methods1 = OpenStack_1_1_MockHttp.__dict__

        names1 = [m for m in methods1 if m.find("_v1_1") == 0]

        for name in names1:
            method = methods1[name]
            new_name = name.replace("_v1_1_slug_", "_v2_1337_")
            setattr(self, new_name, method_type(method, self, OpenStack_2_0_MockHttp))

    def _v2_0_tenants_UNAUTHORIZED(self, method, url, body, headers):
        return (httplib.UNAUTHORIZED, "", {}, httplib.responses[httplib.UNAUTHORIZED])


class OpenStack_AllAuthVersions_MockHttp(MockHttp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Lazy import to avoid cyclic dependency issue
        from libcloud.test.common.test_openstack_identity import (
            OpenStackIdentity_2_0_MockHttp,
            OpenStackIdentity_3_0_MockHttp,
            OpenStackIdentity_3_0_AppCred_MockHttp,
        )

        self.mock_http = OpenStackMockHttp(*args, **kwargs)
        self.mock_http_1_1 = OpenStack_1_1_MockHttp(*args, **kwargs)
        self.mock_http_2_0 = OpenStack_2_0_MockHttp(*args, **kwargs)
        self.mock_http_2_0_identity = OpenStackIdentity_2_0_MockHttp(*args, **kwargs)
        self.mock_http_3_0_identity = OpenStackIdentity_3_0_MockHttp(*args, **kwargs)
        self.mock_http_3_0_appcred_identity = OpenStackIdentity_3_0_AppCred_MockHttp(
            *args, **kwargs
        )

    def _v1_0_slug_servers_detail(self, method, url, body, headers):
        return self.mock_http_1_1._v1_1_slug_servers_detail(
            method=method, url=url, body=body, headers=headers
        )

    def _v1_1_auth(self, method, url, body, headers):
        return self.mock_http._v1_1_auth(method=method, url=url, body=body, headers=headers)

    def _v2_0_tokens(self, method, url, body, headers):
        return self.mock_http_2_0._v2_0_tokens(method=method, url=url, body=body, headers=headers)

    def _v2_1337_servers_detail(self, method, url, body, headers):
        return self.mock_http_2_0._v2_1337_servers_detail(
            method=method, url=url, body=body, headers=headers
        )

    def _v2_0_tenants(self, method, url, body, headers):
        return self.mock_http_2_0_identity._v2_0_tenants(
            method=method, url=url, body=body, headers=headers
        )

    def _v2_9c4693dce56b493b9b83197d900f7fba_servers_detail(self, method, url, body, headers):
        return self.mock_http_1_1._v1_1_slug_servers_detail(
            method=method, url=url, body=body, headers=headers
        )

    def _v3_OS_FEDERATION_identity_providers_user_name_protocols_tenant_name_auth(
        self, method, url, body, headers
    ):
        return self.mock_http_3_0_identity._v3_OS_FEDERATION_identity_providers_test_user_id_protocols_test_tenant_auth(
            method=method, url=url, body=body, headers=headers
        )

    def _v3_auth_tokens(self, method, url, body, headers):
        if "application_credential" in body:
            return self.mock_http_3_0_appcred_identity._v3_auth_tokens(
                method=method, url=url, body=body, headers=headers
            )
        elif "token" in body:
            return self.mock_http_3_0_identity._v3_auth_tokens(
                method=method, url=url, body=body, headers=headers
            )
        else:
            return self.mock_http_3_0_identity._v3_auth_tokens(
                method=method, url=url, body=body, headers=headers
            )

    def _v3_0_auth_tokens(self, method, url, body, headers):
        return self.mock_http_3_0_identity._v3_0_auth_tokens(
            method=method, url=url, body=body, headers=headers
        )

    def _v3_auth_projects(self, method, url, body, headers):
        return self.mock_http_3_0_identity._v3_auth_projects(
            method=method, url=url, body=body, headers=headers
        )


class OpenStack_1_1_Auth_2_0_Tests(OpenStack_1_1_Tests):
    driver_args = OPENSTACK_PARAMS + ("1.1",)
    driver_kwargs = {"ex_force_auth_version": "2.0"}

    def setUp(self):
        self.driver_klass.connectionCls.conn_class = OpenStack_2_0_MockHttp
        self.driver_klass.connectionCls.auth_url = "https://auth.api.example.com"
        OpenStackMockHttp.type = None
        OpenStack_1_1_MockHttp.type = None
        OpenStack_2_0_MockHttp.type = None
        self.driver = self.create_driver()
        # normally authentication happens lazily, but we force it here
        self.driver.connection._populate_hosts_and_request_paths()
        clear_pricing_data()
        self.node = self.driver.list_nodes()[1]

    def test_auth_user_info_is_set(self):
        self.driver.connection._populate_hosts_and_request_paths()
        self.assertEqual(
            self.driver.connection.auth_user_info,
            {
                "id": "7",
                "name": "testuser",
                "roles": [
                    {
                        "description": "Default Role.",
                        "id": "identity:default",
                        "name": "identity:default",
                    }
                ],
            },
        )


class OpenStack_AuthVersions_Tests(unittest.TestCase):
    def setUp(self):
        # monkeypatch get_endpoint because the base openstack driver doesn't actually
        # work with old devstack but this class/tests are still used by the rackspace
        # driver
        self.originalGetEndpoint = OpenStack_1_1_NodeDriver.connectionCls.get_endpoint
        self.originalConnectionCls = OpenStack_1_1_NodeDriver.connectionCls

        def get_endpoint(*args, **kwargs):
            return "https://servers.api.rackspacecloud.com/v1.0/slug"

        OpenStack_1_1_NodeDriver.connectionCls.get_endpoint = get_endpoint
        OpenStack_1_1_NodeDriver.connectionCls.conn_class = OpenStack_AllAuthVersions_MockHttp

        OpenStackMockHttp.type = None
        OpenStack_1_1_MockHttp.type = None
        OpenStack_2_0_MockHttp.type = None

    def tearDown(self):
        OpenStack_1_1_NodeDriver.connectionCls.get_endpoint = self.originalGetEndpoint
        OpenStack_1_1_NodeDriver.connectionCls.conn_class = self.originalConnectionCls

        OpenStackMockHttp.type = None
        OpenStack_1_1_MockHttp.type = None
        OpenStack_2_0_MockHttp.type = None

    def test_ex_force_auth_version_all_possible_values(self):
        """
        Test case which verifies that the driver can be correctly instantiated using all the
        supported API versions.
        """
        cls = get_driver(Provider.OPENSTACK)

        for auth_version in AUTH_VERSIONS_WITH_EXPIRES:
            driver_kwargs = {}

            if auth_version in ["1.1", "3.0"]:
                # 1.1 is old and deprecated, 3.0 is not exposed directly to the end user

                continue

            user_id = OPENSTACK_PARAMS[0]
            key = OPENSTACK_PARAMS[1]

            if auth_version.startswith("3.x"):
                driver_kwargs["ex_domain_name"] = "test_domain"
                driver_kwargs["ex_tenant_domain_id"] = "test_tenant_domain_id"
                driver_kwargs["ex_force_service_region"] = "regionOne"
                driver_kwargs["ex_tenant_name"] = "tenant-name"

            if auth_version == "3.x_oidc_access_token":
                key = "test_key"
                driver_kwargs["ex_domain_name"] = None

            elif auth_version == "3.x_appcred":
                user_id = "appcred_id"
                key = "appcred_secret"

            driver = cls(
                user_id,
                key,
                ex_force_auth_url="http://x.y.z.y:5000",
                ex_force_auth_version=auth_version,
                **driver_kwargs,
            )
            nodes = driver.list_nodes()
            self.assertTrue(len(nodes) >= 1)


class OpenStackMockAuthCache(OpenStackAuthenticationCache):
    def __init__(self):
        self.reset()

    def get(self, key):
        return self.store.get(key)

    def put(self, key, context):
        self.store[key] = context

    def clear(self, key):
        if key in self.store:
            del self.store[key]

    def reset(self):
        self.store = {}

    def __len__(self):
        return len(self.store)


if __name__ == "__main__":
    sys.exit(unittest.main())
