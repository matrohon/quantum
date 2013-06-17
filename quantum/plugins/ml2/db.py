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

from sqlalchemy.orm import exc
from sqlalchemy.sql import func

from quantum.db import api as db_api
from quantum.db import models_v2
from quantum.db import securitygroups_db as sg_db
from quantum import manager
from quantum.openstack.common import log
from quantum.openstack.common import uuidutils
from quantum.plugins.ml2 import driver_api as api
from quantum.plugins.ml2.drivers import type_gre
from quantum.plugins.ml2 import models


LOG = log.getLogger(__name__)


def initialize():
    db_api.configure_db()


def add_network_segment(session, network_id, segment):
    with session.begin(subtransactions=True):
        record = models.NetworkSegment(
            id=uuidutils.generate_uuid(),
            network_id=network_id,
            network_type=segment.get(api.NETWORK_TYPE),
            physical_network=segment.get(api.PHYSICAL_NETWORK),
            segmentation_id=segment.get(api.SEGMENTATION_ID)
        )
        session.add(record)
    LOG.info(_("Added segment %(id)s of type %(network_type)s for network"
               " %(network_id)s"),
             {'id': record.id,
              'network_type': record.network_type,
              'network_id': record.network_id})


def get_network_segments(session, network_id):
    with session.begin(subtransactions=True):
        records = (session.query(models.NetworkSegment).
                   filter_by(network_id=network_id))
        return [{api.NETWORK_TYPE: record.network_type,
                 api.PHYSICAL_NETWORK: record.physical_network,
                 api.SEGMENTATION_ID: record.segmentation_id}
                for record in records]


def get_port(session, port_id):
    """Get port record for update within transcation."""

    with session.begin(subtransactions=True):
        try:
            record = (session.query(models_v2.Port).
                      filter(models_v2.Port.id.startswith(port_id)).
                      one())
            return record
        except exc.NoResultFound:
            return
        except exc.MultipleResultsFound:
            LOG.error(_("Multiple ports have port_id starting with %s"),
                      port_id)
            return


def get_port_and_sgs(port_id):
    """Get port from database with security group info."""

    LOG.debug(_("get_port_and_sgs() called for port_id %s"), port_id)
    session = db_api.get_session()
    sg_binding_port = sg_db.SecurityGroupPortBinding.port_id

    with session.begin(subtransactions=True):
        query = session.query(models_v2.Port,
                              sg_db.SecurityGroupPortBinding.security_group_id)
        query = query.outerjoin(sg_db.SecurityGroupPortBinding,
                                models_v2.Port.id == sg_binding_port)
        query = query.filter(models_v2.Port.id.startswith(port_id))
        port_and_sgs = query.all()
        if not port_and_sgs:
            return
        port = port_and_sgs[0][0]
        plugin = manager.QuantumManager.get_plugin()
        port_dict = plugin._make_port_dict(port)
        port_dict['security_groups'] = [
            sg_id for port_, sg_id in port_and_sgs if sg_id]
        port_dict['security_group_rules'] = []
        port_dict['security_group_source_groups'] = []
        port_dict['fixed_ips'] = [ip['ip_address']
                                  for ip in port['fixed_ips']]
        return port_dict


def get_gre_endpoints():
    """Get every gre endpoints from database."""

    LOG.debug(_("get_gre_endpoints() called"))
    session = db_api.get_session()

    with session.begin(subtransactions=True):
        gre_endpoints = session.query(type_gre.GreEndpoints)
        return [{'id': gre_endpoint.id,
                 'ip_address': gre_endpoint.ip_address}
                for gre_endpoint in gre_endpoints]


def _generate_gre_endpoint_id(session):
    max_tunnel_id = session.query(
        func.max(type_gre.GreEndpoints.id)).scalar() or 0
    return max_tunnel_id + 1


def add_gre_endpoint(ip):
    LOG.debug(_("add_gre_endpoint() called for ip %s"), ip)
    session = db_api.get_session()
    with session.begin(subtransactions=True):
        try:
            gre_endpoint = (session.query(type_gre.GreEndpoints).
                            filter_by(ip_address=ip).
                            with_lockmode('update').one())
        except exc.NoResultFound:
            gre_endpoint_id = _generate_gre_endpoint_id(session)
            gre_endpoint = type_gre.GreEndpoints(ip, gre_endpoint_id)
            session.add(gre_endpoint)
            session.flush()
        return gre_endpoint
