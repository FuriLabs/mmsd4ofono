# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import Variant, DBusError

from mmsd.logging import mmsd_print

import asyncio

class OfonoMMSMessageInterface(ServiceInterface):
    def __init__(self, mms_dir, verbose=False):
        super().__init__('org.ofono.mms.Message')
        mmsd_print("Initializing MMS Messageinterface", verbose)
        self.mms_dir = mms_dir
        self.verbose = verbose
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
    def MarkRead(self):
        mmsd_print("Marking as read", self.verbose)

    @method()
    def Delete(self):
        mmsd_print("Deleting message", self.verbose)

    @signal()
    def PropertyChanged(self, name, value) -> 'a{sv}':
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

#    @dbus_property(access=PropertyAccess.READ, name="Delivery Report")
#    def DeliveryReport(self) -> 'b':
#        return self.props['Delivery Report'].value

#    @dbus_property(access=PropertyAccess.READ, name="Delivery Status")
#    def DeliveryStatus(self) -> 's':
#        return self.props['Delivery Status'].value

#    @dbus_property(access=PropertyAccess.READ, name="Modem Number")
#    def ModemNumber(self) -> 's':
#        return self.props['Modem Number'].value

    @dbus_property(access=PropertyAccess.READ)
    def Recipients(self) -> '{sv}':
        return self.props['Recipients'].value

    @dbus_property(access=PropertyAccess.READ)
    def Smil(self) -> 's':
        return self.props['Smil'].value

    @dbus_property(access=PropertyAccess.READ)
    def Attachments(self) -> '{sv}':
        return self.props['Attachments'].value
