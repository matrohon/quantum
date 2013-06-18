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

import sys

from oslo.config import cfg
import sqlalchemy as sa
from sqlalchemy.orm import exc as sa_exc
from sqlalchemy.sql import func

from quantum.common import exceptions as exc
from quantum.db import api as db_api
from quantum.db import model_base
from quantum.openstack.common import log
from quantum.plugins.ml2 import driver_api as api
from quantum.plugins.ml2.drivers import type_tunnel

LOG = log.getLogger(__name__)

gre_opts = [
    cfg.ListOpt('tunnel_id_ranges',
                default=[],
                help=_("Comma-separated list of <tun_min>:<tun_max> tuples "
                       "enumerating ranges of GRE tunnel IDs that are "
                       "available for tenant network allocation"))
]

cfg.CONF.register_opts(gre_opts, "ml2_type_gre")


class GreTypeDriver(api.TypeDriver,
                   type_tunnel.TunnelTypeDriver ):

    def get_type(self):
        return type_tunnel.TYPE_GRE

    def initialize(self):
        self.gre_id_ranges = []
        self._parse_gre_id_ranges()
        self._sync_gre_allocations()

    def validate_provider_segment(self, segment):
        physical_network = segment.get(api.PHYSICAL_NETWORK)
        if physical_network:
            msg = _("provider:physical_network specified for GRE "
                    "network")
            raise exc.InvalidInput(error_message=msg)

        segmentation_id = segment.get(api.SEGMENTATION_ID)
        if segmentation_id is None:
            msg = _("segmentation_id required for GRE provider network")
            raise exc.InvalidInput(error_message=msg)

    def reserve_provider_segment(self, session, segment):
        segmentation_id = segment.get(api.SEGMENTATION_ID)
        with session.begin(subtransactions=True):
            try:
                alloc = (session.query(GreAllocation).
                         filter_by(gre_id=segmentation_id).
                         with_lockmode('update').
                         one())
                if alloc.allocated:
                    raise exc.TunnelIdInUse(tunnel_id=segmentation_id)
                LOG.debug(_("Reserving specific gre tunnel %s from pool"),
                          segmentation_id)
                alloc.allocated = True
            except sa_exc.NoResultFound:
                LOG.debug(_("Reserving specific gre tunnel %s outside pool"),
                          segmentation_id)
                alloc = GreAllocation(segmentation_id)
                alloc.allocated = True
                session.add(alloc)

    def allocate_tenant_segment(self, session):
        with session.begin(subtransactions=True):
            alloc = (session.query(GreAllocation).
                     filter_by(allocated=False).
                     with_lockmode('update').
                     first())
            if alloc:
                LOG.debug(_("Allocating gre tunnel id  %(gre_id)s"),
                          {'gre_id': alloc.gre_id})
                alloc.allocated = True
                return {api.NETWORK_TYPE: type_tunnel.TYPE_GRE,
                        api.PHYSICAL_NETWORK: None,
                        api.SEGMENTATION_ID: alloc.gre_id}

    def release_segment(self, session, segment):
        gre_id = segment[api.SEGMENTATION_ID]
        with session.begin(subtransactions=True):
            try:
                alloc = (session.query(GreAllocation).
                         filter_by(gre_id=gre_id).
                         with_lockmode('update').
                         one())
                alloc.allocated = False
                inside = False
                for gre_id_range in self.gre_id_ranges:
                    if (gre_id >= gre_id_range[0]
                        and gre_id <= gre_id_range[1]):
                        inside = True
                        break
                if not inside:
                    session.delete(alloc)
                    LOG.debug(_("Releasing gre tunnel %s outside pool"),
                              gre_id)
                else:
                    LOG.debug(_("Releasing gre tunnel %s to pool"), gre_id)
            except sa_exc.NoResultFound:
                LOG.warning(_("gre_id %s not found"), gre_id)

    def _parse_gre_id_ranges(self):
        for entry in cfg.CONF.ml2_type_gre.tunnel_id_ranges:
            entry = entry.strip()
            try:
                tun_min, tun_max = entry.split(':')
                self.gre_id_ranges.append((int(tun_min), int(tun_max)))
            except ValueError as ex:
                LOG.error(_("Invalid tunnel ID range: "
                            "'%(range)s' - %(e)s. Agent terminated!"),
                          {'range': entry, 'e': ex})
                sys.exit(1)
        LOG.info(_("gre ID ranges: %s"), self.gre_id_ranges)

    def _sync_gre_allocations(self):
        """Synchronize gre_allocations table with configured tunnel ranges."""

        # determine current configured allocatable gres
        gre_ids = set()
        for gre_id_range in self.gre_id_ranges:
            tun_min, tun_max = gre_id_range
            if tun_max + 1 - tun_min > 1000000:
                LOG.error(_("Skipping unreasonable gre ID range "
                            "%(tun_min)s:%(tun_max)s"),
                          {'tun_min': tun_min, 'tun_max': tun_max})
            else:
                gre_ids |= set(xrange(tun_min, tun_max + 1))

        session = db_api.get_session()
        with session.begin(subtransactions=True):
            # remove from table unallocated tunnels not currently allocatable
            allocs = (session.query(GreAllocation).all())
            for alloc in allocs:
                try:
                    # see if tunnel is allocatable
                    gre_ids.remove(alloc.gre_id)
                except KeyError:
                    # it's not allocatable, so check if its allocated
                    if not alloc.allocated:
                        # it's not, so remove it from table
                        LOG.debug(_("Removing tunnel %s from pool"),
                                  alloc.gre_id)
                        session.delete(alloc)

            # add missing allocatable tunnels to table
            for gre_id in sorted(gre_ids):
                alloc = GreAllocation(gre_id)
                session.add(alloc)

    def get_endpoints(self):
        """Get every gre endpoints from database."""
    
        LOG.debug(_("get_gre_endpoints() called"))
        session = db_api.get_session()
    
        with session.begin(subtransactions=True):
            gre_endpoints = session.query(self.GreEndpoints)
            return [{'id': gre_endpoint.id,
                     'ip_address': gre_endpoint.ip_address}
                    for gre_endpoint in gre_endpoints]
    
    
    def _generate_gre_endpoint_id(self, session):
        max_tunnel_id = session.query(
            func.max(self.GreEndpoints.id)).scalar() or 0
        return max_tunnel_id + 1
    
    
    def add_endpoint(self, ip):
        LOG.debug(_("add_gre_endpoint() called for ip %s"), ip)
        session = db_api.get_session()
        with session.begin(subtransactions=True):
            try:
                gre_endpoint = (session.query(self.GreEndpoints).
                                filter_by(ip_address=ip).
                                with_lockmode('update').one())
            except sa_exc.NoResultFound:
                gre_endpoint_id = self._generate_gre_endpoint_id(session)
                gre_endpoint = self.GreEndpoints(ip, gre_endpoint_id)
                session.add(gre_endpoint)
                session.flush()
            return gre_endpoint


class GreAllocation(model_base.BASEV2):

    __tablename__ = 'ml2_gre_allocations'

    gre_id = sa.Column(sa.Integer, nullable=False, primary_key=True,
                       autoincrement=False)
    allocated = sa.Column(sa.Boolean, nullable=False)

    def __init__(self, gre_id):
        self.gre_id = gre_id
        self.allocated = False


class GreEndpoints(model_base.BASEV2):
    """Represents tunnel endpoint in RPC mode."""
    __tablename__ = 'ml2_gre_endpoints'

    ip_address = sa.Column(sa.String(64), primary_key=True)
    id = sa.Column(sa.Integer, nullable=False)

    def __init__(self, ip_address, id):
        self.ip_address = ip_address
        self.id = id

    def __repr__(self):
        return "<TunnelEndpoint(%s,%s)>" % (self.ip_address, self.id)
