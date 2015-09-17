# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import mock
from oslo_context import context as oslo_context

from senlin.common import consts
from senlin.db import api as db_api
from senlin.drivers import base as driver_base
from senlin.engine import cluster_policy
from senlin.engine import node as node_mod
from senlin.policies import base as policy_base
from senlin.policies import lb_policy
from senlin.tests.unit.common import base
from senlin.tests.unit.common import utils


class TestLoadBalancingPolicy(base.SenlinTestCase):

    def setUp(self):
        super(TestLoadBalancingPolicy, self).setUp()
        self.context = utils.dummy_context()
        self.spec = {
            'type': 'senlin.policy.loadbalance',
            'version': '1.0',
            'properties': {
                'pool': {
                    'protocol': 'HTTP',
                    'protocol_port': 80,
                    'subnet': 'test-subnet',
                    'lb_method': 'ROUND_ROBIN',
                    'admin_state_up': True,
                    'session_persistence': {
                        'type': 'SOURCE_IP',
                        'cookie_name': 'whatever'
                    }
                },
                'vip': {
                    'address': '192.168.1.100',
                    'subnet': 'test-subnet',
                    'connection_limit': 500,
                    'protocol': 'HTTP',
                    'protocol_port': 80,
                    'admin_state_up': True,
                }
            }
        }
        sd = mock.Mock()
        self.patchobject(driver_base, 'SenlinDriver', return_value=sd)
        self.lb_driver = mock.Mock()
        sd.loadbalancing.return_value = self.lb_driver

    @mock.patch.object(lb_policy.LoadBalancingPolicy, 'validate')
    def test_init(self, mock_validate):
        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        self.assertIsNone(policy.id)
        self.assertEqual('test-policy', policy.name)
        self.assertEqual('senlin.policy.loadbalance-1.0', policy.type)
        self.assertEqual(self.spec['properties']['pool'], policy.pool_spec)
        self.assertEqual(self.spec['properties']['vip'], policy.vip_spec)
        self.assertIsNone(policy.lb)
        mock_validate.assert_called_once_with()

    def test_init_with_default_value(self):
        spec = {
            'type': 'senlin.policy.loadbalance',
            'version': '1.0',
            'properties': {
                'pool': {'subnet': 'test-subnet'},
                'vip': {'subnet': 'test-subnet'}
            }
        }
        default_spec = {
            'type': 'senlin.policy.loadbalance',
            'version': '1.0',
            'properties': {
                'pool': {
                    'protocol': 'HTTP',
                    'protocol_port': 80,
                    'subnet': 'test-subnet',
                    'lb_method': 'ROUND_ROBIN',
                    'admin_state_up': True,
                    'session_persistence': {},
                },
                'vip': {
                    'address': None,
                    'subnet': 'test-subnet',
                    'connection_limit': -1,
                    'protocol': 'HTTP',
                    'protocol_port': 80,
                    'admin_state_up': True,
                }
            }
        }

        policy = lb_policy.LoadBalancingPolicy('test-policy', spec)

        self.assertIsNone(policy.id)
        self.assertEqual('test-policy', policy.name)
        self.assertEqual('senlin.policy.loadbalance-1.0', policy.type)
        self.assertEqual(default_spec['properties']['pool'], policy.pool_spec)
        self.assertEqual(default_spec['properties']['vip'], policy.vip_spec)
        self.assertIsNone(policy.lb)

    @mock.patch.object(policy_base.Policy, 'validate')
    def test_validate(self, mock_validate):
        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        policy.validate()
        mock_validate.assert_called_with()

    @mock.patch.object(lb_policy.LoadBalancingPolicy, '_build_policy_data')
    @mock.patch.object(node_mod.Node, 'load_all')
    @mock.patch.object(policy_base.Policy, 'attach')
    @mock.patch.object(lb_policy.LoadBalancingPolicy, '_build_conn_params')
    def test_attach_succeeded(self, m_conn, m_attach, m_load, m_build):
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        node1 = mock.Mock()
        node2 = mock.Mock()
        m_attach.return_value = (True, None)
        m_load.return_value = [node1, node2]
        m_build.return_value = 'policy_data'
        data = {
            'loadbalancer': 'LB_ID',
            'pool': 'POOL_ID'
        }

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        self.lb_driver.lb_create.return_value = (True, data)
        self.lb_driver.member_add.side_effect = ['MEMBER1_ID', 'MEMBER2_ID']
        res, data = policy.attach(cluster)
        self.assertTrue(res)
        self.assertEqual('policy_data', data)
        self.lb_driver.lb_create.assert_called_once_with(policy.vip_spec,
                                                         policy.pool_spec)
        m_load.assert_called_once_with(mock.ANY, cluster_id=cluster.id)
        member_add_calls = [
            mock.call(node1, 'LB_ID', 'POOL_ID', 80, 'test-subnet'),
            mock.call(node2, 'LB_ID', 'POOL_ID', 80, 'test-subnet')
        ]
        self.lb_driver.member_add.assert_has_calls(member_add_calls)
        node1.data.update.assert_called_once_with({'lb_member': 'MEMBER1_ID'})
        node2.data.update.assert_called_once_with({'lb_member': 'MEMBER2_ID'})
        node1.store.assert_called_once_with(mock.ANY)
        node2.store.assert_called_once_with(mock.ANY)

    @mock.patch.object(policy_base.Policy, 'attach')
    def test_attach_failed_base_return_false(self, mock_attach):

        cluster = mock.Mock()
        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        # Base policy attaching return False
        mock_attach.return_value = (False, 'data')
        res, data = policy.attach(cluster)
        self.assertFalse(res)
        self.assertEqual('data', data)

    @mock.patch.object(lb_policy.LoadBalancingPolicy, '_build_conn_params')
    @mock.patch.object(node_mod.Node, 'load_all')
    @mock.patch.object(policy_base.Policy, 'attach')
    def test_attach_failed_lb_creation_error(self, m_attach, m_load, m_conn):
        cluster = mock.Mock()
        m_attach.return_value = (True, None)

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        # lb_driver.lb_create return False
        self.lb_driver.lb_create.return_value = (False, 'error')
        res = policy.attach(cluster)
        self.assertEqual((False, 'error'), res)

    @mock.patch.object(lb_policy.LoadBalancingPolicy, '_build_conn_params')
    @mock.patch.object(node_mod.Node, 'load_all')
    @mock.patch.object(policy_base.Policy, 'attach')
    def test_attach_failed_member_add(self, mock_attach, mock_load, m_conn):
        cluster = mock.Mock()
        mock_attach.return_value = (True, None)
        mock_load.return_value = ['node1', 'node2']
        lb_data = {
            'loadbalancer': 'LB_ID',
            'pool': 'POOL_ID'
        }

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        # lb_driver.member_add return None
        self.lb_driver.lb_create.return_value = (True, lb_data)
        self.lb_driver.member_add.return_value = None
        res = policy.attach(cluster)
        self.assertEqual((False, 'Failed in adding node into lb pool'), res)
        self.lb_driver.lb_delete.assert_called_once_with(**lb_data)


