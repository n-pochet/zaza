# Copyright 2018 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Collection of functions that did not fit anywhere else."""

import logging
import os
import subprocess
import yaml

from zaza import model
from zaza.utilities import juju as juju_utils


def dict_to_yaml(dict_data):
    """Return YAML from dictionary.

    :param dict_data: Dictionary data
    :type dict_data: dict
    :returns: YAML dump
    :rtype: string
    """
    return yaml.dump(dict_data, default_flow_style=False)


def get_network_config(net_topology, ignore_env_vars=False,
                       net_topology_file="network.yaml"):
    """Get network info from environment.

    Get network info from network.yaml, override the values if specific
    environment variables are set for the undercloud.

    This function may be used when running network configuration from CLI to
    pass in network configuration settings from a YAML file.

    :param net_topology: Network topology name from network.yaml
    :type net_topology: string
    :param ignore_env_vars: Ignore enviroment variables or not
    :type ignore_env_vars: boolean
    :returns: Dictionary of network configuration
    :rtype: dict
    """
    if os.path.exists(net_topology_file):
        net_info = get_yaml_config(net_topology_file)[net_topology]
    else:
        raise Exception("Network topology file: {} not found."
                        .format(net_topology_file))

    if not ignore_env_vars:
        logging.info("Consuming network environment variables as overrides "
                     "for the undercloud.")
        net_info.update(get_undercloud_env_vars())

    logging.info("Network info: {}".format(dict_to_yaml(net_info)))
    return net_info


def get_pkg_version(application, pkg):
    """Return package version.

    :param application: Application name
    :type application: string
    :param pkg: Package name
    :type pkg: string
    :returns: List of package version
    :rtype: list
    """
    versions = []
    units = model.get_units(application)
    for unit in units:
        cmd = 'dpkg -l | grep {}'.format(pkg)
        out = juju_utils.remote_run(unit.entity_id, cmd)
        versions.append(out.split('\n')[0].split()[2])
    if len(set(versions)) != 1:
        raise Exception('Unexpected output from pkg version check')
    return versions[0]


def get_undercloud_env_vars():
    """Get environment specific undercloud network configuration settings.

    Get environment specific undercloud network configuration settings from
    environment variables.

    For each testing substrate, specific undercloud network configuration
    settings should be exported into the environment to enable testing on that
    substrate.

    Note: *Overcloud* settings should be declared by the test caller and should
    not be overridden here.

    Return a dictionary compatible with zaza.configure.network functions'
    expected key structure.

    Example exported environment variables:
    export default_gateway="172.17.107.1"
    export external_net_cidr="172.17.107.0/24"
    export external_dns="10.5.0.2"
    export start_floating_ip="172.17.107.200"
    export end_floating_ip="172.17.107.249"

    Example o-c-t & uosci non-standard environment variables:
    export NET_ID="a705dd0f-5571-4818-8c30-4132cc494668"
    export GATEWAY="172.17.107.1"
    export CIDR_EXT="172.17.107.0/24"
    export NAMESERVER="10.5.0.2"
    export FIP_RANGE="172.17.107.200:172.17.107.249"

    :returns: Network environment variables
    :rtype: dict
    """
    # Handle backward compatibile OSCI enviornment variables
    _vars = {}
    _vars['net_id'] = os.environ.get('NET_ID')
    _vars['external_dns'] = os.environ.get('NAMESERVER')
    _vars['default_gateway'] = os.environ.get('GATEWAY')
    _vars['external_net_cidr'] = os.environ.get('CIDR_EXT')

    # Take FIP_RANGE and create start and end floating ips
    _fip_range = os.environ.get('FIP_RANGE')
    if _fip_range and ':' in _fip_range:
        _vars['start_floating_ip'] = os.environ.get('FIP_RANGE').split(':')[0]
        _vars['end_floating_ip'] = os.environ.get('FIP_RANGE').split(':')[1]

    # Env var naming consistent with zaza.configure.network functions takes
    # priority. Override backward compatible settings.
    _keys = ['default_gateway',
             'start_floating_ip',
             'end_floating_ip',
             'external_dns',
             'external_net_cidr']
    for _key in _keys:
        _val = os.environ.get(_key)
        if _val:
            _vars[_key] = _val

    # Remove keys and items with a None value
    for k, v in list(_vars.items()):
        if not v:
            del _vars[k]

    return _vars


def get_yaml_config(config_file):
    """Return configuration from YAML file.

    :param config_file: Configuration file name
    :type config_file: string
    :returns: Dictionary of configuration
    :rtype: dict
    """
    # Note in its original form get_mojo_config it would do a search pattern
    # through mojo stage directories. This version assumes the yaml file is in
    # the pwd.
    logging.info('Using config %s' % (config_file))
    return yaml.load(open(config_file, 'r').read())


