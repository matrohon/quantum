# Copyright (c) 2013 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Sylvain Afchain, eNovance SAS
# @author: Francois Eleouet, Orange
# @author: Mathieu Rohon, Orange

import mock

from neutron.common import constants
from neutron.common import topics
from neutron import context
from neutron.db import agents_db
from neutron.db import api as db_api
from neutron.extensions import portbindings
from neutron.extensions import providernet as pnet
from neutron import manager
from neutron.openstack.common import timeutils
from neutron.plugins.ml2 import config as config
from neutron.plugins.ml2.drivers.l2pop import constants as l2_consts
from neutron.plugins.ml2 import managers
from neutron.plugins.ml2 import rpc
from neutron.tests.unit import test_db_plugin as test_plugin

HOST = 'my_l2_host'
L2_AGENT = {
    'binary': 'neutron-openvswitch-agent',
    'host': HOST,
    'topic': constants.L2_AGENT_TOPIC,
    'configurations': {'tunneling_ip': '20.0.0.1',
                       'tunnel_types': ['vxlan']},
    'agent_type': constants.AGENT_TYPE_OVS,
    'tunnel_type': [],
    'start_flag': True
}

L2_AGENT_2 = {
    'binary': 'neutron-openvswitch-agent',
    'host': HOST + '_2',
    'topic': constants.L2_AGENT_TOPIC,
    'configurations': {'tunneling_ip': '20.0.0.2',
                       'tunnel_types': ['vxlan']},
    'agent_type': constants.AGENT_TYPE_OVS,
    'tunnel_type': [],
    'start_flag': True
}

L2_AGENT_3 = {
    'binary': 'neutron-openvswitch-agent',
    'host': HOST + '_3',
    'topic': constants.L2_AGENT_TOPIC,
    'configurations': {'tunneling_ip': '20.0.0.3',
                       'tunnel_types': ['vxlan']},
    'agent_type': constants.AGENT_TYPE_OVS,
    'tunnel_type': [],
    'start_flag': True
}

PLUGIN_NAME = 'neutron.plugins.ml2.plugin.Ml2Plugin'
NOTIFIER = 'neutron.plugins.ml2.rpc.AgentNotifierApi'


