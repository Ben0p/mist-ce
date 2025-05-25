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
import sys
import copy
import json

from libcloud.test import MockHttp, unittest
from libcloud.utils.py3 import httplib
from libcloud.compute.base import NodeLocation, NodeAuthSSHKey, NodeAuthPassword
from libcloud.compute.types import NodeState
from libcloud.test.file_fixtures import ComputeFileFixtures
from libcloud.compute.drivers.kubevirt import (
    KubeVirtNodeSize,
    KubeVirtNodeImage,
    KubeVirtNodeDriver,
    _memory_in_MB,
    _deep_merge_dict,
)
from libcloud.test.common.test_kubernetes import KubernetesAuthTestCaseMixin


class KubeVirtTestCase(unittest.TestCase, KubernetesAuthTestCaseMixin):
    driver_cls = KubeVirtNodeDriver
    fixtures = ComputeFileFixtures("kubevirt")

    def setUp(self):
        KubeVirtNodeDriver.connectionCls.conn_class = KubeVirtMockHttp
        self.driver = KubeVirtNodeDriver(
            key="user", secret="pass", secure=True, host="foo", port=6443
        )

    def test_list_locations(self):
        locations = self.driver.list_locations()
        self.assertEqual(len(locations), 6)
        self.assertEqual(locations[0].name, "default")
        self.assertEqual(locations[1].name, "kube-node-lease")
        self.assertEqual(locations[2].name, "kube-public")
        self.assertEqual(locations[3].name, "kube-system")

        namespace4 = locations[0].driver.list_locations()[4].name
        self.assertEqual(namespace4, "kubevirt")
        id4 = locations[2].driver.list_locations()[4].id
        self.assertEqual(id4, "e6d3d7e8-0ee5-428b-8e17-5187779e5627")

    def test_list_nodes(self):
        nodes = self.driver.list_nodes(location="default")
        id0 = "74fd7665-fbd6-4565-977c-96bd21fb785a"

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].extra["namespace"], "default")
        valid_node_states = {NodeState.RUNNING, NodeState.PENDING, NodeState.STOPPED}
        self.assertTrue(nodes[0].state in valid_node_states)
        self.assertEqual(nodes[0].name, "testvm")
        self.assertEqual(nodes[0].id, id0)

    def test_destroy_node(self):
        nodes = self.driver.list_nodes()
        to_destroy = nodes[-1]
        resp = self.driver.destroy_node(to_destroy)
        self.assertTrue(resp)

    def test_start_node(self):
        nodes = self.driver.list_nodes()
        r1 = self.driver.start_node(nodes[0])
        self.assertTrue(r1)

    def test_stop_node(self):
        nodes = self.driver.list_nodes()
        r1 = self.driver.stop_node(nodes[0])
        self.assertTrue(r1)

    def test_reboot_node(self):
        nodes = self.driver.list_nodes()
        for node in nodes:
            if node.name == "testvm":
                resp = self.driver.reboot_node(node)

        self.assertTrue(resp)

    def test_create_node(self):
        node = self.driver.create_node(
            name="testcreatenode",
            size=KubeVirtNodeSize(cpu=1, ram=128),
            image=KubeVirtNodeImage("kubevirt/cirros-registry-disk-demo"),
            ex_disks=[
                {
                    "name": "anpvc",
                    "bus": "virtio",
                    "device": "disk",
                    "disk_type": "persistentVolumeClaim",
                    "volume_spec": {"claim_name": "mypvc2"},
                },
            ],
            ex_network={
                "name": "netw1",
                "network_type": "pod",
                "interface": "masquerade",
            },
        )
        self.assertEqual(node.name, "testcreatenode")
        self.assertEqual(node.size.extra["cpu"], 1)
        self.assertEqual(node.size.ram, 128)

    def test_create_node_default_net(self):
        node = self.driver.create_node(
            name="testcreatenode",
            size=KubeVirtNodeSize(cpu=1, ram=128),
            image=KubeVirtNodeImage("kubevirt/cirros-registry-disk-demo"),
            ex_disks=[
                {
                    "name": "anpvc",
                    "bus": "virtio",
                    "device": "disk",
                    "disk_type": "persistentVolumeClaim",
                    "volume_spec": {"claim_name": "mypvc2"},
                },
            ],
        )
        self.assertEqual(node.name, "testcreatenode")
        self.assertEqual(node.size.extra["cpu"], 1)
        self.assertEqual(node.size.ram, 128)

    def test_create_node_legacy_3_tuple_net(self):
        node = self.driver.create_node(
            name="testcreatenode",
            size=KubeVirtNodeSize(cpu=1, ram=128),
            image=KubeVirtNodeImage("kubevirt/cirros-registry-disk-demo"),
            ex_disks=[
                {
                    "name": "anpvc",
                    "bus": "virtio",
                    "device": "disk",
                    "disk_type": "persistentVolumeClaim",
                    "volume_spec": {"claim_name": "mypvc2"},
                },
            ],
            ex_network=("pod", "masquerade", "netw1"),
        )
        self.assertEqual(node.name, "testcreatenode")
        self.assertEqual(node.size.extra["cpu"], 1)
        self.assertEqual(node.size.ram, 128)

    def test_create_node_auth_and_cloud_init(self):
        try:
            self.driver.create_node(
                name="testcreatenode",
                size=KubeVirtNodeSize(cpu=1, ram=128),
                image=KubeVirtNodeImage("kubevirt/cirros-registry-disk-demo"),
                auth=NodeAuthPassword("password"),
                ex_disks=[
                    {
                        "name": "anpvc",
                        "bus": "virtio",
                        "device": "disk",
                        "disk_type": "persistentVolumeClaim",
                        "volume_spec": {"claim_name": "mypvc2"},
                    },
                    {
                        "name": "cloudinit",
                        "bus": "virtio",
                        "device": "cdrom",
                        "disk_type": "cloudInitConfigDrive",
                        "volume_spec": {
                            "cloudInitNoCloud": {
                                "userData": "echo 'hello world'",
                            }
                        },
                    },
                ],
            )
        except ValueError as e:
            self.assertIn("auth and cloudInit at the same time", str(e))
        else:
            self.fail("Expected ValueError")

    def test_create_node_bad_pvc(self):
        try:
            self.driver.create_node(
                name="testcreatenode",
                size=KubeVirtNodeSize(cpu=1, ram=128),
                image=KubeVirtNodeImage("kubevirt/cirros-registry-disk-demo"),
                ex_disks=[
                    {
                        "name": "badpvc",
                        "bus": "virtio",
                        "device": "disk",
                        "disk_type": "persistentVolumeClaim",
                        "volume_spec": {
                            "claim_name": "notexistnewpvc",
                            "storage_class_name": "longhorn",
                            # missing size
                        },
                    },
                ],
            )
        except KeyError as e:
            self.assertIn("size", str(e))
            self.assertIn("storage_class_name", str(e))
            self.assertIn("both required", str(e))
        else:
            self.fail("Expected KeyError")

    def test_create_node_ex_template(self):
        # missing the optional metadata & required apiVersion
        # metadata will be added by the driver
        # and apiVersion missing will raise an error by _create_node_with_template
        template = {
            "kind": "VirtualMachine",
            "spec": {
                "running": False,
                "template": {
                    "spec": {
                        "domain": {
                            "devices": {
                                "disks": [],
                                "interfaces": [],
                                "networkInterfaceMultiqueue": False,
                            },
                            "machine": {"type": ""},
                            "resources": {"requests": {}, "limits": {}},
                        },
                        "networks": [],
                        "terminationGracePeriodSeconds": 0,
                        "volumes": [],
                    },
                },
            },
        }
        try:
            self.driver.create_node(
                name="testcreatenode",
                # size & image should be ignored when ex_template is provided
                size=KubeVirtNodeSize(cpu=1, ram=128),
                image=KubeVirtNodeImage("kubevirt/cirros-registry-disk-demo"),
                ex_template=template,
            )
        except ValueError as e:
            self.assertEqual(str(e), "The template must have an apiVersion: kubevirt.io/v1alpha3")
        else:
            self.fail("Expected ValueError")

    def test_create_node_req_lim(self):
        node = self.driver.create_node(
            name="vm-test-tumbleweed-07",
            size=KubeVirtNodeSize(
                cpu=2,
                ram=4096,
                cpu_request="1m",
                ram_request=1,
            ),
            image=KubeVirtNodeImage(
                name="registry.internal.com/kubevirt-vmidisks/tumbleweed:240531"
            ),
            location=NodeLocation(
                id="5341e71d-e8d8-4a1b-a97b-52864eb3dd7d",
                name="testreqlim",
                country="",
                driver=self.driver,
            ),
            auth=NodeAuthSSHKey("ssh-rsa FAKEKEY foo@bar.com"),
            ex_network={
                "network_type": "pod",
                "interface": "bridge",
                "name": "default",
            },
        )
        self.assertEqual(node.name, "vm-test-tumbleweed-07")
        self.assertEqual(node.size.extra["cpu"], 2)
        self.assertEqual(node.size.extra["cpu_request"], "1m")
        self.assertEqual(node.size.ram, 4096)
        self.assertEqual(node.size.extra["ram_request"], 1)

    def test_memory_in_MB(self):
        self.assertEqual(_memory_in_MB("128Mi"), 128)
        self.assertEqual(_memory_in_MB("128M"), 128)

        self.assertEqual(_memory_in_MB("134217728"), 128)
        self.assertEqual(_memory_in_MB(134217728), 128)

        self.assertEqual(_memory_in_MB("128Gi"), 128 * 1024)
        self.assertEqual(_memory_in_MB("128G"), 128 * 1000)

        self.assertEqual(_memory_in_MB("1920Ki"), 1920 // 1024)
        self.assertEqual(_memory_in_MB("1920K"), 1920 // 1000)

        self.assertEqual(_memory_in_MB("1Ti"), 1 * 1024 * 1024)
        self.assertEqual(_memory_in_MB("1T"), 1 * 1000 * 1000)

    def test_deep_merge_dict(self):
        a = {"domain": {"devices": 0}, "volumes": [1, 2, 3], "network": {}}
        b = {"domain": {"machine": "non-exist-in-a", "devices": 1024}, "volumes": [4, 5, 6]}
        expected_result = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024},
            "volumes": [1, 2, 3],
            "network": {},
        }
        self.assertEqual(_deep_merge_dict(a, b), expected_result)

        a = {"domain": {"devices": 1024}, "volumes": [1, 2, 3], "network": {}}
        b = {"domain": {"machine": "non-exist-in-a", "devices": 0}, "volumes": [4, 5, 6]}
        expected_result = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024},
            "volumes": [1, 2, 3],
            "network": {},
        }
        self.assertEqual(_deep_merge_dict(a, b), expected_result)

        a = {"domain": {"devices": 0}, "volumes": [1, 2, 3], "network": {}}
        b = {"domain": {"machine": "non-exist-in-a", "devices": 0}, "volumes": [4, 5, 6]}
        expected_result = {
            "domain": {"machine": "non-exist-in-a", "devices": 0},
            "volumes": [1, 2, 3],
            "network": {},
        }
        self.assertEqual(_deep_merge_dict(a, b), expected_result)

        a = {"domain": {"devices": 1024}, "volumes": [1, 2, 3], "network": {}}
        b = {"domain": {"machine": "non-exist-in-a", "devices": 1024}, "volumes": [4, 5, 6]}
        expected_result = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024},
            "volumes": [1, 2, 3],
            "network": {},
        }
        self.assertEqual(_deep_merge_dict(a, b), expected_result)

        a = {"domain": {"devices": 1024}, "volumes": [1, 2, 3], "network": {}}
        b = {"domain": {"machine": "non-exist-in-a", "devices": 2048}, "volumes": [4, 5, 6]}
        expected_result = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024},
            "volumes": [1, 2, 3],
            "network": {},
        }
        self.assertEqual(_deep_merge_dict(a, b), expected_result)

        a = {"domain": {"devices": 1024}, "volumes": [1, 2, 3], "network": {}}
        b = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024, "foo": "bar"},
            "volumes": [4, 5, 6],
        }
        expected_result = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024, "foo": "bar"},
            "volumes": [1, 2, 3],
            "network": {},
        }
        self.assertEqual(_deep_merge_dict(a, b), expected_result)

        a = {"domain": {"devices": 1024}, "volumes": [1, 2, 3], "network": {}}
        b = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024, "foo": ""},
            "volumes": [4, 5, 6],
        }
        expected_result = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024, "foo": ""},
            "volumes": [1, 2, 3],
            "network": {},
        }
        self.assertEqual(_deep_merge_dict(a, b), expected_result)

        a = {"domain": {"devices": 1024}, "volumes": [1, 2, 3], "network": {}}
        b = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024, "foo": None},
            "volumes": [4, 5, 6],
        }
        expected_result = {
            "domain": {"machine": "non-exist-in-a", "devices": 1024, "foo": None},
            "volumes": [1, 2, 3],
            "network": {},
        }
        self.assertEqual(_deep_merge_dict(a, b), expected_result)

    def test_create_node_auth(self):
        mock_vm = {
            "spec": {"template": {"spec": {"domain": {"devices": {"disks": []}}, "volumes": []}}}
        }
        cases = [
            NodeAuthPassword("password"),
            NodeAuthSSHKey("ssh-rsa FAKEKEY foo@bar.com"),
            NodeAuthPassword("bad\npassword\nwith\nnew\nline"),
            NodeAuthPassword("bad\npassword\n\fwith\tnot\b\b\b printable\a\n\rcharacters\b\n"),
            NodeAuthPassword("bad\npassword\nwith\n\"double\" and 'single' quotes"),
            NodeAuthSSHKey("ssh-rsa bad\nkey\nwith new line injected hacker@yaml.security"),
            NodeAuthSSHKey(
                "ssh-rsa bad\n\akey\b\b\b\nwith many\a \"injected' chars hacker@yaml.security"
            ),
        ]
        for a in cases:
            try:
                vm = copy.deepcopy(mock_vm)
                self.driver._create_node_auth(vm, a)
                user_data = vm["spec"]["template"]["spec"]["volumes"][0]["cloudInitNoCloud"][
                    "userData"
                ]
                self.assertTrue(isinstance(user_data, str))
                # 1. make sure there are no newlines escaped
                if isinstance(a, NodeAuthSSHKey):
                    # >>> public_key = "ssh-rsa FAKEKEY foo@bar.com"
                    # >>> a = (
                    # ...     """#cloud-config\n""" """ssh_authorized_keys:\n""" """  - {}\n"""
                    # ... ).format(public_key)
                    # >>> len(a.splitlines())
                    # 3
                    self.assertEqual(len(user_data.splitlines()), 3)
                elif isinstance(a, NodeAuthPassword):
                    # >>> password = "password"
                    # >>> a = (
                    # ...     """#cloud-config\n"""
                    # ...     """password: {}\n"""
                    # ...     """chpasswd: {{ expire: False }}\n"""
                    # ...     """ssh_pwauth: True\n"""
                    # ... ).format(password)
                    # >>> len(a.splitlines())
                    # 4
                    self.assertEqual(len(user_data.splitlines()), 4)
                # 2. check if the quotes are well-escaped
                for line in user_data.splitlines():
                    key = ""  # public key or password
                    if line.startswith("  - "):
                        key = line[4:]
                        self.assertEqual(key, json.dumps(a.pubkey.strip()))
                    elif line.startswith("password: "):
                        key = line[10:]
                        self.assertEqual(key, json.dumps(a.password.strip()))
                    else:
                        continue
                    self.assertTrue(key.startswith('"'))
                    self.assertTrue(key.endswith('"'))
                    # all double quotes inside must be escaped
                    for i, c in enumerate(key[1:-1]):
                        if c == '"':
                            # since enumerate starts from key[1:],
                            # so c is key[i+1],
                            # and key[i] is the previous char
                            self.assertEqual(key[i], "\\")

            except Exception as e:
                self.fail(f"Failed to create node auth for {a}: {e}")


