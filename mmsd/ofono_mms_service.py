# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from datetime import datetime
from os.path import join, exists, getsize
from socket import socket, AF_INET, SOCK_STREAM
from string import ascii_letters, digits
from random import choice
from uuid import uuid4
from io import StringIO
import asyncio

from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import Variant, DBusError

from mmsd.logging import mmsd_print

from mmsdecoder.message import MMSMessage

class OfonoMMSServiceInterface(ServiceInterface):
    def __init__(self, ofono_client, ofono_props, ofono_interfaces, ofono_interface_props, mms_dir, ofono_mms_modemmanager_interface, export_mms_message, path, verbose=False):
        super().__init__('org.ofono.mms.Service')
        self.modem_name = path
        mmsd_print("Initializing MMS Service interface", verbose)
        self.ofono_client = ofono_client
        self.verbose = verbose
        self.ofono_props = ofono_props
        self.ofono_interfaces = ofono_interfaces
        self.ofono_interface_props = ofono_interface_props
        self.mms_dir = mms_dir
        self.ofono_mms_modemmanager_interface = ofono_mms_modemmanager_interface
        self.export_mms_message = export_mms_message
        self.mms_config_file = join(self.mms_dir, 'mms')
        self.messages = []
        self.props = {
            'UseDeliveryReports': Variant('b', False),
            'AutoCreateSMIL': Variant('b', True),
            'TotalMaxAttachmentSize': Variant('i', 1100000),
            'MaxAttachments': Variant('i', 25),
            'NotificationInds': Variant('i', 0),
            'ForceCAres': Variant('b', True)
        }

    def generate_random_string(self, length=8):
        characters = ascii_letters + digits
        random_string = ''.join(choice(characters) for _ in range(length))
        return random_string

    async def send_message(self, recipients, attachments):
        mms = MMSMessage()

        modem_number = self.ofono_mms_modemmanager_interface.props['ModemNumber'].value
        if modem_number:
            mms.headers['From'] = f"{modem_number}/TYPE=PLMN"

        mms.headers['To'] = f"{recipients[0]}/TYPE=PLMN"
        mms.headers['Message-Type'] = 'm-send-req'
        mms.headers['MMS-Version'] = '1.1'

        id = self.generate_random_string()
        mms.headers['Transaction-Id'] = id
        mms.headers['Message-ID'] = id

        mms.headers['Content-Type'] = ('application/vnd.wap.multipart.mixed', {})
        payload = mms.encode()
        smil = ''.join(mms.smil().split())

        mmsc = self.ofono_mms_modemmanager_interface.props['CarrierMMSC'].value
        proxy = self.ofono_mms_modemmanager_interface.props['CarrierMMSProxy'].value
        gw_host, gw_port = proxy.split(':')
        gw_port = int(gw_port)

        mms_socket = socket(AF_INET, SOCK_STREAM)
        mms_socket.connect((gw_host, gw_port))
        mms_socket.send(f"POST {mmsc} HTTP/1.0\r\n".encode())
        mms_socket.send("Content-Type: application/vnd.wap.mms-message\r\n".encode())
        mms_socket.send(f"Content-Length: {len(payload)}\r\n\r\n".encode())

        mms_socket.sendall(payload)

        buf = StringIO()

        while True:
            data = mms_socket.recv(4096)
            if not data:
                break

        buf.write(data.decode())

        mms_socket.close()
        buf.close()

        return smil

    def set_props(self):
        mmsd_print("Setting properties", self.verbose)
        self.save_settings_to_file()

    def save_settings_to_file(self):
        mmsd_print(f"Saving settings to file {self.mms_config_file}", self.verbose)

        settings_section = '[Settings]\n'
        settings_content = ''.join(f'{key}={variant.value}\n' for key, variant in self.props.items())

        if exists(self.mms_config_file):
            with open(self.mms_config_file, 'r') as f:
                lines = f.readlines()
        else:
            lines = []

        inside_settings = False
        new_lines = []
        section_replaced = False

        for line in lines:
            if line.strip() == '[Settings]':
                inside_settings = True
                new_lines.append(settings_section)
                new_lines.append(settings_content)
                section_replaced = True
            elif line.strip().startswith('[') and inside_settings:
                inside_settings = False
                new_lines.append(line)
            elif not inside_settings:
                new_lines.append(line)

        if not section_replaced:
            new_lines.append(settings_section)
            new_lines.append(settings_content)

        with open(self.mms_config_file, 'w') as f:
            f.writelines(new_lines)

    @method()
    def GetMessages(self) -> 'a(oa{sv})':
        mmsd_print("Getting messages", self.verbose)
        return self.messages

    @method()
    def GetProperties(self) -> 'a{sv}':
        mmsd_print("Getting properties", self.verbose)
        return self.props

    @method()
    async def SendMessage(self, recipients: 'as', smil: 'v', attachments: 'a(sss)') -> 'o':
        mmsd_print(f"Sending message to recipients {recipients}, attachments {attachments}", self.verbose)
        uuid = str(uuid4()).replace('-', '1')
        object_path = f'/org/ofono/mms/modemmanager/{uuid}'

        updated_attachments = []
        for attachment in attachments:
            file_path = attachment[2]
            file_length = getsize(file_path)
            updated_attachment = list(attachment) + [0, file_length]
            updated_attachments.append(updated_attachment)
        attachments = updated_attachments

        smil = await self.send_message(recipients, attachments)
        date = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.export_mms_message(uuid, 'sent', date, self.ofono_mms_modemmanager_interface.props['ModemNumber'].value, False, recipients, smil, attachments)
        return object_path

#    @method()
#    def SendMessage(self, recipients: 'as', options: 'a{sv}', attachments: 'a(sss)') -> 'o':
#        mmsd_print(f"Sending message to recipients {recipients}, options {options}, attachments {attachments}", self.verbose)
#        uuid = str(uuid4()).replace('-', '1')
#        object_path = f'/org/ofono/mms/modemmanager/{uuid}'
#        self.MessageAdded(object_path, options)
#        return object_path

    @method()
    def SetProperty(self, property: 's', value: 'v'):
        mmsd_print(f"Setting property {property} to value {value}", self.verbose)
        if property in self.props:
            self.props[property] = value
            self.save_settings_to_file()

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
