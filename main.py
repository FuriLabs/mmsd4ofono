#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

import asyncio
import sys
from os import environ
from argparse import ArgumentParser
from os.path import expanduser
from os import makedirs
from tenacity import retry, wait_fixed

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import DBusError, BusType, Variant

from mmsd import OfonoMMSServiceInterface, OfonoMMSModemManagerInterface, OfonoMMSMessageInterface, OfonoPushNotification, Ofono, DBus
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
        self.export_new_modem_tasks = {}
        self.modem_added_block = False
        self.ofono_mms_service_interface = False
        self.ofono_mms_modemmanager_interface = False
        self.ofono_push_notification_interface = False
        self.already_exported = False
        self.activation_task = None
        self.context_property_setting = False
        self.home = expanduser("~")
        self.mms_dir = expanduser("~/.mms/modemmanager")
        makedirs(self.mms_dir, exist_ok=True)
        self.loop.create_task(self.check_ofono_presence())
        self.unused_interfaces = {
            "org.ofono.RadioSettings",
            "org.ofono.NetworkMonitor",
            "org.ofono.IpMultimediaSystem",
            "org.ofono.SupplementaryServices",
            "org.ofono.NetworkRegistration",
            "org.ofono.MessageManager",
            "org.ofono.NetworkTime",
            "org.ofono.SmsHistory",
            "org.ofono.SimAuthentication",
            "org.ofono.VoiceCallManager",
            "org.ofono.CellBroadcast",
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

        self.required_interfaces = {
            "org.ofono.ConnectionManager",
            "org.ofono.PushNotification",
            "org.ofono.SimManager"
        }

        self.ALLOWED_MMS_PROPERTIES = {
            'AccessPointName',
            'MessageProxy',
            'MessageCenter',
            'Username',
            'Password'
        }

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

        return self.props['services']

    async def mms_set_properties(self, properties):
        retries = 0
        max_retries = 10

        while retries < max_retries:
            try:
                # Cancel any ongoing activation task
                if self.activation_task and not self.activation_task.done():
                    mmsd_print("Cancelling ongoing activation task", self.verbose)
                    self.activation_task.cancel()
                    try:
                        await self.activation_task
                    except asyncio.CancelledError:
                        pass

                self.context_property_setting = True
                contexts = await self.ofono_interfaces['org.ofono.ConnectionManager'].call_get_contexts()
                mms_ctx = None

                for ctx in contexts:
                    type_value = ctx[1].get('Type', Variant('s', '')).value
                    if type_value.lower() == "mms":
                        mms_ctx = ctx
                        break

                if not mms_ctx:
                    mmsd_print("No MMS context found", self.verbose)
                    return

                ctx_path = mms_ctx[0]
                ctx_interface = self.ofono_client["ofono_context"][ctx_path]['org.ofono.ConnectionContext']

                mmsd_print("Deactivating MMS context before setting properties", self.verbose)
                await ctx_interface.call_set_property("Active", Variant('b', False))

                for property, value in properties.items():
                    await ctx_interface.call_set_property(property, Variant('s', value))

                # Success - start reactivation and return
                self.activation_task = self.loop.create_task(self.force_activate_context())
                return
            except Exception as e:
                if "Operation already in progress" in str(e):
                    retries += 1
                    mmsd_print(f"Operation in progress, retry {retries}/{max_retries}", self.verbose)
                    await asyncio.sleep(1)
                else:
                    raise

            finally:
                self.context_property_setting = False

        mmsd_print("Properties setting completed", self.verbose)

    @method()
    async def SetMMSContextProperty(self, property: 's', value: 's') -> None:
        if property not in self.ALLOWED_MMS_PROPERTIES:
            raise ValueError(f"Property {property} is not allowed. Allowed properties are: {', '.join(self.ALLOWED_MMS_PROPERTIES)}")

        await self.mms_set_properties({property: value})

    @method()
    async def SetMMSContextProperties(self, properties: 'a{ss}') -> None:
        for property in properties.keys():
            if property not in self.ALLOWED_MMS_PROPERTIES:
                raise ValueError(f"Property {property} is not allowed. Allowed properties are: {', '.join(self.ALLOWED_MMS_PROPERTIES)}")

        await self.mms_set_properties(properties)

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

    async def find_ofono_modems(self):
        mmsd_print("Finding oFono modems", self.verbose)

        if not self.ofono_manager_interface:
            mmsd_print("oFono manager interface is empty, skipping", self.verbose)
            return

        self.ofono_modem_list = False
        self.modem_added_block = True
        attempts = 0
        max_attempts = 5
        modem = None
        modem_interfaces = None
        while not self.ofono_modem_list and attempts < max_attempts:
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
                    attempts += 1
                    if attempts < max_attempts:
                        await asyncio.sleep(2)
                    continue

                modem = self.ofono_modem_list[0]

                modem_interfaces = set(self.ofono_modem_list[0][1]['Interfaces'].value)
                if not self.required_interfaces.issubset(modem_interfaces):
                    mmsd_print("Required interfaces not available, retrying", self.verbose)
                    self.ofono_modem_list = False
                    attempts += 1
                    if attempts < max_attempts:
                        await asyncio.sleep(2)
                        self.ofono_modem_list = False
                    continue
            except DBusError as e:
                mmsd_print(f"Failed to get the current modem: {e}", self.verbose)
                self.ofono_modem_list = False
                attempts += 1
                if attempts < max_attempts:
                    await asyncio.sleep(2)

        if not self.ofono_modem_list:
            mmsd_print("Failed to initialize modem after 5 attempts", self.verbose)
            if modem and "org.ofono.SimManager" in modem_interfaces:
                ofono_sim_manager = self.ofono_client["ofono_modem"][modem[0]]['org.ofono.SimManager']
                sim_props = await ofono_sim_manager.call_get_properties()
                if 'PinRequired' in sim_props and sim_props['PinRequired'].value != 'none':
                    mmsd_print("SIM is locked. setting a listener for unlock", self.verbose)
                    ofono_sim_manager.on_property_changed(self.sim_property_changed)

                # export mmsd objects over dbus. even if we can't send any message, we want the object exposed over dbus
                # if they are not exported, systemd will assume service has failed and it will restart it.
                # exporting it early really doesn't change anything since there is no logic bound to ofono in any of the interfaces initialization
                self.loop.create_task(self.export_mmsd_objects(modem[0]))
            return

        try:
            if modem is None:
                mmsd_print("No modem found or modem is None", self.verbose)
                return

            mmsd_print(f"modem is {modem[0]}", self.verbose)

            task = self.loop.create_task(self.export_new_modem(modem[0], modem[1]))
            self.export_new_modem_tasks[modem[0]] = task
            await task
        except DBusError as e:
            mmsd_print(f"Error interacting with modem {modem[0]}: {e}", self.verbose)

    async def sim_property_changed(self, property, value):
        mmsd_print(f"SIM property changed: property: {property}, value: {value.value}", self.verbose)
        if property == "PinRequired":
            if value.value == "none":
                # sim is now unlocked, try finding the modem again
                self.loop.create_task(self.find_ofono_modems())

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
            task = self.loop.create_task(self.export_new_modem(path, mprops))
            self.export_new_modem_tasks[path] = task
        except Exception as e:
            mmsd_print(f"Failed to create task for modem {path}: {e}", self.verbose)

    async def export_mmsd_objects(self, path):
        global has_bus

        if "/org/ofono/mms" not in self.ofono_mms_objects:
            self.ofono_mms_modemmanager_interface = OfonoMMSModemManagerInterface(self.ofono_client, self.ofono_props, self.ofono_interfaces, self.ofono_interface_props, self.mms_dir, path, self.verbose)
            self.session_bus.export('/org/ofono/mms', self.ofono_mms_modemmanager_interface)
            await self.ofono_mms_modemmanager_interface.set_props()
            self.ofono_mms_interfaces.append(self.ofono_mms_modemmanager_interface)
            self.ofono_mms_objects.append('/org/ofono/mms')
        else:
            mmsd_print("Skip exporting mms modem manager interface at /org/ofono/mms, path is already exported", self.verbose)

        if self.props['services'][0][0] not in self.ofono_mms_objects:
            self.ofono_mms_service_interface = OfonoMMSServiceInterface(self.ofono_client, self.ofono_props, self.ofono_interfaces, self.ofono_interface_props, self.mms_dir, self.ofono_mms_modemmanager_interface, self.export_mms_message, path, self.verbose)
            self.session_bus.export(self.props['services'][0][0], self.ofono_mms_service_interface)
            self.ofono_mms_service_interface.set_props()
            self.ofono_mms_interfaces.append(self.ofono_mms_service_interface)
            self.ofono_mms_objects.append(self.props['services'][0][0])
        else:
            mmsd_print("Skip exporting mms service interface at /org/ofono/mms/modemmanager, path is already exported", self.verbose)

        if not has_bus and len(self.ofono_mms_objects) != 0:
            await self.session_bus.request_name('org.ofono.mms')
            has_bus = True

    @retry(wait=wait_fixed(3))
    async def export_new_modem(self, path, mprops):
        try:
            mmsd_print(f"Processing modem {path} with properties {mprops}", self.verbose)
            if self.already_exported:
                mmsd_print(f"Skipping modem: {path}. dual sim is not yet supported by MMSD", self.verbose)
                return

            self.ofono_props = mprops
            self.ofono_proxy = self.ofono_client["ofono_modem"][path]
            self.ofono_proxy['org.ofono.Modem'].on_property_changed(self.ofono_changed)
            await self.init_ofono_interfaces()

            await self.export_mmsd_objects(path)

            self.ofono_push_notification_interface = OfonoPushNotification(self.system_bus, self.ofono_client, self.ofono_props, self.ofono_interfaces, self.ofono_interface_props, self.mms_dir, self.export_mms_message, path, self.verbose)
            await self.ofono_push_notification_interface.RegisterAgent('/mmsd')
            self.ofono_mms_interfaces.append(self.ofono_push_notification_interface)
            self.ofono_mms_objects.append('/mmsd')

            try:
                self.ofono_push_notification_interface.export_old_messages()
            except Exception as e:
                mmsd_print(f"Failed to export old messages: {e}", self.verbose)

            self.loop.create_task(self.setup_mms_context_monitoring())

            self.modem_added_block = False
            self.already_exported = True
        except asyncio.CancelledError:
            mmsd_print(f"export_new_modem task cancelled for path {path}", self.verbose)
            raise  # Re-raise the CancelledError to properly handle the cancellation
        finally:
            if path in self.export_new_modem_tasks:
                del self.export_new_modem_tasks[path]

    async def setup_mms_context_monitoring(self):
        mmsd_print("Setting up mms context", self.verbose)
        try:
            contexts = await self.ofono_interfaces['org.ofono.ConnectionManager'].call_get_contexts()
            for ctx in contexts:
                name = ctx[1].get('Type', Variant('s', '')).value
                if name.lower() == "mms":
                    ctx_path = ctx[0]
                    ctx_interface = self.ofono_client["ofono_context"][ctx_path]['org.ofono.ConnectionContext']
                    self.activation_task = self.loop.create_task(self.force_activate_context())
                    ctx_interface.on_property_changed(self.context_active_changed)
        except Exception as e:
            mmsd_print(f"Failed to set up MMS context monitoring: {e}", self.verbose)

    def export_mms_message(self, uuid, status, date, sender, delivery_report, recipients, smil, attachments):
        ofono_mms_message = OfonoMMSMessageInterface(self.mms_dir, uuid, self.delete_mms_message, self.verbose)

        if status == 'received' and not recipients:
            recipients.append(self.ofono_mms_modemmanager_interface.props['ModemNumber'].value)

        props_array = {
            'Status': Variant('s', status),
            'Date': Variant('s', date),
            'Subject': Variant('s', ''),
            'Sender': Variant('s', sender),
            'Delivery Report': Variant('b', delivery_report),
            'Modem Number': Variant('s', self.ofono_mms_modemmanager_interface.props['ModemNumber'].value),
            'Recipients': Variant('as', recipients),
            'Smil': Variant('s', smil),
            'Attachments': Variant('a(ssstt)', attachments)
        }

        if status == 'sent':
            props_array['Status'] = Variant('s', 'draft')

        ofono_mms_message.update_properties(props_array)

        object_path = f"{self.props['services'][0][0]}/{uuid}"

        self.session_bus.export(object_path, ofono_mms_message)

        self.ofono_mms_service_interface.messages.append([object_path, props_array])
        self.ofono_mms_service_interface.MessageAdded(object_path, props_array)

        if status == 'sent':
            props_array['Status'] = Variant('s', status)

        ofono_mms_message.update_properties(props_array)
        ofono_mms_message.PropertyChanged('status', props_array['Status'])

        return object_path

    def delete_mms_message(self, uuid):
        object_path = f"{self.props['services'][0][0]}/{uuid}"
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
                if hasattr(self, 'context_property_setting') and self.context_property_setting:
                    mmsd_print("Property setting in progress, stopping activation", self.verbose)
                    return

                ret = await self.activate_mms_context()
                if ret == True:
                    return
            except asyncio.CancelledError:
                mmsd_print("Force activate context task cancelled", self.verbose)
                raise
            except Exception as e:
                mmsd_print(f"Failed to activate context: {e}", self.verbose)

            await asyncio.sleep(2)

    async def context_active_changed(self, property, propvalue):
        mmsd_print(f"property: {property}, value: {propvalue}", self.verbose)
        if property == "Active" and not self.context_property_setting:
            if propvalue.value == False:
                mmsd_print("oFono MMS connection dropped while we still need it, reactivating context", self.verbose)
                if self.activation_task and not self.activation_task.done():
                    self.activation_task.cancel()
                self.activation_task = self.loop.create_task(self.force_activate_context())

    async def activate_mms_context(self):
        ofono_internet_ctx_interface = None
        ofono_mms_ctx_interface = None
        internet_apn = None
        mms_apn = None

        try:
            contexts = await self.ofono_interfaces['org.ofono.ConnectionManager'].call_get_contexts()
            for ctx in contexts:
                ctx_path = ctx[0]
                ctx_properties = ctx[1]

                ctx_type = ctx_properties.get('Type', Variant('s', '')).value
                ctx_apn = ctx_properties.get('AccessPointName', Variant('s', '')).value

                if ctx_type.lower() == "internet":
                    ofono_internet_ctx_interface = self.ofono_client["ofono_context"][ctx_path]["org.ofono.ConnectionContext"]
                    internet_apn = ctx_apn
                elif ctx_type.lower() == "mms":
                    ofono_mms_ctx_interface = self.ofono_client["ofono_context"][ctx_path]["org.ofono.ConnectionContext"]
                    mms_apn = ctx_apn

            # if there is no MMS context then there is nothing to do here
            if ofono_mms_ctx_interface is None:
                mmsd_print("No MMS context found", self.verbose)
                return True

            # if internet and MMS context APNs clash, MMS won't work and it will cause an infinite loop here which causes data instability
            if (ofono_internet_ctx_interface is not None and
                internet_apn is not None and
                mms_apn is not None and
                internet_apn == mms_apn):
                mmsd_print(f"Internet and MMS contexts use the same APN ({internet_apn}), no activation needed", self.verbose)
                return True

            await ofono_mms_ctx_interface.call_set_property("Active", Variant('b', True))
            return True
        except Exception as e:
            mmsd_print(f"Failed to activate MMS context: {e}", self.verbose)
            return False

    def ofono_modem_removed(self, path):
        mmsd_print(f"oFono modem removed at path {path}", self.verbose)
        mmsd_print(f"Exported object paths before unexporting: {self.ofono_mms_objects}", self.verbose)

        if path in self.export_new_modem_tasks:
            task = self.export_new_modem_tasks[path]
            if not task.done():
                task.cancel()
                mmsd_print(f"Cancelled export_new_modem task for path {path}", self.verbose)
            del self.export_new_modem_tasks[path]

        self.ofono_push_notification_interface = False

        objects_to_remove = []
        for ofono_mms_object in self.ofono_mms_objects:
            try:
                for ofono_mms in self.ofono_mms_interfaces:
                    if ofono_mms.modem_name == path:
                        if ofono_mms_object == "/mmsd":
                            mmsd_print(f"oFono modem at object path {ofono_mms.modem_name} matches our modem interface path, unexporting {ofono_mms_object}", self.verbose)
                            self.session_bus.unexport(ofono_mms_object)
                            self.ofono_mms_objects.remove("/mmsd")
                            break
            except Exception as e:
                mmsd_print(f"Failed to unexport modem at path {path} with object path {ofono_mms}: {e}", self.verbose)

        mmsd_print(f"Exported object paths after unexporting: {self.ofono_mms_objects}", self.verbose)

        self.already_exported = False
        self.modem_added_block = False

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
        if iface in self.unused_interfaces:
            mmsd_print(f"Interface is {iface} which is unused, skipping", self.verbose)
            return
        else:
            mmsd_print(f"Add oFono interface for iface {iface}", self.verbose)

        try:
            self.ofono_interfaces.update({
                iface: self.ofono_proxy[iface]
            })
        except Exception as e:
            mmsd_print(f"Failed to add iface {iface}, ignoring", self.verbose)
            return

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
    return "1.3.2"

def print_version():
    version = get_version()
    print(f"MMSD version {version}")

def custom_help(parser):
    parser.print_help()
    print("\nMultimedia Messaging Service Daemon")

async def main():
    # Disable buffering for stdout and stderr so that logs are written immediately
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

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

    try:
        await session_bus.wait_for_disconnect()
    except:
        print("Session bus disconnected, exiting")

if __name__ == "__main__":
    asyncio.run(main())
