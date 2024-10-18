# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from os.path import join, exists, getsize
from socket import socket, AF_INET, SOCK_STREAM
from string import ascii_letters, digits
from random import choice
from time import sleep
from uuid import uuid4
from io import StringIO
from re import sub
import asyncio

from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import Variant, DBusError

from mmsd.logging import mmsd_print

from mmsdecoder.message import MMSMessage, MMSMessagePage

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

        self.loop = asyncio.get_event_loop()
        self.executor = ThreadPoolExecutor()

    def generate_random_string(self, length=8):
        characters = ascii_letters + digits
        random_string = ''.join(choice(characters) for _ in range(length))
        return random_string.upper()

    def build_message(self, recipients, attachments):
        mms = MMSMessage()

        modem_number = self.ofono_mms_modemmanager_interface.props['ModemNumber'].value
        if modem_number:
            mms.headers['From'] = f"{modem_number}/TYPE=PLMN"

        recipients = [sub(r'\D', '', recipient) + '/TYPE=PLMN' for recipient in recipients]
        mms.headers['To'] = recipients
        mms.headers['Message-Type'] = 'm-send-req'
        mms.headers['MMS-Version'] = '1.1'

        id = self.generate_random_string()
        mms.headers['Transaction-Id'] = id
        mms.headers['Message-ID'] = id

        mms.headers['Content-Type'] = ('application/vnd.wap.multipart.mixed', {})

        for attachment in attachments:
            type = attachment[1].split('/')[0]
            if type == 'text':
                try:
                    with open(attachment[2], 'r') as file:
                        text_content = file.read()
                        text_slide = MMSMessagePage()
                        text_slide.add_text(text_content)
                        mms.add_page(text_slide)
                except Exception as e:
                    mmsd_print(f"Failed to process text attachment: {e}", self.verbose)
            elif type == 'image':
                try:
                    image_slide = MMSMessagePage()
                    image_slide.add_image(attachment[2])
                    mms.add_page(image_slide)
                except Exception as e:
                    mmsd_print(f"Failed to process image attachment: {e}", self.verbose)
            elif type == 'audio':
                try:
                    image_slide = MMSMessagePage()
                    image_slide.add_image(attachment[2])
                    mms.add_page(image_slide)
                except Exception as e:
                    mmsd_print(f"Failed to process audio attachment: {e}", self.verbose)
            else:
                mmsd_print(f"Attachment type {type} not supported, skipping", self.verbose)

        payload = mms.encode()
        smil = ''.join(mms.smil().split())

        return mms, payload, smil, id

    async def send_message_wrapper(self, payload, uuid):
        await self.loop.run_in_executor(self.executor, self.send_message, payload, uuid)

    def send_message(self, payload, uuid):
        while True:
            try:
                mmsc = self.ofono_mms_modemmanager_interface.props['CarrierMMSC'].value
                proxy = self.ofono_mms_modemmanager_interface.props['CarrierMMSProxy'].value

                if ':' not in proxy:
                    # Since it's an HTTP proxy we can default to port 80
                    proxy += ':80'

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

                mmsd_print(f"Message {uuid} sent successfully", self.verbose)
                break
            except Exception as e:
                mmsd_print(f"Error sending message: {str(e)}. Retrying...", self.verbose)
                sleep(5)

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

    def create_message_files(self, pdu, uuid, date, id):
        mmsd_print(f"Saving message {uuid} to disk", self.verbose)
        pdu_path = join(self.mms_dir, uuid)
        with open(pdu_path, 'wb') as pdu_file:
            pdu_file.write(pdu)

        status_path = join(self.mms_dir, f"{uuid}.status")
        with open(status_path, 'w') as status_file:
            status_file.write(f"[info]\n")
            status_file.write(f"read=false\n")
            status_file.write(f"state=sent\n")
            status_file.write(f"id={id}\n")
            status_file.write(f"date={date}\n")

    @method()
    def GetMessages(self) -> 'a(oa{sv})':
        mmsd_print("Getting messages", self.verbose)
        return self.messages

    @method()
    def GetProperties(self) -> 'a{sv}':
        mmsd_print("Getting properties", self.verbose)
        return self.props

    @method()
    def SendMessage(self, recipients: 'as', smil: 'v', attachments: 'a(sss)') -> 'o':
        mmsd_print(f"Sending message to recipients {recipients}, attachments {attachments}", self.verbose)
        uuid = str(uuid4()).replace('-', '1')

        updated_attachments = []
        for attachment in attachments:
            file_path = attachment[2]
            file_length = getsize(file_path)
            updated_attachment = list(attachment) + [0, file_length]
            updated_attachments.append(updated_attachment)
        attachments = updated_attachments

        mms, payload, smil, id = self.build_message(recipients, attachments)
        self.loop.create_task(self.send_message_wrapper(payload, uuid))
        date = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.create_message_files(payload, uuid, date, id)
        object_path = self.export_mms_message(uuid, 'sent', date, self.ofono_mms_modemmanager_interface.props['ModemNumber'].value, False, recipients, smil, attachments)

        return object_path

    @method(name="SendMessage")
    def SendMessage2(self, recipients: 'as', options: 'a{sv}', attachments: 'a(sss)') -> 'o':
        mmsd_print(f"Sending message to recipients {recipients}, options: {options}, attachments {attachments}", self.verbose)
        uuid = str(uuid4()).replace('-', '1')

        updated_attachments = []
        for attachment in attachments:
            file_path = attachment[2]
            file_length = getsize(file_path)
            updated_attachment = list(attachment) + [0, file_length]
            updated_attachments.append(updated_attachment)
        attachments = updated_attachments

        mms, payload, smil, id = self.build_message(recipients, attachments)
        self.loop.create_task(self.send_message_wrapper(payload, uuid))
        date = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.create_message_files(payload, uuid, date, id)
        object_path = self.export_mms_message(uuid, 'sent', date, self.ofono_mms_modemmanager_interface.props['ModemNumber'].value, False, recipients, smil, attachments)

        return object_path

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