def series_upgrade_application(application, pause_non_leader_primary=True,
                               pause_non_leader_subordinate=True,
                               from_series="trusty", to_series="xenial",
                               origin='openstack-origin',
                               completed_machines=[],
                               files=None, workaround_script=None):
    """Series upgrade application.

    Wrap all the functionality to handle series upgrade for a given
    application. Including pausing non-leader units.

    :param application: Name of application to upgrade series
    :type application: str
    :param pause_non_leader_primary: Whether the non-leader applications should
                                     be paused
    :type pause_non_leader_primary: bool
    :param pause_non_leader_subordinate: Whether the non-leader subordinate
                                         hacluster applications should be
                                         paused
    :type pause_non_leader_subordinate: bool
    :param from_series: The series from which to upgrade
    :type from_series: str
    :param to_series: The series to which to upgrade
    :type to_series: str
    :param origin: The configuration setting variable name for changing origin
                   source. (openstack-origin or source)
    :type origin: str
    :param completed_machines: List of completed machines which do no longer
                               require series upgrade.
    :type files: list
    :param files: Workaround files to scp to unit under upgrade
    :type files: list
    :param workaround_script: Workaround script to run during series upgrade
    :type workaround_script: str
    :returns: None
    :rtype: None
    """
    status = model.get_status().applications[application]

    # For some applications (percona-cluster) the leader unit must upgrade
    # first. For API applications the non-leader haclusters must be paused
    # before upgrade. Finally, for some applications this is aribtrary but
    # generalized.
    leader = None
    non_leaders = []
    for unit in status["units"]:
        if status["units"][unit].get("leader"):
            leader = unit
        else:
            non_leaders.append(unit)

    # Pause the non-leaders
    for unit in non_leaders:
        if pause_non_leader_subordinate:
            if status["units"][unit].get("subordinates"):
                for subordinate in status["units"][unit]["subordinates"]:
                    logging.info("Pausing {}".format(subordinate))
                    model.run_action(subordinate, "pause", action_params={})
        if pause_non_leader_primary:
            logging.info("Pausing {}".format(unit))
            model.run_action(unit, "pause", action_params={})

    machine = status["units"][leader]["machine"]
    # Series upgrade the leader
    logging.info("Series upgrade leader: {}".format(leader))
    if machine not in completed_machines:
        series_upgrade(leader, machine,
                       from_series=from_series, to_series=to_series,
                       origin=origin, workaround_script=workaround_script,
                       files=files)
        completed_machines.append(machine)
    else:
        logging.info("Skipping unit: {}. Machine: {} already upgraded."
                     "But setting origin on the application {}"
                     .format(unit, machine, application))
        logging.info("Set origin on {}".format(application))
        set_origin(application, origin)
        model.block_until_all_units_idle()

    # Series upgrade the non-leaders
    for unit in non_leaders:
        machine = status["units"][unit]["machine"]
        if machine not in completed_machines:
            logging.info("Series upgrade non-leader unit: {}"
                         .format(unit))
            series_upgrade(unit, machine,
                           from_series=from_series, to_series=to_series,
                           origin=origin, workaround_script=workaround_script,
                           files=files)
            completed_machines.append(machine)
        else:
            logging.info("Skipping unit: {}. Machine: {} already upgraded. "
                         "But setting origin on the application {}"
                         .format(unit, machine, application))
            logging.info("Set origin on {}".format(application))
            set_origin(application, origin)
            model.block_until_all_units_idle()


def series_upgrade(unit_name, machine_num,
                   from_series="trusty", to_series="xenial",
                   origin='openstack-origin',
                   files=None, workaround_script=None):
    """Perform series upgrade on a unit.

    :param unit_name: Unit Name
    :type unit_name: str
    :param machine_num: Machine number
    :type machine_num: str
    :param from_series: The series from which to upgrade
    :type from_series: str
    :param to_series: The series to which to upgrade
    :type to_series: str
    :param origin: The configuration setting variable name for changing origin
                   source. (openstack-origin or source)
    :type origin: str
    :param files: Workaround files to scp to unit under upgrade
    :type files: list
    :param workaround_script: Workaround script to run during series upgrade
    :type workaround_script: str
    :returns: None
    :rtype: None
    """
    logging.info("Series upgrade {}".format(unit_name))
    application = unit_name.split('/')[0]
    set_dpkg_non_interactive_on_unit(unit_name)
    logging.info("Prepare series upgrade on {}".format(machine_num))
    model.prepare_series_upgrade(machine_num, to_series=to_series)
    logging.info("Watiing for workload status 'blocked' on {}"
                 .format(unit_name))
    model.block_until_unit_wl_status(unit_name, "blocked")
    logging.info("Watiing for model idleness")
    model.block_until_all_units_idle()
    wrap_do_release_upgrade(unit_name, from_series=from_series,
                            to_series=to_series, files=files,
                            workaround_script=workaround_script)
    logging.info("Reboot {}".format(unit_name))
    reboot(unit_name)
    logging.info("Watiing for workload status 'blocked' on {}"
                 .format(unit_name))
    model.block_until_unit_wl_status(unit_name, "blocked")
    logging.info("Watiing for model idleness")
    model.block_until_all_units_idle()
    logging.info("Set origin on {}".format(application))
    set_origin(application, origin)
    model.block_until_all_units_idle()
    logging.info("Complete series upgrade on {}".format(machine_num))
    model.complete_series_upgrade(machine_num)
    model.block_until_all_units_idle()
    logging.info("Watiing for workload status 'active' on {}"
                 .format(unit_name))
    model.block_until_unit_wl_status(unit_name, "active")
    model.block_until_all_units_idle()
    # This step may be performed by juju in the future
    logging.info("Set series on {} to {}".format(application, to_series))
    model.set_series(application, to_series)