class TestL2PopulationRpcTestCase(test_plugin.NeutronDbPluginV2TestCase):

    def setUp(self):
        # Enable the test mechanism driver to ensure that
        # we can successfully call through to all mechanism
        # driver apis.
        config.cfg.CONF.set_override('mechanism_drivers',
                                     ['openvswitch', 'linuxbridge',
                                      'l2population'],
                                     'ml2')
        config.cfg.CONF.set_override('debug',
                                     'True')
        super(TestL2PopulationRpcTestCase, self).setUp(PLUGIN_NAME)
        self.addCleanup(config.cfg.CONF.reset)

        self.adminContext = context.get_admin_context()

        self.type_manager = managers.TypeManager()
        self.notifier = rpc.AgentNotifierApi(topics.AGENT)
        self.callbacks = rpc.RpcCallbacks(self.notifier, self.type_manager)

        self.orig_supported_agents = l2_consts.SUPPORTED_AGENT_TYPES
        l2_consts.SUPPORTED_AGENT_TYPES = [constants.AGENT_TYPE_OVS]

        net_arg = {pnet.NETWORK_TYPE: 'vxlan',
                   pnet.SEGMENTATION_ID: '1'}
        self._network = self._make_network(self.fmt, 'net1', True,
                                           arg_list=(pnet.NETWORK_TYPE,
                                                     pnet.SEGMENTATION_ID,),
                                           **net_arg)

        notifier_patch = mock.patch(NOTIFIER)
        notifier_patch.start()

        self.fanout_topic = topics.get_topic_name(topics.AGENT,
                                                  topics.L2POPULATION,
                                                  topics.UPDATE)
        fanout = ('neutron.openstack.common.rpc.proxy.RpcProxy.fanout_cast')
        fanout_patch = mock.patch(fanout)
        self.mock_fanout = fanout_patch.start()

        cast = ('neutron.openstack.common.rpc.proxy.RpcProxy.cast')
        cast_patch = mock.patch(cast)
        self.mock_cast = cast_patch.start()

        uptime = ('neutron.plugins.ml2.drivers.l2pop.db.L2populationDbMixin.'
                  'get_agent_uptime')
        uptime_patch = mock.patch(uptime, return_value=190)
        uptime_patch.start()

        self.addCleanup(mock.patch.stopall)
        self.addCleanup(db_api.clear_db)

    def tearDown(self):
        l2_consts.SUPPORTED_AGENT_TYPES = self.orig_supported_agents
        super(TestL2PopulationRpcTestCase, self).tearDown()

    def _register_ml2_agents(self):
        callback = agents_db.AgentExtRpcCallback()
        callback.report_state(self.adminContext,
                              agent_state={'agent_state': L2_AGENT},
                              time=timeutils.strtime())
        callback.report_state(self.adminContext,
                              agent_state={'agent_state': L2_AGENT_2},
                              time=timeutils.strtime())
        callback.report_state(self.adminContext,
                              agent_state={'agent_state': L2_AGENT_3},
                              time=timeutils.strtime())

    def _fdb_entries_exists(self, call_args_list):
        found = False
        for call, topic in call_args_list:
            args = call[1]['args']
            if args.has_key('fdb_entries'):
                found = True
        return found

    def test_port_up_down_delete(self):
        self._register_ml2_agents()
        with self.subnet(network=self._network) as subnet:
            host_arg = {portbindings.HOST_ID: HOST,
                        'admin_state_up': True}
            with self.port(subnet=subnet,
                           arg_list=(portbindings.HOST_ID, 'admin_state_up',),
                           **host_arg) as port1:
                host2_arg = {portbindings.HOST_ID: L2_AGENT_2["host"],
                            'admin_state_up': True}
                with self.port(subnet=subnet,
                               arg_list=(portbindings.HOST_ID,
                                         'admin_state_up',),
                               **host2_arg) as port2:
                    #Adding port that are down on the net :
                    #assert that no fanout or cast has been sent
                    fdb_exists = self._fdb_entries_exists(
                        self.mock_fanout.call_args_list)
                    self.assertFalse(fdb_exists)
                    self.assertFalse(self.mock_cast.called)


                    #Bringing a first port up on agent
                    #no fanout and no cast should be sent
                    p1 = port1['port']
                    device1 = 'tap' + p1['id']

                    self.mock_cast.reset_mock()
                    self.mock_fanout.reset_mock()
                    self.callbacks.update_device_up(self.adminContext,
                                                    agent_id=HOST,
                                                    device=device1)


                    self.assertFalse(self.mock_fanout.called)
                    self.assertFalse(self.mock_cast.called)


                    #Bringing a second port up on another agent
                    #assert that fanout and cast has been sent with Flooding 
                    #entries
                    p2 = port2['port']
                    device2 = 'tap' + p2['id']

                    self.mock_cast.reset_mock()
                    self.mock_fanout.reset_mock()
                    self.callbacks.update_device_up(self.adminContext,
                                                    agent_id=L2_AGENT_2["host"],
                                                    device=device2)
                    p1_ips = [p['ip_address'] for p in p1['fixed_ips']]
                    p2_ips = [p['ip_address'] for p in p2['fixed_ips']]

                    expected1 = {'args':
                                 {'fdb_entries':
                                  {p1['network_id']:
                                   {'ports':
                                    {'20.0.0.1': [constants.FLOODING_ENTRY,
                                                  [p1['mac_address'],
                                                   p1_ips[0]]]},
                                    'network_type': 'vxlan',
                                    'segment_id': 1}}},
                                 'namespace': None,
                                 'method': 'add_fdb_entries'}

                    topic = topics.get_topic_name(topics.AGENT,
                              topics.L2POPULATION,
                              topics.UPDATE,
                              L2_AGENT_2["host"])
                    self.mock_cast.assert_called_with(mock.ANY,
                                                      expected1,
                                                      topic=topic)

                    expected2 = {'args':
                                 {'fdb_entries':
                                  {p1['network_id']:
                                   {'ports':
                                    {'20.0.0.2': [constants.FLOODING_ENTRY,
                                                  [p2['mac_address'],
                                                   p2_ips[0]]]},
                                    'network_type': 'vxlan',
                                    'segment_id': 1}}},
                                 'namespace': None,
                                 'method': 'add_fdb_entries'}

 

                    self.mock_fanout.assert_called_with(
                        mock.ANY, expected2, topic=self.fanout_topic)

                    #Bringing a third port up on an agent that already hosts 
                    #a port of this net, and another port up of this net is 
                    #on onother agent
                    #assert that fanouty withoiut flooding entries is sent
                    #assert that no cast is sent
                    with self.port(subnet=subnet,
                                   arg_list=(portbindings.HOST_ID,
                                             'admin_state_up',),
                                   **host_arg) as port3:
                        p3 = port3['port']
                        device3 = 'tap' + p3['id']

                        p3_ips = [p['ip_address'] for p in p3['fixed_ips']]

                        self.mock_cast.reset_mock()
                        self.mock_fanout.reset_mock()

                        self.callbacks.update_device_up(self.adminContext,
                                                        agent_id=HOST,
                                                        device=device3)
                        expected3 = {'args':
                                 {'fdb_entries':
                                  {p1['network_id']:
                                   {'ports':
                                    {'20.0.0.1': [[p3['mac_address'],
                                                   p3_ips[0]]]},
                                    'network_type': 'vxlan',
                                    'segment_id': 1}}},
                                 'namespace': None,
                                 'method': 'add_fdb_entries'}
                        self.mock_fanout.assert_called_with(
                            mock.ANY, expected3, topic=self.fanout_topic)
                        self.assertFalse(self.mock_cast.called)

                        #Bringing the third port down
                        #assert that a fanout cast is sent without flooding 
                        #entries
                        expected3_del = {'args':
                                 {'fdb_entries':
                                  {p1['network_id']:
                                   {'ports':
                                    {'20.0.0.1': [[p3['mac_address'],
                                                   p3_ips[0]]]},
                                    'network_type': 'vxlan',
                                    'segment_id': 1}}},
                                 'namespace': None,
                                 'method': 'remove_fdb_entries'}

                        self.mock_fanout.reset_mock()
                        self.callbacks.update_device_down(self.adminContext,
                                                          agent_id=HOST,
                                                          device=device3)
                        self.mock_fanout.assert_called_with(
                            mock.ANY, expected3_del, topic=self.fanout_topic)

                        #Bringing the third port up and delete it
                        #assert that a fanout cast is sent without flooding 
                        #entries
                        self.callbacks.update_device_up(self.adminContext,
                                                        agent_id=HOST,
                                                        device=device3)
                        self.mock_fanout.reset_mock()

                    self.mock_fanout.assert_any_call(
                        mock.ANY, expected3_del, topic=self.fanout_topic)

                    #Bringing the second port down, which is the last port
                    #on the agent
                    #assert that fanout has been sent with flooding entries
                    self.mock_fanout.reset_mock()
                    self.callbacks.update_device_down(self.adminContext,
                                                      agent_id=L2_AGENT_2["host"],
                                                      device=device2)

                    expected2_del = {'args':
                                     {'fdb_entries':
                                      {p1['network_id']:
                                       {'ports':
                                        {'20.0.0.2': [constants.FLOODING_ENTRY,
                                                      [p2['mac_address'],
                                                       p2_ips[0]]]},
                                        'network_type': 'vxlan',
                                        'segment_id': 1}}},
                                     'namespace': None,
                                     'method': 'remove_fdb_entries'}


                    self.mock_fanout.assert_any_call(
                        mock.ANY, expected2_del, topic=self.fanout_topic)

                    #Bringing the second port up and delete it
                    #assert that fanout has been sent with flooding entries
                    self.callbacks.update_device_up(self.adminContext,
                                                    agent_id=L2_AGENT_2["host"],
                                                    device=device2)
                    self.mock_fanout.reset_mock()

                self.mock_fanout.assert_any_call(
                    mock.ANY, expected2_del, topic=self.fanout_topic)

                #Bringing the first port down, which is the last port
                #on the net
                #assert that no fanout is sent
                self.mock_fanout.reset_mock()
                self.callbacks.update_device_down(self.adminContext,
                                                  agent_id=HOST,
                                                  device=device1)
                self.assertFalse(self.mock_fanout.called)

                #Bringing the first port up and delete it
                #assert that no fanout is sent
                self.callbacks.update_device_up(self.adminContext,
                                                agent_id=HOST,
                                                device=device1)
                self.mock_fanout.reset_mock()

            fdb_exists = self._fdb_entries_exists(
                self.mock_fanout.call_args_list)
            self.assertFalse(fdb_exists)

    def test_port_up_down_delete_called_every_ports_on_the_same_agent(self):
        self._register_ml2_agents()

        with self.subnet(network=self._network) as subnet:
            host_arg = {portbindings.HOST_ID: HOST}
            with self.port(subnet=subnet,
                           arg_list=(portbindings.HOST_ID,),
                           **host_arg) as port1:
                with self.port(subnet=subnet,
                               arg_list=(portbindings.HOST_ID,),
                               **host_arg) as port2:
                    p1 = port1['port']
                    device1 = 'tap' + p1['id']

                    self.callbacks.update_device_up(self.adminContext,
                                                    agent_id=HOST,
                                                    device=device1)

                    p2 = port2['port']
                    device2 = 'tap' + p2['id']

                    #Bringing the second port up
                    #Assert no rpc is sent
                    self.mock_fanout.reset_mock()
                    self.mock_cast.reset_mock()
                    self.callbacks.update_device_up(self.adminContext,
                                                    agent_id=HOST,
                                                    device=device2)

                    self.assertFalse(self.mock_fanout.called)
                    self.assertFalse(self.mock_cast.called)

                    #Bringing the second port down
                    #Assert no rpc is sent
                    self.mock_fanout.reset_mock()
                    self.mock_cast.reset_mock()
                    self.callbacks.update_device_down(self.adminContext,
                                                      agent_id=HOST,
                                                      device=device2)

                    self.assertFalse(self.mock_fanout.called)
                    self.assertFalse(self.mock_cast.called)

                    #Bringing the second port down and delete it
                    #Assert no rpc is sent

                    self.callbacks.update_device_up(self.adminContext,
                                                    agent_id=HOST,
                                                    device=device2)
                    self.mock_fanout.reset_mock()
                    self.mock_cast.reset_mock()

                self.assertFalse(self.mock_cast.called)
                fdb_exists = self._fdb_entries_exists(
                    self.mock_fanout.call_args_list)
                self.assertFalse(fdb_exists)

    def test_port_up_called_two_networks(self):
        self._register_ml2_agents()

        with self.subnet(network=self._network) as subnet:
            host_arg = {portbindings.HOST_ID: HOST + '_2'}
            with self.port(subnet=subnet,
                           arg_list=(portbindings.HOST_ID,),
                           **host_arg) as port1:
                with self.subnet(cidr='10.1.0.0/24') as subnet2:
                    with self.port(subnet=subnet2,
                                   arg_list=(portbindings.HOST_ID,),
                                   **host_arg):
                        host_arg = {portbindings.HOST_ID: HOST}
                        with self.port(subnet=subnet,
                                       arg_list=(portbindings.HOST_ID,),
                                       **host_arg) as port3:
                            p1 = port1['port']
                            device1 = 'tap' + p1['id']

                            p3 = port3['port']
                            device3 = 'tap' + p3['id']

                            self.callbacks.update_device_up(
                                self.adminContext, agent_id=HOST,
                                device=device1)

                            self.mock_cast.reset_mock()
                            self.mock_fanout.reset_mock()
                            self.callbacks.update_device_up(
                                self.adminContext, agent_id=HOST,
                                device=device3)

                            p1_ips = [p['ip_address']
                                      for p in p1['fixed_ips']]
                            expected1 = {'args':
                                         {'fdb_entries':
                                          {p1['network_id']:
                                           {'ports':
                                            {'20.0.0.2':
                                             [constants.FLOODING_ENTRY,
                                              [p1['mac_address'],
                                               p1_ips[0]]]},
                                            'network_type': 'vxlan',
                                            'segment_id': 1}}},
                                         'namespace': None,
                                         'method': 'add_fdb_entries'}

                            topic = topics.get_topic_name(topics.AGENT,
                                                          topics.L2POPULATION,
                                                          topics.UPDATE,
                                                          HOST)

                            self.mock_cast.assert_called_with(mock.ANY,
                                                              expected1,
                                                              topic=topic)

                            p3_ips = [p['ip_address']
                                      for p in p3['fixed_ips']]
                            expected2 = {'args':
                                         {'fdb_entries':
                                          {p1['network_id']:
                                           {'ports':
                                            {'20.0.0.1':
                                             [constants.FLOODING_ENTRY,
                                              [p3['mac_address'],
                                               p3_ips[0]]]},
                                            'network_type': 'vxlan',
                                            'segment_id': 1}}},
                                         'namespace': None,
                                         'method': 'add_fdb_entries'}

                            self.mock_fanout.assert_called_with(
                                mock.ANY, expected2,
                                topic=self.fanout_topic)

    def test_delete_port_down_called(self):
        """
        Test the deletion of a port that is down.
        No fanout should be sent
        """ 
        self._register_ml2_agents()

        with self.subnet(network=self._network) as subnet:
            host_arg = {portbindings.HOST_ID: HOST}
            host2_arg = {portbindings.HOST_ID: L2_AGENT_2["host"]}
            with self.port(subnet=subnet,
                           arg_list=(portbindings.HOST_ID,),
                           **host_arg) as port1:
                with self.port(subnet=subnet,
                               arg_list=(portbindings.HOST_ID,),
                               **host2_arg) as port2:
                    with self.port(subnet=subnet,
                                   arg_list=(portbindings.HOST_ID,),
                                   **host_arg) as port3:

                        p1 = port1['port']
                        device1 = 'tap' + p1['id']
                        self.callbacks.update_device_up(self.adminContext,
                                                        agent_id=HOST,
                                                        device=device1)
                        p2 = port2['port']
                        device2 = 'tap' + p2['id']
                        self.callbacks.update_device_up(self.adminContext,
                                                        agent_id=L2_AGENT_2["host"],
                                                        device=device2)
                        p3 = port3['port']

                        self.mock_fanout.reset_mock()

                    fdb_exists = self._fdb_entries_exists(
                        self.mock_fanout.call_args_list)

                    self.assertFalse(fdb_exists)


    def test_fixed_ips_changed(self):
        self._register_ml2_agents()

        with self.subnet(network=self._network) as subnet:
            host_arg = {portbindings.HOST_ID: HOST}
            with self.port(subnet=subnet, cidr='10.0.0.0/24',
                           arg_list=(portbindings.HOST_ID,),
                           **host_arg) as port1:
                p1 = port1['port']

                device = 'tap' + p1['id']

                self.callbacks.update_device_up(self.adminContext,
                                                agent_id=HOST,
                                                device=device)

                self.mock_fanout.reset_mock()

                data = {'port': {'fixed_ips': [{'ip_address': '10.0.0.2'},
                                               {'ip_address': '10.0.0.10'}]}}
                req = self.new_update_request('ports', data, p1['id'])
                res = self.deserialize(self.fmt, req.get_response(self.api))
                ips = res['port']['fixed_ips']
                self.assertEqual(len(ips), 2)

                add_expected = {'args':
                                {'fdb_entries':
                                 {'chg_ip':
                                  {p1['network_id']:
                                   {'20.0.0.1':
                                    {'after': [[p1['mac_address'],
                                                '10.0.0.10']]}}}}},
                                'namespace': None,
                                'method': 'update_fdb_entries'}

                self.mock_fanout.assert_any_call(
                    mock.ANY, add_expected, topic=self.fanout_topic)

                self.mock_fanout.reset_mock()

                data = {'port': {'fixed_ips': [{'ip_address': '10.0.0.2'},
                                               {'ip_address': '10.0.0.16'}]}}
                req = self.new_update_request('ports', data, p1['id'])
                res = self.deserialize(self.fmt, req.get_response(self.api))
                ips = res['port']['fixed_ips']
                self.assertEqual(len(ips), 2)

                upd_expected = {'args':
                                {'fdb_entries':
                                 {'chg_ip':
                                  {p1['network_id']:
                                   {'20.0.0.1':
                                    {'before': [[p1['mac_address'],
                                                 '10.0.0.10']],
                                     'after': [[p1['mac_address'],
                                                '10.0.0.16']]}}}}},
                                'namespace': None,
                                'method': 'update_fdb_entries'}

                self.mock_fanout.assert_any_call(
                    mock.ANY, upd_expected, topic=self.fanout_topic)

                self.mock_fanout.reset_mock()

                data = {'port': {'fixed_ips': [{'ip_address': '10.0.0.16'}]}}
                req = self.new_update_request('ports', data, p1['id'])
                res = self.deserialize(self.fmt, req.get_response(self.api))
                ips = res['port']['fixed_ips']
                self.assertEqual(len(ips), 1)

                del_expected = {'args':
                                {'fdb_entries':
                                 {'chg_ip':
                                  {p1['network_id']:
                                   {'20.0.0.1':
                                    {'before': [[p1['mac_address'],
                                                 '10.0.0.2']]}}}}},
                                'namespace': None,
                                'method': 'update_fdb_entries'}

                self.mock_fanout.assert_any_call(
                    mock.ANY, del_expected, topic=self.fanout_topic)

    def test_no_fdb_updates_without_port_updates(self):
        self._register_ml2_agents()

        with self.subnet(network=self._network) as subnet:
            host_arg = {portbindings.HOST_ID: HOST}
            with self.port(subnet=subnet, cidr='10.0.0.0/24',
                           arg_list=(portbindings.HOST_ID,),
                           **host_arg) as port1:
                p1 = port1['port']

                device = 'tap' + p1['id']

                self.callbacks.update_device_up(self.adminContext,
                                                agent_id=HOST,
                                                device=device)
                p1['status'] = 'ACTIVE'
                self.mock_fanout.reset_mock()

                fanout = ('neutron.plugins.ml2.drivers.l2pop.rpc.'
                          'L2populationAgentNotifyAPI._notification_fanout')
                fanout_patch = mock.patch(fanout)
                mock_fanout = fanout_patch.start()

                plugin = manager.NeutronManager.get_plugin()
                plugin.update_port(self.adminContext, p1['id'], port1)

                self.assertFalse(mock_fanout.called)
                fanout_patch.stop()

    def test_host_changed(self):
        self._register_ml2_agents()

        with self.subnet(network=self._network) as subnet:
            host_arg = {portbindings.HOST_ID: HOST}
            host3_arg = {portbindings.HOST_ID: L2_AGENT_3["host"]}
            with self.port(subnet=subnet, cidr='10.0.0.0/24',
                           arg_list=(portbindings.HOST_ID,),
                           **host_arg) as port1:
                # only one port moved to a new host, no other port exists
                # on any host
                # assert no rpc call
                p1 = port1['port']
                p1_ips = [p['ip_address'] for p in p1['fixed_ips']]
                device1 = 'tap' + p1['id']
                self.callbacks.update_device_up(self.adminContext,
                                                agent_id=HOST,
                                                device=device1)

                data2 = {'port': {'binding:host_id': L2_AGENT_2['host']}}

                self.mock_cast.reset_mock()
                self.mock_fanout.reset_mock()

                req = self.new_update_request('ports', data2, p1['id'])
                res = self.deserialize(self.fmt,
                                       req.get_response(self.api))
                self.assertEqual(res['port']['binding:host_id'],
                                 L2_AGENT_2['host'])
                fdb_exists = self._fdb_entries_exists(
                    self.mock_fanout.call_args_list)
                self.assertFalse(fdb_exists)
                self.assertFalse(self.mock_cast.called)

                # bring up a second port on host 3 and migrate it on host 1
                # assert that fanout update fdb is sent with flooding entries
                # assert that cast add_fdb is sent to host 1 with flooding
                # entries to agent 2
                with self.port(subnet=subnet, cidr='10.0.0.0/24',
                           arg_list=(portbindings.HOST_ID,),
                           **host3_arg) as port2:
                    p2 = port2['port']
                    p2_ips = [p['ip_address'] for p in p2['fixed_ips']]
                    device2 = 'tap' + p2['id']
                    self.callbacks.update_device_up(self.adminContext,
                                                agent_id=L2_AGENT_3["host"],
                                                device=device2)
                    data1 = {'port': {'binding:host_id': L2_AGENT['host']}}

                    self.mock_cast.reset_mock()
                    self.mock_fanout.reset_mock()

                    req = self.new_update_request('ports', data1, p2['id'])
                    res = self.deserialize(self.fmt,
                                           req.get_response(self.api))
                    self.assertEqual(res['port']['binding:host_id'],
                                     L2_AGENT['host'])
                    upd_expected = {'args':
                                    {'fdb_entries':
                                     {'chg_host':
                                      {p2['network_id']:
                                       {'ports':
                                           {'before':
                                            {L2_AGENT_3['configurations']
                                             ['tunneling_ip']:
                                             [constants.FLOODING_ENTRY,
                                              [p2['mac_address'], p2_ips[0]]]
                                             },
                                            'after':
                                            {L2_AGENT['configurations']
                                             ['tunneling_ip']:
                                             [constants.FLOODING_ENTRY,
                                              [p2['mac_address'], p2_ips[0]]]
                                             }}}}}},
                                    'namespace': None,
                                    'method': 'update_fdb_entries'}
                    self.mock_fanout.assert_any_call(mock.ANY,
                                                     upd_expected,
                                                     topic=self.fanout_topic)
                    add_expected = {'args':
                                     {'fdb_entries':
                                      {p1['network_id']:
                                       {'ports':
                                        {L2_AGENT_2['configurations']
                                        ['tunneling_ip']:
                                         [constants.FLOODING_ENTRY,
                                          [p1['mac_address'],
                                           p1_ips[0]]]},
                                        'network_type': 'vxlan',
                                        'segment_id': 1}}},
                                     'namespace': None,
                                     'method': 'add_fdb_entries'}

                    topic = topics.get_topic_name(topics.AGENT,
                              topics.L2POPULATION,
                              topics.UPDATE,
                              L2_AGENT["host"])
                    self.mock_cast.assert_called_with(mock.ANY,
                                                      add_expected,
                                                      topic=topic)


                    # the second port migrates on the second host
                    # assert no fanout is sent
                    # assert a cast is sent to the second host to remove its 
                    # fdb entries to previous host
                    self.mock_cast.reset_mock()
                    self.mock_fanout.reset_mock()

                    req = self.new_update_request('ports', data2, p2['id'])
                    res = self.deserialize(self.fmt,
                                           req.get_response(self.api))
                    self.assertEqual(res['port']['binding:host_id'],
                                     L2_AGENT_2['host'])

                    fdb_exists = self._fdb_entries_exists(
                        self.mock_fanout.call_args_list)
                    self.assertFalse(fdb_exists)
                    expected1 = {'args':
                                 {'fdb_entries':
                                  {p2['network_id']:
                                   {'ports':
                                    {L2_AGENT['configurations']
                                     ['tunneling_ip']: 
                                     [constants.FLOODING_ENTRY,
                                      [p2['mac_address'],
                                       p2_ips[0]]]},
                                    'network_type': 'vxlan',
                                    'segment_id': 1}}},
                                 'namespace': None,
                                 'method': 'remove_fdb_entries'}

                    topic = topics.get_topic_name(topics.AGENT,
                              topics.L2POPULATION,
                              topics.UPDATE,
                              L2_AGENT_2["host"])
                    self.mock_cast.assert_called_with(mock.ANY,
                                                      expected1,
                                                      topic=topic)

                    with self.port(subnet=subnet, cidr='10.0.0.0/24',
                           arg_list=(portbindings.HOST_ID,),
                           **host3_arg) as port3:
                        # bring up a third port on host 3 and migrate the
                        # second port on host 3
                        # assert that fanout update fdb is sent without
                        # flooding entries
                        p3 = port3['port']
                        p3_ips = [p['ip_address'] for p in p3['fixed_ips']]
                        device3 = 'tap' + p3['id']
                        self.callbacks.update_device_up(
                            self.adminContext,
                            agent_id=L2_AGENT_3["host"],
                            device=device3)
                        data3 = {'port': 
                                 {'binding:host_id': L2_AGENT_3['host']}}

                        self.mock_cast.reset_mock()
                        self.mock_fanout.reset_mock()

                        req = self.new_update_request('ports', data3, p2['id'])
                        res = self.deserialize(self.fmt,
                                               req.get_response(self.api))
                        self.assertEqual(res['port']['binding:host_id'],
                                         L2_AGENT_3['host'])
                        upd_expected = {'args':
                                        {'fdb_entries':
                                         {'chg_host':
                                          {p2['network_id']:
                                           {'ports':
                                               {'before':
                                                {L2_AGENT_2['configurations']
                                                 ['tunneling_ip']:
                                                  [[p2['mac_address'],
                                                    p2_ips[0]]]
                                                 },
                                                'after':
                                                {L2_AGENT_3['configurations']
                                                 ['tunneling_ip']:
                                                  [[p2['mac_address'],
                                                    p2_ips[0]]]
                                                 }}}}}},
                                        'namespace': None,
                                        'method': 'update_fdb_entries'}
                        self.mock_fanout.assert_any_call(
                            mock.ANY,
                            upd_expected,
                            topic=self.fanout_topic)

                        # migrate the second port to host1
                        # assert that fanout update fdb is sent with
                        # flooding entries for the host1
                        # assert cast is sent to host1
                        self.mock_cast.reset_mock()
                        self.mock_fanout.reset_mock()

                        req = self.new_update_request('ports', data1, p2['id'])
                        res = self.deserialize(self.fmt,
                                               req.get_response(self.api))
                        self.assertEqual(res['port']['binding:host_id'],
                                         L2_AGENT['host'])
                        upd_expected = {'args':
                                    {'fdb_entries':
                                     {'chg_host':
                                      {p2['network_id']:
                                       {'ports':
                                           {'before':
                                            {L2_AGENT_3['configurations']
                                             ['tunneling_ip']:
                                             [[p2['mac_address'], p2_ips[0]]]
                                             },
                                            'after':
                                            {L2_AGENT['configurations']
                                             ['tunneling_ip']:
                                             [constants.FLOODING_ENTRY,
                                              [p2['mac_address'], p2_ips[0]]]
                                             }}}}}},
                                    'namespace': None,
                                    'method': 'update_fdb_entries'}
                        self.mock_fanout.assert_any_call(
                            mock.ANY,
                            upd_expected,
                            topic=self.fanout_topic)
                        add_expected = {'args':
                                         {'fdb_entries':
                                          {p1['network_id']:
                                           {'ports':
                                            {L2_AGENT_2['configurations']
                                             ['tunneling_ip']:
                                             [constants.FLOODING_ENTRY,
                                             [p1['mac_address'],
                                             p1_ips[0]]],
                                             L2_AGENT_3['configurations']
                                             ['tunneling_ip']: 
                                             [constants.FLOODING_ENTRY,
                                             [p3['mac_address'],
                                              p3_ips[0]]]},
                                            'network_type': 'vxlan',
                                            'segment_id': 1}}},
                                         'namespace': None,
                                         'method': 'add_fdb_entries'}

                        topic = topics.get_topic_name(topics.AGENT,
                                  topics.L2POPULATION,
                                  topics.UPDATE,
                                  L2_AGENT["host"])
                        self.mock_cast.assert_called_with(mock.ANY,
                                                          add_expected,
                                                          topic=topic)

                        # migrate the second port to host2
                        # assert a fanout is sent to with flooding entry
                        # deletion for port host1
                        self.mock_cast.reset_mock()
                        self.mock_fanout.reset_mock()

                        req = self.new_update_request('ports', data2, p2['id'])
                        res = self.deserialize(self.fmt,
                                               req.get_response(self.api))
                        self.assertEqual(res['port']['binding:host_id'],
                                         L2_AGENT_2['host'])
                        upd_expected = {'args':
                                    {'fdb_entries':
                                     {'chg_host':
                                      {p2['network_id']:
                                       {'ports':
                                           {'before':
                                            {L2_AGENT['configurations']
                                             ['tunneling_ip']:
                                              [constants.FLOODING_ENTRY,
                                               [p2['mac_address'], p2_ips[0]]]
                                             },
                                            'after':
                                            {L2_AGENT_2['configurations']
                                             ['tunneling_ip']:
                                              [[p2['mac_address'], p2_ips[0]]]
                                             }}}}}},
                                    'namespace': None,
                                    'method': 'update_fdb_entries'}
                        self.mock_fanout.assert_any_call(
                            mock.ANY,
                            upd_expected,
                            topic=self.fanout_topic)
