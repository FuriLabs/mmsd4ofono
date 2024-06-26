# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import Variant, DBusError

from mmsd.logging import mmsd_print

import asyncio

class OfonoMMSServiceInterface(ServiceInterface):
    def __init__(self, ofono_client, ofono_props, ofono_interfaces, ofono_interface_props, verbose=False):
        super().__init__('org.ofono.mms.Service')
        mmsd_print("Initializing MMS Service interface", verbose)
        self.ofono_client = ofono_client
        self.verbose = verbose
        self.ofono_props = ofono_props
        self.ofono_interfaces = ofono_interfaces
        self.ofono_interface_props = ofono_interface_props
        self.props = {
            'UseDeliveryReports': Variant('b', False),
            'AutoCreateSMIL': Variant('b', False),
            'TotalMaxAttachmentSize': Variant('i', 1100000),
            'MaxAttachments': Variant('i', 25),
            'NotificationInds': Variant('i', 0)
        }

    def set_props(self):
        mmsd_print("Setting properties", self.verbose)

    @method()
    async def GetMessages(self) -> 'a(oa{sv})':
        mmsd_print("Getting messages", self.verbose)
        return []

    @method()
    async def GetProperties(self) -> 'a{sv}':
        mmsd_print("Getting properties", self.verbose)
        return self.props

    @method()
    async def SendMessage(self, recipients: 'as', smil: 'v', attachments: 'a(sss)') -> 'o':
        mmsd_print(f"Sending message to recipients {recipients}, smil {smil}, attachments {recipients}", self.verbose)
        object_path = '/'
        self.MessageAdded(object_path, {})
        return object_path

    @method()
    async def SendMessage(self, recipients: 'as', options: 'a{sv}', attachments: 'a(sss)') -> 'o':
        mmsd_print(f"Sending message to recipients {recipients}, options {options}, attachments {recipients}", self.verbose)
        object_path = '/'
        self.MessageAdded(object_path, options)
        return object_path

    @method()
    async def SetProperty(self, property: 's', value: 'v'):
        mmsd_print(f"Setting property {property} to value {value}", self.verbose)
        if property in self.props:
            self.props[property] = value

    @signal()
    def MessageAdded(self, path, properties) -> 'oa{sv}':
        mmsd_print(f"Message added emitted with path {path} and properties {properties}", self.verbose)
        return [path, properties]

    @signal()
    def MessageRemoved(self, path) -> 'o':
        mmsd_print(f"Message removed emitted with path {path}", self.verbose)
        return path

    @signal()
    def MessageSendError(self, properties) -> 'a{sv}':
        mmsd_print(f"Message send error emitted with path properties {properties}", self.verbose)
        return properties

    @signal()
    def MessageReceiveError(self, properties) -> 'a{sv}':
        mmsd_print(f"Message receive error emitted with path properties {properties}", self.verbose)
        return properties

    @dbus_property(access=PropertyAccess.READ)
    def UseDeliveryReports(self) -> 'b':
        return self.props['UseDeliveryReports'].value

    @dbus_property(access=PropertyAccess.READ)
    def AutoCreateSMIL(self) -> 'b':
        return self.props['AutoCreateSMIL'].value

    @dbus_property(access=PropertyAccess.READ)
    def TotalMaxAttachmentSize(self) -> 'i':
        return self.props['TotalMaxAttachmentSize'].value

    @dbus_property(access=PropertyAccess.READ)
    def MaxAttachments(self) -> 'i':
        return self.props['MaxAttachments'].value

    @dbus_property(access=PropertyAccess.READ)
    def NotificationInds(self) -> 'i':
        return self.props['NotificationInds'].value

    def ofono_changed(self, name, varval):
        self.ofono_props[name] = varval
        self.set_props()

    def ofono_client_changed(self, ofono_client):
        self.ofono_client = ofono_client

    def ofono_interface_changed(self, iface):
        def ch(name, varval):
            if iface in self.ofono_interface_props:
                self.ofono_interface_props[iface][name] = varval
            self.set_props()

        return ch
