# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from os.path import join, exists, getsize
from string import ascii_letters, digits
from random import choice
from urllib.parse import urlparse
from uuid import uuid4
from re import sub
import asyncio
import io

from aiohttp import ClientSession

from dbus_fast.service import ServiceInterface, method, dbus_property, signal
from dbus_fast.constants import PropertyAccess
from dbus_fast import Variant, DBusError

from mmsd.logging import mmsd_print

from mmsd.route_controller import cleanup_mms_routes, setup_mms_routes
from mmsd.utils import resolve_host
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

        # Write an empty string to From, which gets replaced by the PDU encoder to the insert-address-token
        mms.headers['From'] = ''

        recipients = [sub(r'[^0-9+]', '', recipient) + '/TYPE=PLMN' for recipient in recipients]
        mms.headers['To'] = recipients
        mms.headers['Message-Type'] = 'm-send-req'
        mms.headers['MMS-Version'] = '1.2'

        id = self.generate_random_string(length=40)
        mms.headers['Transaction-Id'] = id
        mmsd_print(f"Generated transaction ID: {id}", self.verbose)

        mms.headers['Content-Type'] = ('application/vnd.wap.multipart.related', {'Type': 'application/smil', 'Start': '<0000>'})
        mms.headers['Message-Class'] = 'Personal'

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
        smil = ' '.join(mms.smil().split())

        return mms, payload, smil, id

    async def send_message_wrapper(self, payload, uuid):
        await self.send_message(payload, uuid)

    async def send_message(self, payload, uuid):
        while True:
            try:
                mmsc = self.ofono_mms_modemmanager_interface.props['CarrierMMSC'].value
                proxy = self.ofono_mms_modemmanager_interface.props.get('CarrierMMSProxy', {}).value

                needed_ips = []
                resolved_proxy = None

                if proxy:
                    proxy_host = proxy if not ':' in proxy else proxy.split(':')[0]
                    proxy_ips = await resolve_host(proxy_host, self.verbose)
                    if not proxy_ips:
                         mmsd_print(f"Failed to resolve proxy host: {proxy_host}", self.verbose)
                         await asyncio.sleep(5)
                         continue

                    needed_ips.extend(proxy_ips)

                    proxy_port = '80' if not ':' in proxy else proxy.split(':')[1]
                    resolved_proxy = f"{proxy_ips[0]}:{proxy_port}"

                url_parts = urlparse(mmsc)

                if not proxy:
                    url_ips = await resolve_host(url_parts.hostname, self.verbose)
                    if not url_ips:
                        mmsd_print(f"Failed to resolve URL host: {url_parts.hostname}", self.verbose)
                        await asyncio.sleep(5)
                        continue
                    needed_ips.extend(url_ips)

                    resolved_url = url_parts._replace(
                        netloc=f"{url_ips[0]}" + (f":{url_parts.port}" if url_parts.port else "")
                    ).geturl()
                else:
                    # Leave the URL as is, the proxy will deal with it (hopefully)
                    resolved_url = mmsc

                if not await setup_mms_routes(needed_ips):
                    mmsd_print("Failed to setup MMS routes, retrying...", self.verbose)
                    await asyncio.sleep(5)
                    continue

                # payload is an array('B', [...]), convert it to bytes
                payload = payload if isinstance(payload, bytes) else payload.tobytes()

                async with ClientSession() as session:
                    try:
                         mmsd_print(f"Sending message to: {resolved_url} using proxy: {resolved_proxy}", self.verbose)

                         headers = {
                             'Host': url_parts.hostname,
                             'Content-Type': 'application/vnd.wap.mms-message',
                             'User-Agent': 'Android MmsLib/1.0',
                             'Connection': 'Keep-Alive',
                         }

                         if resolved_proxy:
                             async with session.post(
                                 resolved_url,
                                 proxy=f"http://{resolved_proxy}",
                                 headers=headers,
                                 data=payload,
                                 skip_auto_headers=['User-Agent']
                             ) as response:
                                mmsd_print(f"Response status: {response.status}", self.verbose)
                                response.raise_for_status()
                         else:
                             async with session.post(
                                 resolved_url,
                                 headers=headers,
                                 data=bytes(payload)
                             ) as response:
                                 mmsd_print(f"Response status: {response.status}", self.verbose)
                                 response.raise_for_status()

                         mmsd_print(f"Message {uuid} sent successfully", self.verbose)
                         break
                    except Exception as e:
                        mmsd_print(f"Error sending message: {str(e)}. Retrying...", self.verbose)
                        await asyncio.sleep(5)
            finally:
                await cleanup_mms_routes()

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
