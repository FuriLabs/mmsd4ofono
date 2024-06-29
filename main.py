#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

import asyncio
from os import environ
from argparse import ArgumentParser
from os.path import expanduser
from os import makedirs

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import DBusError, BusType, Variant

from mmsd import OfonoMMSServiceInterface, OfonoMMSModemManagerInterface, OfonoMMSMessageInterface, OfonoPushNotification, Ofono, DBus
from mmsd.utils import async_locked
from mmsd.logging import mmsd_print

from mmsdecoder.message import MMSMessage

has_bus = False

class OfonoMMSManagerInterface(ServiceInterface):
    def __init__(self, loop, system_bus, session_bus, verbose=False):
        super().__init__('org.ofono.mms.Manager')
        mmsd_print("Initializing Manager interface", verbose)
        self.loop = loop
        self.system_bus = system_bus
        self.session_bus = session_bus
        self.verbose = verbose
        self.ofono_client = Ofono(system_bus)
        self.dbus_client = DBus(system_bus)
        self.ofono_mms_interfaces = []
        self.ofono_mms_objects = []
        self.ofono_proxy = []
        self.ofono_props = {}
        self.ofono_interfaces = {}
        self.ofono_interface_props = {}
        self.modem_added_block = False
        self.ofono_mms_service_interface = False
        self.ofono_mms_modemmanager_interface = False
        self.ofono_push_notification_interface = False
        self.home = expanduser("~")
        self.mms_dir = expanduser("~/.mms/modemmanager")
        makedirs(self.mms_dir, exist_ok=True)
        self.loop.create_task(self.check_ofono_presence())
        self.props = {
            'services': [
                ['/org/ofono/mms/modemmanager', {'Identity': Variant('s', 'modemmanager')}]
            ]
        }

    @method()
    async def GetServices(self) -> 'a(oa{sv})':
        mmsd_print("Getting services", self.verbose)

        if not self.ofono_mms_service_interface:
            try:
                await self.find_ofono_modems()
            except Exception as e:
                mmsd_print(f"Failed to get services: {e}", self.verbose)

        self.ServiceAdded(self.props['services'][0][0], self.props['services'][0][1])

        return self.props['services']

    @signal()
    def ServiceAdded(self, path: 'o', properties: 'a{sv}') -> 'oa{sv}':
        mmsd_print(f"Service added emitted with path {path} and properties {properties}", self.verbose)
        return [path, properties]

    @signal()
    def ServiceRemoved(self, path: 'o') -> 'o':
        mmsd_print(f"Service removed emitted with path {path}", self.verbose)
        return path

    async def check_ofono_presence(self):
        mmsd_print("Checking ofono presence", self.verbose)

        dbus_iface = self.dbus_client["dbus"]["/org/freedesktop/DBus"]["org.freedesktop.DBus"]
        dbus_iface.on_name_owner_changed(self.dbus_name_owner_changed)
        has_ofono = await dbus_iface.call_name_has_owner("org.ofono")
        if has_ofono:
            self.ofono_added()
        else:
            self.ofono_removed()

    def ofono_added(self):
        mmsd_print("oFono added", self.verbose)

        self.ofono_manager_interface = self.ofono_client["ofono"]["/"]["org.ofono.Manager"]
        self.ofono_manager_interface.on_modem_added(self.ofono_modem_added)
        self.ofono_manager_interface.on_modem_removed(self.ofono_modem_removed)
        self.loop.create_task(self.find_ofono_modems())

    def ofono_removed(self):
        mmsd_print("oFono removed", self.verbose)
        self.ofono_manager_interface = None

    @async_locked
    async def find_ofono_modems(self):
        mmsd_print("Finding oFono modems", self.verbose)

        global has_bus

        for ofono_mms_object in self.ofono_mms_objects:
            self.session_bus.unexport(ofono_mms_object)

        self.ofono_mms_objects = []
        self.ofono_mms_interfaces = []

        if not self.ofono_manager_interface:
            mmsd_print("oFono manager interface is empty, skipping", self.verbose)
            return

        self.ofono_modem_list = False
        self.modem_added_block = True
        while not self.ofono_modem_list:
            try:
                if self.ofono_manager_interface is None:
                    mmsd_print("oFono manager interface is not initialized properly. skipping", self.verbose)
                    return

                modems = await self.ofono_manager_interface.call_get_modems()

                for modem in modems:
                    mmsd_print(f"Modems available in oFono: {modem[0]}", self.verbose)

                self.ofono_modem_list = [
                    x
                    for x in modems
                    if x[0].startswith("/ril_") # FIXME
                ]

                if not self.ofono_modem_list:
                    mmsd_print("No modems available, retrying", self.verbose)
                    await asyncio.sleep(2)
                    continue
            except DBusError as e:
                mmsd_print(f"Failed to get the current modem: {e}", self.verbose)
                self.ofono_modem_list = False

        for modem in self.ofono_modem_list:
            try:
                self.ofono_sim_manager = self.ofono_client["ofono_modem"][modem[0]]['org.ofono.SimManager']

                mmsd_print(f"modem is {modem[0]}", self.verbose)

                await self.export_new_modem(modem[0], modem[1])
            except DBusError as e:
                print(f"Error interacting with modem {modem[0]}: {e}")
                continue

        if not has_bus and len(self.ofono_mms_objects) != 0:
            await self.session_bus.request_name('org.ofono.mms')
            has_bus = True

    def dbus_name_owner_changed(self, name, old_owner, new_owner):
        if name == "org.ofono":
            mmsd_print(f"oFono name owner changed, name: {name}, old owner: {old_owner}, new owner: {new_owner}", self.verbose)
            if new_owner == "":
                self.ofono_removed()
            elif old_owner == "":
                self.ofono_added()

    def ofono_modem_added(self, path, mprops):
        mmsd_print(f"oFono modem added at path {path} and properties {mprops}", self.verbose)

        if self.modem_added_block:
            mmsd_print("oFono modem block is on, skipping", self.verbose)
            return

        try:
            self.loop.create_task(self.export_new_modem(path, mprops))
        except Exception as e:
            mmsd_print(f"Failed to create task for modem {path}: {e}", self.verbose)

    async def export_new_modem(self, path, mprops):
        mmsd_print(f"Processing modem {path} with properties {mprops}", self.verbose)

        self.ofono_props = mprops
        self.ofono_proxy = self.ofono_client["ofono_modem"][path]
        self.ofono_proxy['org.ofono.Modem'].on_property_changed(self.ofono_changed)
        await self.init_ofono_interfaces()

        self.ofono_mms_service_interface = OfonoMMSServiceInterface(self.ofono_client, self.ofono_props, self.ofono_interfaces, self.ofono_interface_props, self.mms_dir, self.verbose)
        self.session_bus.export('/org/ofono/mms/modemmanager', self.ofono_mms_service_interface)
        self.ofono_mms_service_interface.set_props()
        self.ofono_mms_interfaces.append(self.ofono_mms_service_interface)
        self.ofono_mms_objects.append('/org/ofono/mms/modemmanager')

        self.ofono_mms_modemmanager_interface = OfonoMMSModemManagerInterface(self.ofono_client, self.ofono_props, self.ofono_interfaces, self.ofono_interface_props, self.mms_dir, self.verbose)
        self.session_bus.export('/org/ofono/mms', self.ofono_mms_modemmanager_interface)
        await self.ofono_mms_modemmanager_interface.set_props()
        self.ofono_mms_interfaces.append(self.ofono_mms_modemmanager_interface)
        self.ofono_mms_objects.append('/org/ofono/mms')

        self.ofono_push_notification_interface = OfonoPushNotification(self.system_bus, self.ofono_client, self.ofono_props, self.ofono_interfaces, self.ofono_interface_props, self.mms_dir, self.export_mms_message, self.verbose)
        await self.ofono_push_notification_interface.RegisterAgent('/mmsd')
        self.ofono_mms_interfaces.append(self.ofono_push_notification_interface)
        self.ofono_mms_objects.append('/mmsd')
        self.ofono_push_notification_interface.export_old_messages()

        self.modem_added_block = False

        if 'org.ofono.ConnectionManager' in self.ofono_interfaces:
            contexts = await self.ofono_interfaces['org.ofono.ConnectionManager'].call_get_contexts()
            for ctx in contexts:
                name = ctx[1].get('Type', Variant('s', '')).value
                if name.lower() == "mms":
                    ctx_path = ctx[0]
                    ctx_interface = self.ofono_client["ofono_context"][ctx_path]['org.ofono.ConnectionContext']
                    await self.force_activate_context()
                    ctx_interface.on_property_changed(self.context_active_changed)

    def export_mms_message(self, uuid, status, date, sender, delivery_report, recipients, smil, attachments):
        ofono_mms_message = OfonoMMSMessageInterface(self.mms_dir, uuid, self.delete_mms_message, self.verbose)

        if status == 'received' and not recipients:
            recipients.append(self.ofono_mms_modemmanager_interface.props['ModemNumber'].value)

        props_array = {
            'Status': Variant('s', status),
            'Date': Variant('s', date),
            'Sender': Variant('s', sender),
            'Delivery Report': Variant('b', delivery_report),
            'Modem Number': Variant('s', self.ofono_mms_modemmanager_interface.props['ModemNumber'].value),
            'Recipients': Variant('as', recipients),
            'Smil': Variant('s', smil),
            'Attachments': Variant('a(ssstt)', attachments)
        }

        ofono_mms_message.update_properties(props_array)

        object_path = f'/org/ofono/mms/{uuid}'
        self.session_bus.export(object_path, ofono_mms_message)

        self.ofono_mms_service_interface.messages.append([object_path, props_array])
        self.ofono_mms_service_interface.MessageAdded(object_path, props_array)

    def delete_mms_message(self, uuid):
        object_path = f'/org/ofono/mms/{uuid}'
        mmsd_print(f"Unexporting MMS message at path {object_path}", self.verbose)
        self.session_bus.unexport(object_path)
        for message in self.ofono_mms_service_interface.messages:
            if message[0] == object_path:
                self.ofono_mms_service_interface.messages.remove(message)
                break

        self.ofono_mms_service_interface.MessageRemoved(object_path)

    async def force_activate_context(self):
        while True:
            try:
                ret = await self.activate_mms_context()
                if ret == True:
                    return
            except Exception as e:
                mmsd_print(f"Failed to activate context: {e}", self.verbose)

            await asyncio.sleep(2)

    async def context_active_changed(self, property, propvalue):
        mmsd_print(f"property: {property}, value: {propvalue}", self.verbose)
        if property == "Active":
            if propvalue.value == False:
                mmsd_print("oFono MMS connection dropped while we still need it, reactivating context", self.verbose)
                await self.force_activate_context()

    async def activate_mms_context(self):
        try:
            contexts = await self.ofono_interfaces['org.ofono.ConnectionManager'].call_get_contexts()
            for ctx in contexts:
                type = ctx[1].get('Type', Variant('s', '')).value
                if type.lower() == "mms":
                    ofono_ctx_interface = self.ofono_client["ofono_context"][ctx[0]]["org.ofono.ConnectionContext"]
                    await ofono_ctx_interface.call_set_property("Active", Variant('b', True))
                    return True
        except Exception as e:
            mmsd_print(f"Failed to activate MMS context: {e}", self.verbose)
            return False

    def ofono_modem_removed(self, path):
        mmsd_print(f"oFono modem removed at path {path}", self.verbose)

        for ofono_mms_object in self.ofono_mms_objects:
            try:
                for ofono_mms in self.ofono_mms_interfaces:
                    if ofono_mms.modem_name == path:
                        mmsd_print("oFono path matches our modem interface path, unexporting", self.verbose)
                        self.session_bus.unexport(ofono_mms)
                        ofono_mms = None
            except Exception as e:
                mmsd_print(f"Failed to unexport modem at path {path} with object path {ofono_mms}: {e}", self.verbose)

        self.ofono_mms_objects = []
        self.ofono_mms_interfaces = []

    async def init_ofono_interfaces(self):
        mmsd_print("Initialize oFono interfaces", self.verbose)

        for iface in self.ofono_props['Interfaces'].value:
            await self.add_ofono_interface(iface)

    def ofono_changed(self, name, varval):
        self.ofono_props[name] = varval
        if name == "Interfaces":
            for iface in varval.value:
                if not (iface in self.ofono_interfaces):
                    self.loop.create_task(self.add_ofono_interface(iface))
            for iface in self.ofono_interfaces:
                if not (iface in varval.value):
                    self.loop.create_task(self.remove_ofono_interface(iface))

        if self.ofono_mms_service_interface:
            self.ofono_mms_service_interface.ofono_changed(name, varval)
        if self.ofono_mms_modemmanager_interface:
            self.ofono_mms_modemmanager_interface.ofono_changed(name, varval)
        if self.ofono_push_notification_interface:
            self.ofono_push_notification_interface.ofono_changed(name, varval)

    def ofono_interface_changed(self, iface):
        def ch(name, varval):
            if iface in self.ofono_interface_props:
                self.ofono_interface_props[iface][name] = varval
                if self.ofono_mms_service_interface:
                    self.ofono_mms_service_interface.ofono_interface_changed(iface)(name, varval)
                if self.ofono_mms_modemmanager_interface:
                    self.ofono_mms_modemmanager_interface.ofono_interface_changed(iface)(name, varval)
                if self.ofono_push_notification_interface:
                    self.ofono_push_notification_interface.ofono_interface_changed(iface)(name, varval)

        return ch

    async def add_ofono_interface(self, iface):
        unused_interfaces = {
            "org.ofono.CallSettings",
            "org.ofono.CallVolume",
            "org.ofono.SimToolkit",
            "org.ofono.Phonebook",
            "org.ofono.SmartMessaging",
            "org.ofono.CallBarring",
            "org.ofono.CallForwarding",
            "org.ofono.MessageWaiting",
            "org.ofono.AllowedAccessPoints",
            "org.nemomobile.ofono.CellInfo",
            "org.nemomobile.ofono.SimInfo"
        }

        if iface in unused_interfaces:
            mmsd_print(f"Interface is {iface} which is unused, skipping", self.verbose)
        else:
            mmsd_print(f"Add oFono interface for iface {iface}", self.verbose)

        self.ofono_interfaces.update({
            iface: self.ofono_proxy[iface]
        })

        try:
            self.ofono_interface_props.update({
                iface: await self.ofono_interfaces[iface].call_get_properties()
            })

            if self.ofono_mms_service_interface:
                self.ofono_mms_service_interface.ofono_interface_props = self.ofono_interface_props.copy()

            if self.ofono_mms_modemmanager_interface:
                self.ofono_mms_modemmanager_interface.ofono_interface_props = self.ofono_interface_props.copy()

            if self.ofono_push_notification_interface:
                self.ofono_push_notification_interface.ofono_interface_props = self.ofono_interface_props.copy()

            self.ofono_interfaces[iface].on_property_changed(self.ofono_interface_changed(iface))
        except DBusError:
            self.ofono_interface_props.update({
                iface: {}
            })

            if self.ofono_mms_service_interface:
                self.ofono_mms_service_interface.ofono_interface_props = self.ofono_interface_props.copy()

            if self.ofono_mms_modemmanager_interface:
                self.ofono_mms_modemmanager_interface.ofono_interface_props = self.ofono_interface_props.copy()

            if self.ofono_push_notification_interface:
                self.ofono_push_notification_interface.ofono_interface_props = self.ofono_interface_props.copy()

            self.ofono_interfaces[iface].on_property_changed(self.ofono_interface_changed(iface))
        except AttributeError:
            pass

        if self.ofono_mms_service_interface:
            self.ofono_mms_service_interface.set_props()

        if self.ofono_mms_modemmanager_interface:
            await self.ofono_mms_modemmanager_interface.set_props()

    async def remove_ofono_interface(self, iface):
        mmsd_print(f"Remove oFono interface for iface {iface}", self.verbose)

        if iface in self.ofono_interfaces:
            self.ofono_interfaces.pop(iface)
        if iface in self.ofono_interface_props:
            self.ofono_interface_props.pop(iface)

        if self.ofono_mms_service_interface:
            self.ofono_mms_service_interface.ofono_interface_props = self.ofono_interface_props.copy()
            self.ofono_mms_service_interface.set_props()

        if self.ofono_mms_modemmanager_interface:
            self.ofono_mms_modemmanager_interface.ofono_interface_props = self.ofono_interface_props.copy()
            await self.ofono_mms_modemmanager_interface.set_props()

        if self.ofono_push_notification_interface:
            self.ofono_push_notification_interface.ofono_interface_props = self.ofono_interface_props.copy()

def get_version():
    return "2.6.1"

def print_version():
    version = get_version()
    print(f"MMSD version {version}")

def custom_help(parser):
    parser.print_help()
    print("\nMultimedia Messaging Service Daemon")

async def main():
    parser = ArgumentParser(description="Run the MMSD interface.", add_help=False)
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output.')
    parser.add_argument('-V', '--version', action='store_true', help='Print version.')
    parser.add_argument('-h', '--help', action='store_true', help='Show help.')

    args = parser.parse_args()

    if args.version:
        print_version()
        return

    if args.help:
        custom_help(parser)
        return

    if environ.get('MODEM_DEBUG', 'false').lower() == 'true':
        verbose = True
    else:
        verbose = args.verbose

    system_bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    session_bus = await MessageBus(bus_type=BusType.SESSION).connect()
    loop = asyncio.get_running_loop()
    ofono_mms_manager_interface = OfonoMMSManagerInterface(loop, system_bus, session_bus, verbose=verbose)
    session_bus.export('/org/ofono/mms', ofono_mms_manager_interface)
    await session_bus.wait_for_disconnect()

if __name__ == "__main__":
    asyncio.run(main())
