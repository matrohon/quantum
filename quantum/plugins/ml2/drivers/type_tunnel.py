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
from abc import ABCMeta, abstractmethod

from quantum.common import exceptions as exc
from quantum.common import topics
from quantum.openstack.common import log

LOG = log.getLogger(__name__)

TUNNEL = 'tunnel'

TYPE_GRE = 'gre'

class TunnelTypeDriver(object):
    
    __metaclass__ = ABCMeta

    @abstractmethod
    def add_endpoint(self):
        pass
    
    @abstractmethod
    def get_endpoints(self):
        pass


class TunnelRpcCallbackMixin(object):

    def __init__(self, notifier, type_manager):
        self.notifier = notifier
        self.type_manager = type_manager

    def tunnel_sync(self, rpc_context, **kwargs):
        """Update new tunnel.

        Updates the database with the tunnel IP. All listening agents will also
        be notified about the new tunnel IP.
        """
        tunnel_ip = kwargs.get('tunnel_ip')
        # TODO(matrohon) tunnel_type should be a list of every tunnel_type that 
        # the agent supports. Should be implemented in bp/ml2-vxlan
        # tunnel_type = kwargs.get('tunnel_type')
        tunnel_type = TYPE_GRE
        driver = self.type_manager.drivers.get(tunnel_type)
        if driver:
            tunnel = driver.obj.add_endpoint(tunnel_ip)
            tunnels = driver.obj.get_endpoints()
            entry = dict()
            entry['tunnels'] = tunnels
            # Notify all other listening agents
            self.notifier.tunnel_update(rpc_context, tunnel.ip_address,
                                        tunnel.id)
            # Return the list of tunnels IP's to the agent
            return entry
        else:
            msg = _("network_type value '%s' not supported") % tunnel_type
            raise exc.InvalidInput(error_message=msg)


class TunnelAgentRpcApiMixin(object):
    
    def __init__(self, topic):
        
        self.topic_tunnel_update = topics.get_topic_name(topic,
                                                         TUNNEL,
                                                         topics.UPDATE)

    def tunnel_update(self, context, tunnel_ip, tunnel_id):
        self.fanout_cast(context,
                         self.make_msg('tunnel_update',
                                       tunnel_ip=tunnel_ip,
                                       tunnel_id=tunnel_id),
                         topic=self.topic_tunnel_update)