def set_origin(application, origin='openstack-origin', pocket='distro'):
    """Set the configuration option for origin source.

    :param application: Name of application to upgrade series
    :type application: str
    :param origin: The configuration setting variable name for changing origin
                   source. (openstack-origin or source)
    :type origin: str
    :param pocket: Origin source cloud pocket.
                   i.e. 'distro' or 'cloud:xenial-newton'
    :type pocket: str
    :returns: None
    :rtype: None
    """
    logging.info("Set origin on {} to {}".format(application, origin))
    model.set_application_config(application, {origin: pocket})


def wrap_do_release_upgrade(unit_name, from_series="trusty",
                            to_series="xenial",
                            files=None, workaround_script=None):
    """Wrap do release upgrade.

    In a production environment this step would be run administratively.
    For testing purposes we need this automated.

    :param unit_name: Unit Name
    :type unit_name: str
    :param from_series: The series from which to upgrade
    :type from_series: str
    :param to_series: The series to which to upgrade
    :type to_series: str
    :param files: Workaround files to scp to unit under upgrade
    :type files: list
    :param workaround_script: Workaround script to run during series upgrade
    :type workaround_script: str
    :returns: None
    :rtype: None
    """
    # Pre upgrade hacks
    # There are a few necessary hacks to accomplish an automated upgrade
    # to overcome some packaging bugs.
    # Copy scripts
    if files:
        logging.info("SCP files")
        for _file in files:
            logging.info("SCP {}".format(_file))
            model.scp_to_unit(unit_name, _file, os.path.basename(_file))

    # Run Script
    if workaround_script:
        logging.info("Running workaround script")
        run_via_ssh(unit_name, workaround_script)

    # Actually do the do_release_upgrade
    do_release_upgrade(unit_name)


def run_via_ssh(unit_name, cmd):
    """Run command on unit via ssh.

    For executing commands on units when the juju agent is down.

    :param unit_name: Unit Name
    :param cmd: Command to execute on remote unit
    :type cmd: str
    :returns: None
    :rtype: None
    """
    if "sudo" not in cmd:
        cmd = "sudo {}".format(cmd)
    cmd = ['juju', 'ssh', unit_name, cmd]
    logging.info("Running {} on {}".format(cmd, unit_name))
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        logging.warn("Failed command {} on {}".format(cmd, unit_name))
        logging.warn(e)


def do_release_upgrade(unit_name):
    """Run do-release-upgrade noninteractive.

    :param unit_name: Unit Name
    :type unit_name: str
    :returns: None
    :rtype: None
    """
    logging.info('Upgrading ' + unit_name)
    # NOTE: It is necessary to run this via juju ssh rather than juju run due
    # to timeout restrictions and error handling.
    cmd = ['juju', 'ssh', unit_name, 'sudo', 'DEBIAN_FRONTEND=noninteractive',
           'do-release-upgrade', '-f', 'DistUpgradeViewNonInteractive']
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        logging.warn("Failed do-release-upgrade for {}".format(unit_name))
        logging.warn(e)


def reboot(unit_name):
    """Reboot unit.

    :param unit_name: Unit Name
    :type unit_name: str
    :returns: None
    :rtype: None
    """
    # NOTE: When used with series upgrade the agent will be down.
    # Even juju run will not work
    cmd = ['juju', 'ssh', unit_name, 'sudo', 'reboot', '&&', 'exit']
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        logging.info(e)
        pass


def set_dpkg_non_interactive_on_unit(
        unit_name, apt_conf_d="/etc/apt/apt.conf.d/50unattended-upgrades"):
    """Set dpkg options on unit.

    :param unit_name: Unit Name
    :type unit_name: str
    :param apt_conf_d: Apt.conf file to update
    :type apt_conf_d: str
    """
    DPKG_NON_INTERACTIVE = 'DPkg::options { "--force-confdef"; };'
    # Check if the option exists. If not, add it to the apt.conf.d file
    cmd = ("grep '{option}' {file_name} || echo '{option}' >> {file_name}"
           .format(option=DPKG_NON_INTERACTIVE, file_name=apt_conf_d))
    model.run_on_unit(unit_name, cmd)
