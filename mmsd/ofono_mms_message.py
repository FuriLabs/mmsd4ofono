# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from os.path import join
from os import listdir, remove
import asyncio

from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import Variant, DBusError

from mmsd.logging import mmsd_print

class OfonoMMSMessageInterface(ServiceInterface):
    def __init__(self, mms_dir, uuid, delete_mms_message, verbose=False):
        super().__init__('org.ofono.mms.Message')
        mmsd_print(f"Initializing MMS Message interface with UUID {uuid}", verbose)
        self.mms_dir = mms_dir
        self.uuid = uuid
        self.delete_mms_message = delete_mms_message
        self.verbose = verbose
        self.props = {
            'Status': Variant('s', ''),
            'Date': Variant('s', ''),
            'Subject': Variant('s', ''),
            'Sender': Variant('s', ''),
            'Delivery Report': Variant('b', False),
            'Delivery Status': Variant('s', ''),
            'Modem Number': Variant('s', ''),
            'Recipients': Variant('as', []),
            'Smil': Variant('s', ''),
            'Attachments': Variant('a(ssstt)', [])
        }

    @method()
    def MarkRead(self):
        mmsd_print(f"Marking {self.uuid} as read", self.verbose)

        self.props['Status'] = Variant('s', 'read')
        self.PropertyChanged('Status', self.props['Status'])

        status_file = join(self.mms_dir, self.uuid + '.status')
        with open(status_file, 'r+') as f:
            lines = f.readlines()
            for i, line in enumerate(lines):
                if line.startswith('read='):
                    lines[i] = 'read=true\n'
                elif line.startswith('state='):
                    lines[i] = 'state=read\n'

            f.seek(0)
            f.writelines(lines)

    @method()
    def Delete(self):
        matching_files = [filename for filename in listdir(self.mms_dir) if self.uuid in filename]
        mmsd_print(f"Deleting message {self.uuid}: Matching files are: {matching_files}", self.verbose)

        self.delete_mms_message(self.uuid)
        for filename in matching_files:
            file_path = join(self.mms_dir, filename)
            try:
                remove(file_path)
                mmsd_print(f"Removed {filename}", self.verbose)
            except OSError as e:
                mmsd_print(f"Error removing {filename}: {e}", self.verbose)

    @signal()
    def PropertyChanged(self, name, value) -> 'sv':
        mmsd_print(f"Emitting property changed: name: {name}, value: {value}", self.verbose)
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

    @dbus_property(access=PropertyAccess.READ, name="Delivery Report")
    def DeliveryReport(self) -> 'b':
        return self.props['Delivery Report'].value

    @dbus_property(access=PropertyAccess.READ, name="Delivery Status")
    def DeliveryStatus(self) -> 's':
        return self.props['Delivery Status'].value

    @dbus_property(access=PropertyAccess.READ, name="Modem Number")
    def ModemNumber(self) -> 's':
        return self.props['Modem Number'].value

    @dbus_property(access=PropertyAccess.READ)
    def Recipients(self) -> 'as':
        return self.props['Recipients'].value

    @dbus_property(access=PropertyAccess.READ)
    def Smil(self) -> 's':
        return self.props['Smil'].value

    @dbus_property(access=PropertyAccess.READ)
    def Attachments(self) -> 'a(ssstt)':
        return self.props['Attachments'].value

    def update_properties(self, props_array):
        for key, value in props_array.items():
            if key in self.props:
                self.props[key] = value
