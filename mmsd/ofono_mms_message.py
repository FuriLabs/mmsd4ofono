# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import Variant, DBusError

from mmsd.logging import mmsd_print

import asyncio

class OfonoMMSMessageInterface(ServiceInterface):
    def __init__(self, ofono_client, ofono_props, ofono_interfaces, ofono_interface_props, verbose=False):
        super().__init__('org.ofono.mms.Message')
        mmsd_print("Initializing MMS Messageinterface", verbose)
        self.ofono_client = ofono_client
        self.verbose = verbose
        self.ofono_props = ofono_props
        self.ofono_interfaces = ofono_interfaces
        self.ofono_interface_props = ofono_interface_props
        self.props = {
            'Status': Variant('s', ''),
            'Date': Variant('s', ''),
            'Subject': Variant('s', ''),
            'Sender': Variant('s', ''),
            'Delivery Report': Variant('b', False),
            'Delivery Status': Variant('s', ''),
            'Modem Number': Variant('s', ''),
            'Recipients': Variant('{sv}', ''),
            'Smil': Variant('s', ''),
            'Attachments': Variant('{sv}', '')
        }

    @method()
    async def MarkRead(self):
        mmsd_print("Marking as read", self.verbose)

    @method()
    async def Delete(self):
        mmsd_print("Deleting message", self.verbose)

    @signal()
    def PropertyChanged(self, name, value) -> 'a{sv)':
        mmsd_print(f"Property {name} changed to value {value}", self.verbose)
        return [name, value]

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> 's':
        return self.props['Status'].value

    @dbus_property(access=PropertyAccess.READ)
    def Date(self) -> 's':
        return self.props['Date'].value

    @dbus_property(access=PropertyAccess.READ)
    def Subject(self) -> 's':
        return self.props['Subject'].value

    @dbus_property(access=PropertyAccess.READ)
    def Sender(self) -> 's':
        return self.props['Sender'].value

    @dbus_property(access=PropertyAccess.READ)
    def Recipients(self) -> '{sv}':
        return self.props['Recipients'].value

    @dbus_property(access=PropertyAccess.READ)
    def Smil(self) -> 's':
        return self.props['Smil'].value

    @dbus_property(access=PropertyAccess.READ)
    def Attachments(self) -> '{sv}':
        return self.props['Attachments'].value

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
