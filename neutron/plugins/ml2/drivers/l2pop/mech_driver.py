# Copyright (c) 2013 OpenStack Foundation.
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

from oslo.config import cfg

from neutron.common import constants as const
from neutron import context as n_context
from neutron.db import api as db_api
from neutron.openstack.common import log as logging
from neutron.plugins.ml2 import driver_api as api
from neutron.plugins.ml2.drivers.l2pop import config  # noqa
from neutron.plugins.ml2.drivers.l2pop import db as l2pop_db
from neutron.plugins.ml2.drivers.l2pop import rpc as l2pop_rpc

LOG = logging.getLogger(__name__)


class L2populationMechanismDriver(api.MechanismDriver,
                                  l2pop_db.L2populationDbMixin):

    def initialize(self):
        LOG.debug(_("Experimental L2 population driver"))
        self.rpc_ctx = n_context.get_admin_context_without_session()

    def _get_port_fdb_entries(self, port):
        return [[port['mac_address'],
                 ip['ip_address']] for ip in port['fixed_ips']]

    def delete_port_precommit(self, context):
        # we need to get this information before the port get removed
        # it can't be found anymore during the postcommit

        # Other agent will change their fdb entry only if the deleted port 
        # was active
        self._fdb_to_del = {}
        LOG.info(_("delete_port_precommit"))
        if context.current['status'] == const.PORT_STATUS_ACTIVE:
            LOG.info(_("delete_port_precommit : port_active"))
            self._fdb_to_del = self._get_other_fdb_entries_to_remove(context)

    def delete_port_postcommit(self, context):
        if self._fdb_to_del:
            l2pop_rpc.L2populationAgentNotify.remove_fdb_entries(
                self.rpc_ctx, self._fdb_to_del)

    def _get_diff_ips(self, orig, port):
        orig_ips = set([ip['ip_address'] for ip in orig['fixed_ips']])
        port_ips = set([ip['ip_address'] for ip in port['fixed_ips']])

        # check if an ip has been added or removed
        orig_chg_ips = orig_ips.difference(port_ips)
        port_chg_ips = port_ips.difference(orig_ips)

        if orig_chg_ips or port_chg_ips:
            return orig_chg_ips, port_chg_ips

    def _fixed_ips_changed(self, context, orig, port, diff_ips):
        orig_ips, port_ips = diff_ips

        port_infos = self._get_port_infos(context, orig)
        if not port_infos:
            return
        agent, agent_ip, segment, port_fdb_entries = port_infos

        orig_mac_ip = [[port['mac_address'], ip] for ip in orig_ips]
        port_mac_ip = [[port['mac_address'], ip] for ip in port_ips]

        upd_fdb_entries = {port['network_id']: {agent_ip: {}}}

        ports = upd_fdb_entries[port['network_id']][agent_ip]
        if orig_mac_ip:
            ports['before'] = orig_mac_ip

        if port_mac_ip:
            ports['after'] = port_mac_ip

        l2pop_rpc.L2populationAgentNotify.update_fdb_entries(
            self.rpc_ctx, {'chg_ip': upd_fdb_entries})

        return True

    def _host_changed(self, context, orig, port):
        #cast to new host
        orig_infos = self._get_port_infos(context, orig)
        if not orig_infos:
            return
        orig_agent, orig_agent_ip, segment, orig_port_fdb_entries = orig_infos

        port_infos = self._get_port_infos(context, port)
        if not port_infos:
            return
        port_agent, port_agent_ip, segment, port_fdb_entries = port_infos

        agent_host = port['binding:host_id']
        network_id = port['network_id']

        upd_fdb_entries = {network_id:
                           {'ports':
                            {'before':
                             {orig_agent_ip: orig_port_fdb_entries},
                             'after':
                             {port_agent_ip: port_fdb_entries}
                             }}}
        ports = upd_fdb_entries[network_id]['ports']

        session = db_api.get_session()
        agent_ports = self.get_agent_net_port_up_count(session,
                                                       agent_host,
                                                       network_id)
        if agent_ports == 1:
            # First port plugged on current agent in this network,
            # we have to provide it with the whole list of fdb entries
            agent_fdb_entries = self._get_agent_fdb_entries(agent_host,
                                                            network_id,
                                                            segment)
            # Notify agent only if there is another agent which hosts this
            # network
            if agent_fdb_entries[network_id]['ports'].keys():
                l2pop_rpc.L2populationAgentNotify.add_fdb_entries(
                    self.rpc_ctx, agent_fdb_entries, agent_host)

            ports['after'][port_agent_ip].append(const.FLOODING_ENTRY)

        old_agent_host = orig['binding:host_id']
        old_agent_ports = self.get_agent_net_port_up_count(session,
                                                           old_agent_host,
                                                           network_id)
        if old_agent_ports == 0:
            # the last port of this network of the previous host, has been
            # removed, so remove broadcast entries to the previous host
            ports['before'][orig_agent_ip].append(const.FLOODING_ENTRY)

        l2pop_rpc.L2populationAgentNotify.update_fdb_entries(
            self.rpc_ctx, {'chg_host': upd_fdb_entries})

    def update_port_postcommit(self, context):
        LOG.info(_("update_port_postcommit"))
        port = context.current
        orig = context.original

        diff_ips = self._get_diff_ips(orig, port)
        if diff_ips:
            self._fixed_ips_changed(context, orig, port, diff_ips)
        elif port['binding:host_id'] != orig['binding:host_id']:
            # check if binding has changed
            self._host_changed(context, orig, port)
        elif port['status'] != orig['status']:
            if port['status'] == const.PORT_STATUS_ACTIVE:
                self._update_port_up(context)
            elif port['status'] == const.PORT_STATUS_DOWN:
                self._update_port_down(context)


    def _get_port_infos(self, context, port):
        agent_host = port['binding:host_id']
        if not agent_host:
            return

        session = db_api.get_session()
        agent = self.get_agent_by_host(session, agent_host)
        if not agent:
            return

        agent_ip = self.get_agent_ip(agent)
        if not agent_ip:
            LOG.warning(_("Unable to retrieve the agent ip, check the agent "
                          "configuration."))
            return

        segment = context.bound_segment
        if not segment:
            LOG.warning(_("Port %(port)s updated by agent %(agent)s "
                          "isn't bound to any segment"),
                        {'port': port['id'], 'agent': agent})
            return

        tunnel_types = self.get_agent_tunnel_types(agent)
        if segment['network_type'] not in tunnel_types:
            return

        fdb_entries = self._get_port_fdb_entries(port)

        return agent, agent_ip, segment, fdb_entries

    #build fdb entries for an agent
    def _get_agent_fdb_entries(self, agent_host, network_id, segment):
        agent_fdb_entries = {network_id:
                             {'segment_id': segment['segmentation_id'],
                              'network_type': segment['network_type'],
                              'ports': {}}}
        ports = agent_fdb_entries[network_id]['ports']

        session = db_api.get_session()
        network_ports = self.get_net_ports_up(session, network_id)
        for network_port in network_ports:
            binding, agent = network_port
            if agent.host == agent_host:
                continue

            ip = self.get_agent_ip(agent)
            if not ip:
                LOG.debug(_("Unable to retrieve the agent ip, check "
                            "the agent %(agent_host)s configuration."),
                          {'agent_host': agent.host})
                continue

            agent_ports = ports.get(ip, [const.FLOODING_ENTRY])
            agent_ports += self._get_port_fdb_entries(binding.port)
            ports[ip] = agent_ports

        return agent_fdb_entries

    def _update_port_up(self, context):
        LOG.info(_("update_port_up"))
        port_context = context.current
        port_infos = self._get_port_infos(context, port_context)
        if not port_infos:
            return
        agent, agent_ip, segment, port_fdb_entries = port_infos

        agent_host = port_context['binding:host_id']
        network_id = port_context['network_id']

        session = db_api.get_session()
        net_port_count_on_agt = self.get_agent_net_port_up_count(session,
                                                                     agent_host,
                                                                     network_id)
        # fdb entries for agent that host the port
        # agent_fdb_entries = {}
        net_port_count = self.get_net_ports_up(session, network_id).count()

        # if other agents hosts a port on this network, notify them and notifiy
        # the the agent if needed
        if (net_port_count - net_port_count_on_agt > 0 ):

            # fdb entries for orther agent 
            other_fdb_entries = {network_id:
                                 {'segment_id': segment['segmentation_id'],
                                  'network_type': segment['network_type'],
                                  'ports': {agent_ip: []}}}

            if net_port_count_on_agt == 1:
                # this port is the first for this network on the agent
                # notify other agents to add a flooding entry
                other_fdb_entries[network_id]['ports'][agent_ip].append(
                    const.FLOODING_ENTRY)

            other_fdb_entries[network_id]['ports'][agent_ip] += port_fdb_entries
            l2pop_rpc.L2populationAgentNotify.add_fdb_entries(self.rpc_ctx,
                                                          other_fdb_entries)


            if net_port_count_on_agt == 1 or (
                    self.get_agent_uptime(agent) < cfg.CONF.l2pop.agent_boot_time):
                # this port is the first for this network on the agent
                # we have to provide it with the whole list of fdb entries
                agent_fdb_entries = self._get_agent_fdb_entries(agent_host,
                                                                network_id,
                                                                segment)
                if agent_fdb_entries:
                    l2pop_rpc.L2populationAgentNotify.add_fdb_entries(
                        self.rpc_ctx, agent_fdb_entries, agent_host)

    def _get_other_fdb_entries_to_remove(self, context):
        port_context = context.current
        port_infos = self._get_port_infos(context, port_context)
        if not port_infos:
            return
        agent, agent_ip, segment, port_fdb_entries = port_infos

        agent_host = port_context['binding:host_id']
        network_id = port_context['network_id']

        session = db_api.get_session()
        net_port_count_on_agent = self.get_agent_net_port_up_count(session,
                                                                   agent_host,
                                                                   network_id)

        # if other agents hosts a port on this network, notify them
        net_port_count = self.get_net_ports_up(session, network_id).count()
        LOG.info(_("_get_other_fdb_entries_to_remove : "
                   "net_port_count %(netcount)s ;"
                   "net_port_count_on_agent : %(agentcount)s"),
                 {'netcount' : net_port_count, 'agentcount' : net_port_count_on_agent})
        if ((net_port_count - net_port_count_on_agent) > 0 ):
            other_fdb_entries = {network_id:
                             {'segment_id': segment['segmentation_id'],
                              'network_type': segment['network_type'],
                              'ports': {agent_ip: []}}}

            if((port_context['status'] == const.PORT_STATUS_ACTIVE and
                net_port_count_on_agent == 1) or
               (port_context['status'] != const.PORT_STATUS_ACTIVE and
                net_port_count_on_agent == 0)):
                # Agent is removing its last port in this network,
                # other agents needs to be notified to delete their flooding entry.
                other_fdb_entries[network_id]['ports'][agent_ip].append(
                    const.FLOODING_ENTRY)

            fdb_entries = self._get_port_fdb_entries(port_context)
            other_fdb_entries[network_id]['ports'][agent_ip] += fdb_entries
            return other_fdb_entries

    def _update_port_down(self, context):
        LOG.info(_("update_port_down"))
        fdb_entries = self._get_other_fdb_entries_to_remove(context)
        if fdb_entries:
            LOG.info(_("update_port_down : sending fdb_entries"))
            l2pop_rpc.L2populationAgentNotify.remove_fdb_entries(
                    self.rpc_ctx, fdb_entries)