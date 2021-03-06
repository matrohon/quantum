# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013, Nachi Ueno, NTT MCL, Inc.
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

import contextlib
from oslo.config import cfg
from webob import exc

from neutron.common.test_lib import test_config
from neutron.db import extraroute_db
from neutron.extensions import extraroute
from neutron.extensions import l3
from neutron.openstack.common import log as logging
from neutron.openstack.common.notifier import api as notifier_api
from neutron.openstack.common.notifier import test_notifier
from neutron.openstack.common import uuidutils
from neutron.tests.unit import test_api_v2
from neutron.tests.unit import test_l3_plugin as test_l3


LOG = logging.getLogger(__name__)

_uuid = uuidutils.generate_uuid
_get_path = test_api_v2._get_path


class ExtraRouteTestExtensionManager(object):

    def get_resources(self):
        l3.RESOURCE_ATTRIBUTE_MAP['routers'].update(
            extraroute.EXTENDED_ATTRIBUTES_2_0['routers'])
        return l3.L3.get_resources()

    def get_actions(self):
        return []

    def get_request_extensions(self):
        return []


# This plugin class is just for testing
class TestExtraRoutePlugin(test_l3.TestL3NatPlugin,
                           extraroute_db.ExtraRoute_db_mixin):
    supported_extension_aliases = ["router", "extraroute"]


