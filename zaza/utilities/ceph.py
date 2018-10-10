"""Module containing Ceph related utilities."""

import logging

import zaza.utilities.openstack as openstack_utils
import zaza.model as zaza_model


def get_ceph_osd_id_cmd(osd_id):
    """Get ceph OSD command.

    Produce a shell command that will return a ceph-osd id.
    :returns: Command for ceph OSD.
    :rtype: string
    """
    return ("`initctl list | grep 'ceph-osd ' | "
            "awk 'NR=={} {{ print $2 }}' | "
            "grep -o '[0-9]*'`".format(osd_id + 1))


def get_expected_pools(radosgw=False):
        """Get expected ceph pools.

        Return a list of expected ceph pools in a ceph + cinder + glance
        test scenario, based on OpenStack release and whether ceph radosgw
        is flagged as present or not.
        :param radosgw: If radosgw is used or not
        :type radosgw: boolean
        :returns: List of pools that are expected
        :rtype: list
        """
        current_release = openstack_utils.get_os_release()
        trusty_icehouse = openstack_utils.get_os_release('trusty_icehouse')
        trusty_kilo = openstack_utils.get_os_release('trusty_kilo')
        zesty_ocata = openstack_utils.get_os_release('zesty_ocata')
        if current_release == trusty_icehouse:
            # Icehouse
            pools = [
                'data',
                'metadata',
                'rbd',
                'cinder-ceph',
                'glance'
            ]
        elif (trusty_kilo <= current_release <= zesty_ocata):
            # Kilo through Ocata
            pools = [
                'rbd',
                'cinder-ceph',
                'glance'
            ]
        else:
            # Pike and later
            pools = [
                'cinder-ceph',
                'glance'
            ]

        if radosgw:
            pools.extend([
                '.rgw.root',
                '.rgw.control',
                '.rgw',
                '.rgw.gc',
                '.users.uid'
            ])

        return pools


def get_ceph_pools(unit_name):
        """Get ceph pools.

        Return a dict of ceph pools from a single ceph unit, with
        pool name as keys, pool id as vals.
        :param unit_name: Name of the unit to get the pools on
        :type unit_name: string
        :returns: Dict of ceph pools
        :rtype: dict
        """
        pools = {}
        cmd = 'sudo ceph osd lspools'
        result = zaza_model.run_on_unit(unit_name, cmd)
        output = result.get('Stdout').strip()
        code = int(result.get('Code'))
        if code != 0:
            raise zaza_model.CommandRunFailed(cmd, result)

        # Example output: 0 data,1 metadata,2 rbd,3 cinder,4 glance,
        for pool in str(output).split(','):
            pool_id_name = pool.split(' ')
            if len(pool_id_name) == 2:
                pool_id = pool_id_name[0]
                pool_name = pool_id_name[1]
                pools[pool_name] = int(pool_id)

        logging.debug('Pools on {}: {}'.format(unit_name, pools))
        return pools
