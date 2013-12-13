#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright 2012 The Plaso Project Authors.
# Please see the AUTHORS file for details on individual authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Parser for Windows NT Registry (REGF) files."""
import logging

from plaso.lib import errors
from plaso.lib import parser
from plaso.lib import win_registry_interface
from plaso.winreg import cache
from plaso.winreg import winregistry


class WinRegistryParser(parser.PlasoParser):
  """Parses Windows NT Registry (REGF) files."""

  NAME = 'regf'

  # List of types registry types and required keys to identify each of these
  # types.
  REG_TYPES = {
      'NTUSER': ('\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer',),
      'SOFTWARE': ('\\Microsoft\\Windows\\CurrentVersion\\App Paths',),
      'SECURITY': ('\\Policy\\PolAdtEv',),
      'SYSTEM': ('\\Select',),
      'SAM': ('\\SAM\\Domains\\Account\\Users',),
      'UNKNOWN': (),
  }

  def __init__(self, pre_obj, config=None):
    """Initializes the parser.

    Args:
      pre_obj: pre-parsing object.
      config: A configuration object.
    """
    super(WinRegistryParser, self).__init__(pre_obj, config)
    self._codepage = getattr(self._pre_obj, 'codepage', 'cp1252')
    self._plugins = win_registry_interface.GetRegistryPlugins()

  def Scan(self, file_object):
    pass

  def Parse(self, file_object):
    """Return a generator for events extracted from registry files."""
    # TODO: Remove this magic reads when the classifier has been
    # implemented, until then we need to make sure we are dealing with
    # a registry file before proceeding.
    magic = 'regf'
    data = file_object.read(len(magic))

    registry = winregistry.WinRegistry(
        winregistry.WinRegistry.BACKEND_PYREGF)

    if data != magic:
      raise errors.UnableToParseFile(u'File %s not a %s. (wrong magic)' % (
          file_object.name, self.parser_name))

    # Determine type, find all parsers
    try:
      winreg_file = registry.OpenFile(file_object, codepage=self._codepage)
    except IOError as e:
      raise errors.UnableToParseFile(
          u'[%s] Unable to parse file %s: %s' % (
              self.parser_name, file_object.name, e))

    # Detect registry type.
    registry_type = 'UNKNOWN'
    for reg_type in self.REG_TYPES:
      if reg_type == 'UNKNOWN':
        continue

      # Check if all the known keys for a certain Registry file exist.
      known_keys_found = True
      for known_key_path in self.REG_TYPES[reg_type]:
        if not winreg_file.GetKeyByPath(known_key_path):
          known_keys_found = False
          break

      if known_keys_found:
        registry_type = reg_type
        break

    self._registry_type = registry_type
    logging.debug(u'Registry file %s detected as <%s>', file_object.name,
                  registry_type)

    registry_cache = cache.WinRegistryCache(winreg_file, registry_type)
    registry_cache.BuildCache()

    plugins = {}
    counter = 0
    for weight in self._plugins.GetWeights():
      plist = self._plugins.GetWeightPlugins(weight, registry_type)
      plugins[weight] = []
      for plugin in plist:
        plugins[weight].append(plugin(
            winreg_file, self._pre_obj, registry_cache))
        counter += 1

    logging.debug('Number of plugins for this registry file: %d', counter)
    # Recurse through keys and apply action.
    # Order:
    #   Compare against key centric plugins for this type of registry.
    #   Compare against key centric plugin that works against any registry.
    #   Compare against value centric plugins for this type of registry.
    #   Compare against value centric plugins that works against any registry.
    root_key = winreg_file.GetKeyByPath('\\')
    for key in self._RecurseKey(root_key):
      parsed = False
      for weight in plugins:
        if parsed:
          break
        for plugin in plugins[weight]:
          call_back = plugin.Process(key)
          if call_back:
            parsed = True
            for event_object in self.GetEvents(call_back, key):
              event_object.plugin = plugin.plugin_name
              yield event_object
            break

  def _RecurseKey(self, key):
    """A generator that takes a registry key and yields every subkey of it."""
    # In the case of a Registry file not having a root key we will not be able
    # to traverse the Registry, in which case we need to return here.
    if not key:
      return

    yield key

    for subkey in key.GetSubkeys():
      for a in self._RecurseKey(subkey):
        yield a

  def GetEvents(self, call_back, key):
    """Return all events generated by a registry plugin."""
    for event_object in call_back:
      event_object.offset = getattr(event_object, 'offset', key.offset)
      event_object.registry_type = self._registry_type
      if getattr(call_back, 'URLS', None):
        event_object.url = ' - '.join(call_back.URLS)

      yield event_object