class ExtraRouteDBTestCase(test_l3.L3NatDBTestCase):

    def setUp(self):
        test_config['plugin_name_v2'] = (
            'neutron.tests.unit.'
            'test_extension_extraroute.TestExtraRoutePlugin')
        # for these tests we need to enable overlapping ips
        cfg.CONF.set_default('allow_overlapping_ips', True)
        cfg.CONF.set_default('max_routes', 3)
        ext_mgr = ExtraRouteTestExtensionManager()
        test_config['extension_manager'] = ext_mgr
        #L3NatDBTestCase will overwrite plugin_name_v2,
        #so we don't need to setUp on the class here
        super(test_l3.L3NatTestCaseBase, self).setUp()

        # Set to None to reload the drivers
        notifier_api._drivers = None
        cfg.CONF.set_override("notification_driver", [test_notifier.__name__])

    def test_route_update_with_one_route(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    body = self._show('routers', r['router']['id'])
                    body = self._router_interface_action('add',
                                                         r['router']['id'],
                                                         None,
                                                         p['port']['id'])

                    routes = [{'destination': '135.207.0.0/16',
                               'nexthop': '10.0.1.3'}]

                    body = self._update('routers', r['router']['id'],
                                        {'router': {'routes': routes}})

                    body = self._show('routers', r['router']['id'])
                    self.assertEqual(body['router']['routes'],
                                     routes)
                    self._update('routers', r['router']['id'],
                                 {'router': {'routes': []}})
                    # clean-up
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_router_interface_in_use_by_route(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    body = self._router_interface_action('add',
                                                         r['router']['id'],
                                                         None,
                                                         p['port']['id'])

                    routes = [{'destination': '135.207.0.0/16',
                               'nexthop': '10.0.1.3'}]

                    body = self._update('routers', r['router']['id'],
                                        {'router': {'routes': routes}})

                    body = self._show('routers', r['router']['id'])
                    self.assertEqual(body['router']['routes'],
                                     routes)

                    self._router_interface_action(
                        'remove',
                        r['router']['id'],
                        None,
                        p['port']['id'],
                        expected_code=exc.HTTPConflict.code)

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes': []}})
                    # clean-up
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_route_update_with_multi_routes(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    body = self._router_interface_action('add',
                                                         r['router']['id'],
                                                         None,
                                                         p['port']['id'])

                    routes = [{'destination': '135.207.0.0/16',
                               'nexthop': '10.0.1.3'},
                              {'destination': '12.0.0.0/8',
                               'nexthop': '10.0.1.4'},
                              {'destination': '141.212.0.0/16',
                               'nexthop': '10.0.1.5'}]

                    body = self._update('routers', r['router']['id'],
                                        {'router': {'routes': routes}})

                    body = self._show('routers', r['router']['id'])
                    self.assertEqual(sorted(body['router']['routes']),
                                     sorted(routes))

                    # clean-up
                    self._update('routers', r['router']['id'],
                                 {'router': {'routes': []}})
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_router_update_delete_routes(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    body = self._router_interface_action('add',
                                                         r['router']['id'],
                                                         None,
                                                         p['port']['id'])

                    routes_orig = [{'destination': '135.207.0.0/16',
                                    'nexthop': '10.0.1.3'},
                                   {'destination': '12.0.0.0/8',
                                    'nexthop': '10.0.1.4'},
                                   {'destination': '141.212.0.0/16',
                                    'nexthop': '10.0.1.5'}]

                    body = self._update('routers', r['router']['id'],
                                        {'router': {'routes':
                                                    routes_orig}})

                    body = self._show('routers', r['router']['id'])
                    self.assertEqual(sorted(body['router']['routes']),
                                     sorted(routes_orig))

                    routes_left = [{'destination': '135.207.0.0/16',
                                    'nexthop': '10.0.1.3'},
                                   {'destination': '141.212.0.0/16',
                                    'nexthop': '10.0.1.5'}]

                    body = self._update('routers', r['router']['id'],
                                        {'router': {'routes':
                                                    routes_left}})

                    body = self._show('routers', r['router']['id'])
                    self.assertEqual(sorted(body['router']['routes']),
                                     sorted(routes_left))

                    body = self._update('routers', r['router']['id'],
                                        {'router': {'routes': []}})

                    body = self._show('routers', r['router']['id'])
                    self.assertEqual(body['router']['routes'], [])

                    # clean-up
                    self._update('routers', r['router']['id'],
                                 {'router': {'routes': []}})
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def _test_malformed_route(self, routes):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    self._router_interface_action('add',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes': routes}},
                                 expected_code=exc.HTTPBadRequest.code)
                    # clean-up
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_no_destination_route(self):
        self._test_malformed_route([{'nexthop': '10.0.1.6'}])

    def test_no_nexthop_route(self):
        self._test_malformed_route({'destination': '135.207.0.0/16'})

    def test_none_destination(self):
        self._test_malformed_route([{'destination': None,
                                     'nexthop': '10.0.1.3'}])

    def test_none_nexthop(self):
        self._test_malformed_route([{'destination': '135.207.0.0/16',
                                     'nexthop': None}])

    def test_nexthop_is_port_ip(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    self._router_interface_action('add',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])
                    port_ip = p['port']['fixed_ips'][0]['ip_address']
                    routes = [{'destination': '135.207.0.0/16',
                               'nexthop': port_ip}]

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes':
                                             routes}},
                                 expected_code=exc.HTTPBadRequest.code)
                    # clean-up
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_router_update_with_too_many_routes(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    self._router_interface_action('add',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

                    routes = [{'destination': '135.207.0.0/16',
                               'nexthop': '10.0.1.3'},
                              {'destination': '12.0.0.0/8',
                               'nexthop': '10.0.1.4'},
                              {'destination': '141.212.0.0/16',
                               'nexthop': '10.0.1.5'},
                              {'destination': '192.168.0.0/16',
                               'nexthop': '10.0.1.6'}]

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes':
                                             routes}},
                                 expected_code=exc.HTTPBadRequest.code)

                    # clean-up
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_router_update_with_dup_address(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    self._router_interface_action('add',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

                    routes = [{'destination': '135.207.0.0/16',
                               'nexthop': '10.0.1.3'},
                              {'destination': '135.207.0.0/16',
                               'nexthop': '10.0.1.3'}]

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes':
                                             routes}},
                                 expected_code=exc.HTTPBadRequest.code)

                    # clean-up
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_router_update_with_invalid_ip_address(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    self._router_interface_action('add',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

                    routes = [{'destination': '512.207.0.0/16',
                               'nexthop': '10.0.1.3'}]

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes':
                                             routes}},
                                 expected_code=exc.HTTPBadRequest.code)

                    routes = [{'destination': '127.207.0.0/48',
                               'nexthop': '10.0.1.3'}]

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes':
                                             routes}},
                                 expected_code=exc.HTTPBadRequest.code)

                    routes = [{'destination': 'invalid_ip_address',
                               'nexthop': '10.0.1.3'}]

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes':
                                             routes}},
                                 expected_code=exc.HTTPBadRequest.code)

                    # clean-up
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_router_update_with_invalid_nexthop_ip(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    self._router_interface_action('add',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

                    routes = [{'destination': '127.207.0.0/16',
                               'nexthop': ' 300.10.10.4'}]

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes':
                                             routes}},
                                 expected_code=exc.HTTPBadRequest.code)

                    # clean-up
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_router_update_with_nexthop_is_outside_port_subnet(self):
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                with self.port(subnet=s, no_delete=True) as p:
                    self._router_interface_action('add',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

                    routes = [{'destination': '127.207.0.0/16',
                               'nexthop': ' 20.10.10.4'}]

                    self._update('routers', r['router']['id'],
                                 {'router': {'routes':
                                             routes}},
                                 expected_code=exc.HTTPBadRequest.code)

                    # clean-up
                    self._router_interface_action('remove',
                                                  r['router']['id'],
                                                  None,
                                                  p['port']['id'])

    def test_router_update_on_external_port(self):
        DEVICE_OWNER_ROUTER_GW = "network:router_gateway"
        with self.router() as r:
            with self.subnet(cidr='10.0.1.0/24') as s:
                self._set_net_external(s['subnet']['network_id'])
                self._add_external_gateway_to_router(
                    r['router']['id'],
                    s['subnet']['network_id'])
                body = self._show('routers', r['router']['id'])
                net_id = body['router']['external_gateway_info']['network_id']
                self.assertEqual(net_id, s['subnet']['network_id'])
                port_res = self._list_ports('json',
                                            200,
                                            s['subnet']['network_id'],
                                            tenant_id=r['router']['tenant_id'],
                                            device_own=DEVICE_OWNER_ROUTER_GW)
                port_list = self.deserialize('json', port_res)
                self.assertEqual(len(port_list['ports']), 1)

                routes = [{'destination': '135.207.0.0/16',
                           'nexthop': '10.0.1.3'}]

                body = self._update('routers', r['router']['id'],
                                    {'router': {'routes':
                                                routes}})

                body = self._show('routers', r['router']['id'])
                self.assertEqual(body['router']['routes'],
                                 routes)

                self._remove_external_gateway_from_router(
                    r['router']['id'],
                    s['subnet']['network_id'])
                body = self._show('routers', r['router']['id'])
                gw_info = body['router']['external_gateway_info']
                self.assertEqual(gw_info, None)

    def test_router_list_with_sort(self):
        with contextlib.nested(self.router(name='router1'),
                               self.router(name='router2'),
                               self.router(name='router3')
                               ) as (router1, router2, router3):
            self._test_list_with_sort('router', (router3, router2, router1),
                                      [('name', 'desc')])

    def test_router_list_with_pagination(self):
        with contextlib.nested(self.router(name='router1'),
                               self.router(name='router2'),
                               self.router(name='router3')
                               ) as (router1, router2, router3):
            self._test_list_with_pagination('router',
                                            (router1, router2, router3),
                                            ('name', 'asc'), 2, 2)

    def test_router_list_with_pagination_reverse(self):
        with contextlib.nested(self.router(name='router1'),
                               self.router(name='router2'),
                               self.router(name='router3')
                               ) as (router1, router2, router3):
            self._test_list_with_pagination_reverse('router',
                                                    (router1, router2,
                                                     router3),
                                                    ('name', 'asc'), 2, 2)


class ExtraRouteDBTestCaseXML(ExtraRouteDBTestCase):
    fmt = 'xml'
