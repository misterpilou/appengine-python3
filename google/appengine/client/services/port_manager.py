#!/usr/bin/env python3
#
# Copyright 2007 Google Inc.
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
#
"""A helper file with a helper class for opening ports."""

import logging

from google.appengine.client.services import vme_constants
from google.appengine.client.services import vme_errors

# These ports are used by our code or critical system daemons.
RESERVED_HOST_PORTS = [22,  # SSH
                       5000,  # Docker registry
                       vme_constants.DEFAULT_SERVING_PORT,  # HTTP server
                       10000,  # For unlocking?
                       10001,  # Nanny stubby proxy endpoint
                      ]
# We allow users to forward traffic to our HTTP server internally.
RESERVED_DOCKER_PORTS = [22,  # SSH
                         5000,  # Docker registry
                         10001,  # Nanny stubby proxy endpoint
                        ]

DEFAULT_CONTAINER_PORT = vme_constants.DEFAULT_SERVING_PORT
VM_PORT_FOR_CONTAINER = vme_constants.DEFAULT_SERVING_PORT


class InconsistentPortConfigurationError(vme_errors.PermanentAppError):
  """The port is already in use."""
  pass


class IllegalPortConfigurationError(vme_errors.PermanentAppError):
  """Raised if the port configuration is illegal."""
  pass


def CreatePortManager(forwarded_ports, container_port):
  """Construct a PortManager object with port forwarding configured.

  Args:
    forwarded_ports: A string containing desired mappings from VM host ports
        to docker container ports.
    container_port: An integer port number for the container port.

  Returns:
    The PortManager instance.
  """
  port_manager_obj = PortManager(container_port)
  ports_list = forwarded_ports if forwarded_ports else []
  logging.debug('setting forwarded ports %s', ports_list)
  port_manager_obj.Add(ports_list, 'forwarded')
  return port_manager_obj


class PortManager(object):
  """A helper class for VmManager to deal with port mappings."""

  def __init__(self, container_port=DEFAULT_CONTAINER_PORT):
    self.used_host_ports = {}
    self._port_mappings = {}
    self.container_port = container_port

  def Add(self, ports, kind):
    """Load port configurations and adds them to an internal dict.

    Args:
      ports: A list of strings or a CSV representing port forwarding.
      kind: what kind of port configuration this is, only used for error
        reporting.

    Raises:
      InconsistentPortConfigurationError: If a port is configured to do
        two different conflicting things.
      IllegalPortConfigurationError: If the port is out of range or
        is not a number.

    Returns:
      A dictionary with forwarding rules as external_port => local_port.
    """
    if isinstance(ports, str):
      # split a csv
      ports = [port.strip() for port in ports.split(',')]
    port_translations = {}
    for port in ports:
      try:
        if ':' in port:
          host_port, docker_port = (int(p.strip()) for p in port.split(':'))
          port_translations[host_port] = docker_port
        else:
          host_port = int(port)
          docker_port = host_port
          port_translations[host_port] = host_port
        if (host_port in self.used_host_ports and
            self.used_host_ports[host_port] != docker_port):
          raise InconsistentPortConfigurationError(
              'Configuration conflict, port %d configured to forward '
              'differently.' % host_port)
        self.used_host_ports[host_port] = docker_port
        if (host_port < 1 or host_port > 65535 or
            docker_port < 1 or docker_port > 65535):
          raise IllegalPortConfigurationError(
              'Failed to load %s port configuration: invalid port %s'
              % (kind, port))
        if docker_port < 1024:
          raise IllegalPortConfigurationError(
              'Cannot listen on port %d as it is priviliged, use a forwarding '
              'port.' % docker_port)
        if docker_port in RESERVED_DOCKER_PORTS:
          raise IllegalPortConfigurationError(
              'Cannot use port %d as it is reserved on the VM.'
              % docker_port)
        if host_port in RESERVED_HOST_PORTS:
          raise IllegalPortConfigurationError(
              'Cannot use port %d as it is reserved on the VM.'
              % host_port)
      except ValueError as e:
        logging.exception('Bad port description')
        raise IllegalPortConfigurationError(
            'Failed to load %s port configuration: "%s" error: "%s"'
            % (kind, port, e))
    # At this point we know they are not destructive.
    self._port_mappings.update(port_translations)
    return port_translations

  def GetAllMappedPorts(self):
    """Returns all mapped ports.

    Returns:
      A dict of port mappings {host: docker}
    """
    if not self._port_mappings:
      return {}
    else:
      return self._port_mappings

  # TODO: look into moving this into a DockerManager.
  def _BuildDockerPublishArgumentString(self):
    """Generates a string of ports to expose to the Docker container.

    Returns:
      A string with --publish=host:docker pairs.
    """
    port_map = self.GetAllMappedPorts()
    # Map container port to port 8080 on the VM (default to 8080 if not set)
    port_map[VM_PORT_FOR_CONTAINER] = int(self.container_port)
    result = ''
    for k, v in port_map.items():
      result += '--publish=%d:%s ' % (k, v)
    return result

  def GetReplicaPoolParameters(self):
    """Returns the contribution to the replica template."""
    publish_ports = self._BuildDockerPublishArgumentString()
    maps = {
        'template': {
            'vmParams': {
                'metadata': {
                    'items': [
                        {'key': 'gae_publish_ports', 'value': publish_ports}
                        ]
                    }
                }
            }
        }
    return maps