class KubeVirtMockHttp(MockHttp):
    fixtures = ComputeFileFixtures("kubevirt")

    did_create_vm = False
    did_create_vm_test_size_req_lim = False

    def _api_v1_namespaces(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("_api_v1_namespaces.json")
        else:
            raise AssertionError("Unsupported method")

        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_default_virtualmachines(
        self, method, url, body, headers
    ):
        if method == "GET":
            if self.did_create_vm:
                body = self.fixtures.load("get_default_vms_after_create_vm.json")
            else:
                body = self.fixtures.load("get_default_vms.json")
            resp = httplib.OK
        elif method == "POST":
            body = self.fixtures.load("create_vm.json")
            resp = httplib.CREATED
            self.did_create_vm = True
        else:
            AssertionError("Unsupported method")
        return (resp, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_kube_node_lease_virtualmachines(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("get_kube_node_lease_vms.json")
        elif method == "POST":
            pass
        else:
            AssertionError("Unsupported method")
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_kube_public_virtualmachines(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("get_kube_public_vms.json")
        elif method == "POST":
            pass
        else:
            AssertionError("Unsupported method")
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_kube_system_virtualmachines(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("get_kube_system_vms.json")
        elif method == "POST":
            pass
        else:
            AssertionError("Unsupported method")
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_kubevirt_virtualmachines(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("get_kube_public_vms.json")
        else:
            AssertionError("Unsupported method")
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_default_virtualmachines_testcreatenode(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("create_vm.json")
        else:
            AssertionError("Unsupported method")

        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_default_virtualmachines_testvm(
        self, method, url, body, headers
    ):
        header = "application/merge-patch+json"
        data_stop = {"spec": {"running": False}}
        data_start = {"spec": {"running": True}}

        if method == "PATCH" and headers["Content-Type"] == header and body == data_start:
            body = self.fixtures.load("start_testvm.json")

        elif method == "PATCH" and headers["Content-Type"] == header and body == data_stop:
            body = self.fixtures.load("stop_testvm.json")

        else:
            AssertionError("Unsupported method")

        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_default_virtualmachines_vm_cirros(
        self, method, url, body, headers
    ):
        header = "application/merge-patch+json"
        data_stop = {"spec": {"running": False}}
        data_start = {"spec": {"running": True}}

        if method == "PATCH" and headers["Content-Type"] == header and body == data_start:
            body = self.fixtures.load("start_vm_cirros.json")

        elif method == "PATCH" and headers["Content-Type"] == header and body == data_stop:
            body = self.fixtures.load("stop_vm_cirros.json")

        else:
            AssertionError("Unsupported method")

        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_default_virtualmachineinstances_testvm(
        self, method, url, body, headers
    ):
        if method == "DELETE":
            body = self.fixtures.load("delete_vmi_testvm.json")
        else:
            AssertionError("Unsupported method")

        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _api_v1_namespaces_default_pods(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("get_pods.json")
        else:
            AssertionError("Unsupported method")

        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _api_v1_namespaces_default_services(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("get_services.json")
        else:
            AssertionError("Unsupported method")

        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _api_v1_namespaces_default_persistentvolumeclaims(self, method, url, body, headers):
        if method == "GET":
            body = self.fixtures.load("get_pvcs.json")
        else:
            AssertionError("Unsupported method")

        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _apis_kubevirt_io_v1alpha3_namespaces_testreqlim_virtualmachines(
        self, method, url, body, headers
    ):
        if method == "GET":
            if self.did_create_vm_test_size_req_lim:
                body = self.fixtures.load("get_testreqlim_vms_after_create_vm.json")
            else:
                body = self.fixtures.load("get_default_vms.json")
            resp = httplib.OK
        elif method == "POST":
            body = self.fixtures.load("create_vm_reqlim.json")
            resp = httplib.CREATED
            self.did_create_vm_test_size_req_lim = True
        else:
            AssertionError("Unsupported method")
        return (resp, body, {}, httplib.responses[httplib.OK])

    def _api_v1_namespaces_testreqlim_services(self, method, url, body, headers):
        return self._api_v1_namespaces_default_services(method, url, body, headers)

    def _api_v1_namespaces_testreqlim_pods(self, method, url, body, headers):
        return self._api_v1_namespaces_default_pods(method, url, body, headers)

    def _apis_kubevirt_io_v1alpha3_namespaces_testreqlim_virtualmachines_vm_test_tumbleweed_07(
        self, method, url, body, headers
    ):
        if method == "GET":
            body = self.fixtures.load("get_vm_test_tumbleweed_07.json")
        else:
            AssertionError("Unsupported method")

        return (httplib.OK, body, {}, httplib.responses[httplib.OK])


if __name__ == "__main__":
    sys.exit(unittest.main())