@mock.patch.object(lb_policy.LoadBalancingPolicy, '_build_conn_params')
@mock.patch.object(cluster_policy.ClusterPolicy, 'load')
@mock.patch.object(lb_policy.LoadBalancingPolicy, '_extract_policy_data')
class TestLoadBalancingPolicyOperations(base.SenlinTestCase):

    def setUp(self):
        super(TestLoadBalancingPolicyOperations, self).setUp()

        self.context = utils.dummy_context()
        self.spec = {
            'type': 'senlin.policy.loadbalance',
            'version': '1.0',
            'properties': {
                'pool': {
                    'protocol': 'HTTP',
                    'protocol_port': 80,
                    'subnet': 'test-subnet',
                    'lb_method': 'ROUND_ROBIN',
                    'admin_state_up': True,
                    'session_persistence': {
                        'type': 'SOURCE_IP',
                        'cookie_name': 'whatever'
                    }
                },
                'vip': {
                    'address': '192.168.1.100',
                    'subnet': 'test-subnet',
                    'connection_limit': 500,
                    'protocol': 'HTTP',
                    'protocol_port': 80,
                    'admin_state_up': True,
                }
            }
        }
        sd = mock.Mock()
        self.patchobject(driver_base, 'SenlinDriver', return_value=sd)
        self.lb_driver = mock.Mock()
        sd.loadbalancing.return_value = self.lb_driver
        self.patchobject(oslo_context, 'get_current')

    def test_detach_no_policy_data(self, m_extract, m_load, m_conn):
        cluster = mock.Mock()
        m_extract.return_value = None
        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        res, data = policy.detach(cluster)
        self.assertTrue(res)
        self.assertEqual('LB resources deletion succeeded.', data)

    def test_detach_succeeded(self, m_extract, m_load, m_conn):
        cluster = mock.Mock()
        cluster.id = 'CLUSTER_ID'
        cp = mock.Mock()
        policy_data = {
            'loadbalancer': 'LB_ID',
            'listener': 'LISTENER_ID',
            'pool': 'POOL_ID',
            'healthmonitor': 'HM_ID'
        }
        cp_data = {
            'LoadBalancingPolicy': {
                'version': '1.0',
                'data': policy_data
            }
        }
        cp.data = cp_data
        m_load.return_value = cp
        m_extract.return_value = policy_data
        self.lb_driver.lb_delete.return_value = (True, 'lb_delete succeeded.')
        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        res, data = policy.detach(cluster)
        self.assertTrue(res)
        self.assertEqual('lb_delete succeeded.', data)
        m_load.assert_called_once_with(mock.ANY, cluster.id, policy.id)
        m_extract.assert_called_once_with(cp_data)
        self.lb_driver.lb_delete.assert_called_once_with(**policy_data)

    def test_detach_failed_lb_delete(self, m_extract, m_load, m_conn):
        cluster = mock.Mock()
        self.lb_driver.lb_delete.return_value = (False, 'lb_delete failed.')

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        res, data = policy.detach(cluster)
        self.assertFalse(res)
        self.assertEqual('lb_delete failed.', data)

    def test_post_op_no_nodes(self, m_extract, m_load, m_conn):
        action = mock.Mock()
        action.data = {'nodes': []}

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        res = policy.post_op('FAKE_ID', action)
        self.assertIsNone(res)

    @mock.patch.object(node_mod.Node, 'load')
    @mock.patch.object(db_api, 'cluster_get')
    def test_post_op_add_nodes(self, m_cluster_get, m_node_load, m_extract,
                               m_load, m_conn):
        cid = 'CLUSTER_ID'
        cluster = mock.Mock()
        m_cluster_get.return_value = cluster
        node1 = mock.Mock()
        node2 = mock.Mock()
        node1.data = {}
        node2.data = {}
        action = mock.Mock()
        action.data = {
            'nodes': ['NODE1_ID', 'NODE2_ID'],
            'creation': {'count': 2}
        }
        action.context = 'action_context'
        action.action = consts.CLUSTER_RESIZE
        cp = mock.Mock()
        policy_data = {
            'loadbalancer': 'LB_ID',
            'listener': 'LISTENER_ID',
            'pool': 'POOL_ID',
            'healthmonitor': 'HM_ID'
        }
        cp_data = {
            'LoadBalancingPolicy': {
                'version': '1.0',
                'data': policy_data
            }
        }
        cp.data = cp_data
        self.lb_driver.member_add.side_effect = ['MEMBER1_ID', 'MEMBER2_ID']
        m_node_load.side_effect = [node1, node2]
        m_load.return_value = cp
        m_extract.return_value = policy_data

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)
        res = policy.post_op(cid, action)
        self.assertIsNone(res)
        m_cluster_get.assert_called_once_with('action_context', 'CLUSTER_ID')
        m_conn.assert_called_once_with(cluster)
        m_load.assert_called_once_with('action_context', cid, policy.id)
        m_extract.assert_called_once_with(cp_data)
        calls_node_load = [mock.call('action_context', node_id=n,
                                     show_deleted=True
                                     ) for n in ['NODE1_ID', 'NODE2_ID']]
        m_node_load.assert_has_calls(calls_node_load)
        calls_member_add = [mock.call(n, 'LB_ID', 'POOL_ID', 80, 'test-subnet'
                                      ) for n in [node1, node2]]
        self.lb_driver.member_add.assert_has_calls(calls_member_add)
        node1.store.assert_called_once_with('action_context')
        node2.store.assert_called_once_with('action_context')
        self.assertEqual({'lb_member': 'MEMBER1_ID'}, node1.data)
        self.assertEqual({'lb_member': 'MEMBER2_ID'}, node2.data)

    @mock.patch.object(node_mod.Node, 'load')
    @mock.patch.object(db_api, 'cluster_get')
    def test_post_op_add_nodes_in_pool(self, m_cluster_get, m_node_load,
                                       m_extract, m_load, m_conn):
        cluster_id = 'CLUSTER_ID'
        node1 = mock.Mock()
        node2 = mock.Mock()
        node1.data = {'lb_member': 'MEMBER1_ID'}
        node2.data = {}
        action = mock.Mock()
        action.data = {
            'nodes': ['NODE1_ID', 'NODE2_ID'],
            'creation': {'count': 2}
        }
        action.context = 'action_context'
        action.action = consts.CLUSTER_RESIZE
        policy_data = {
            'loadbalancer': 'LB_ID',
            'listener': 'LISTENER_ID',
            'pool': 'POOL_ID',
            'healthmonitor': 'HM_ID'
        }
        self.lb_driver.member_add.side_effect = ['MEMBER2_ID']
        m_node_load.side_effect = [node1, node2]
        m_extract.return_value = policy_data

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)
        res = policy.post_op(cluster_id, action)
        self.assertIsNone(res)
        self.lb_driver.member_add.assert_called_once_with(
            node2, 'LB_ID', 'POOL_ID', 80, 'test-subnet')

    @mock.patch.object(node_mod.Node, 'load')
    @mock.patch.object(db_api, 'cluster_get')
    def test_post_op_add_nodes_failed(self, m_cluster_get, m_node_load,
                                      m_extract, m_load, m_conn):
        cluster_id = 'CLUSTER_ID'
        node1 = mock.Mock()
        node1.data = {}
        action = mock.Mock()
        action.data = {
            'nodes': ['NODE1_ID'],
            'creation': {'count': 1}
        }
        action.context = 'action_context'
        action.action = consts.CLUSTER_RESIZE
        self.lb_driver.member_add.return_value = None
        m_node_load.side_effect = [node1]
        m_extract.return_value = {
            'loadbalancer': 'LB_ID',
            'listener': 'LISTENER_ID',
            'pool': 'POOL_ID',
            'healthmonitor': 'HM_ID'
        }
        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        res = policy.post_op(cluster_id, action)

        self.assertIsNone(res)
        self.assertEqual(policy_base.CHECK_ERROR, action.data['status'])
        self.assertEqual('Failed in adding new node into lb pool',
                         action.data['reason'])
        self.lb_driver.member_add.assert_called_once_with(
            node1, 'LB_ID', 'POOL_ID', 80, 'test-subnet')

    @mock.patch.object(node_mod.Node, 'load')
    @mock.patch.object(db_api, 'cluster_get')
    def test_post_op_del_nodes_ok(self, m_cluster_get, m_node_load, m_extract,
                                  m_load, m_conn):
        cluster_id = 'CLUSTER_ID'
        cluster = mock.Mock()
        m_cluster_get.return_value = cluster
        node1 = mock.Mock()
        node2 = mock.Mock()
        node1.data = {'lb_member': 'MEMBER1_ID'}
        node2.data = {'lb_member': 'MEMBER2_ID'}
        action = mock.Mock()
        action.data = {
            'nodes': ['NODE1_ID', 'NODE2_ID'],
            'deletion': {'count': 2}
        }
        action.context = 'action_context'
        action.action = consts.CLUSTER_RESIZE
        cp = mock.Mock()
        policy_data = {
            'loadbalancer': 'LB_ID',
            'listener': 'LISTENER_ID',
            'pool': 'POOL_ID',
            'healthmonitor': 'HM_ID'
        }
        cp_data = {
            'LoadBalancingPolicy': {
                'version': '1.0',
                'data': policy_data
            }
        }
        cp.data = cp_data
        self.lb_driver.member_remove.return_value = True
        m_node_load.side_effect = [node1, node2]
        m_load.return_value = cp
        m_extract.return_value = policy_data

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)
        res = policy.post_op(cluster_id, action)
        self.assertIsNone(res)
        m_cluster_get.assert_called_once_with('action_context', 'CLUSTER_ID')
        m_conn.assert_called_once_with(cluster)
        m_load.assert_called_once_with('action_context', cluster_id, policy.id)
        m_extract.assert_called_once_with(cp_data)
        calls_node_load = [mock.call('action_context', node_id=n,
                                     show_deleted=True
                                     ) for n in ['NODE1_ID', 'NODE2_ID']]
        m_node_load.assert_has_calls(calls_node_load)
        calls_member_del = [mock.call('LB_ID', 'POOL_ID', m
                                      ) for m in ['MEMBER1_ID', 'MEMBER2_ID']]
        self.lb_driver.member_remove.assert_has_calls(calls_member_del)

    @mock.patch.object(node_mod.Node, 'load')
    @mock.patch.object(db_api, 'cluster_get')
    def test_post_op_del_nodes_not_in_pool(self, m_cluster_get, m_node_load,
                                           m_extract, m_load, m_conn):
        cluster_id = 'CLUSTER_ID'
        node1 = mock.Mock()
        node2 = mock.Mock()
        node1.data = {}
        node2.data = {'lb_member': 'MEMBER2_ID'}
        action = mock.Mock()
        action.data = {
            'nodes': ['NODE1_ID', 'NODE2_ID'],
            'deletion': {'count': 2}
        }
        action.context = 'action_context'
        action.action = consts.CLUSTER_RESIZE
        self.lb_driver.member_remove.return_value = True
        m_node_load.side_effect = [node1, node2]
        m_extract.return_value = {
            'loadbalancer': 'LB_ID',
            'listener': 'LISTENER_ID',
            'pool': 'POOL_ID',
            'healthmonitor': 'HM_ID'
        }

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)
        res = policy.post_op(cluster_id, action)
        self.assertIsNone(res)
        self.lb_driver.member_remove.assert_called_once_with(
            'LB_ID', 'POOL_ID', 'MEMBER2_ID')

    @mock.patch.object(node_mod.Node, 'load')
    @mock.patch.object(db_api, 'cluster_get')
    def test_post_op_del_nodes_failed(self, m_cluster_get, m_node_load,
                                      m_extract, m_load, m_conn):
        cluster_id = 'CLUSTER_ID'
        node1 = mock.Mock()
        node1.data = {'lb_member': 'MEMBER1_ID'}
        action = mock.Mock()
        action.data = {
            'nodes': ['NODE1_ID'],
            'deletion': {'count': 1}
        }
        action.context = 'action_context'
        action.action = consts.CLUSTER_RESIZE
        self.lb_driver.member_remove.return_value = False
        m_node_load.side_effect = [node1]
        m_extract.return_value = {
            'loadbalancer': 'LB_ID',
            'listener': 'LISTENER_ID',
            'pool': 'POOL_ID',
            'healthmonitor': 'HM_ID'
        }

        policy = lb_policy.LoadBalancingPolicy('test-policy', self.spec)

        res = policy.post_op(cluster_id, action)

        self.assertIsNone(res)
        self.assertEqual(policy_base.CHECK_ERROR, action.data['status'])
        self.assertEqual('Failed in removing deleted node from lb pool',
                         action.data['reason'])
        self.lb_driver.member_remove.assert_called_once_with(
            'LB_ID', 'POOL_ID', 'MEMBER1_ID')
