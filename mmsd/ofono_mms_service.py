# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2025 Bardia Moshiri <fakeshell@bardia.tech>

from datetime import datetime
from configparser import ConfigParser, Error
from os.path import join, exists, getsize
from string import ascii_letters, digits
from random import choice
from urllib.parse import urlparse
from uuid import uuid4
from re import sub
import asyncio

from aiohttp import ClientSession, ClientTimeout

from dbus_fast.service import ServiceInterface, method, dbus_property, signal
from dbus_fast.constants import PropertyAccess
from dbus_fast import Variant

from mmsd.logging import mmsd_print

from mmsd.route_controller import cleanup_mms_routes, setup_mms_routes
from mmsd.utils import resolve_host
from mmsdecoder.message import MMSMessage, MMSMessagePage, DataPart

# a dead bearer otherwise costs the OS connect timeout, about 30s
CONNECT_TIMEOUT = 8
# the upload needs room, messages run to hundreds of kilobytes
SEND_TIMEOUT = 120
# retrying forever leaves the message reported as sent while it never arrives
SEND_ATTEMPTS = 5
ADDRESS_ATTEMPTS = 3

class OfonoMMSServiceInterface(ServiceInterface):
    def __init__(self, mms_dir, ofono_mms_modemmanager_interface, export_mms_message, update_mms_message, path, verbose=False):
        super().__init__('org.ofono.mms.Service')
        self.modem_name = path
        mmsd_print("Initializing MMS Service interface", verbose)
        self.verbose = verbose
        self.mms_dir = mms_dir
        self.ofono_mms_modemmanager_interface = ofono_mms_modemmanager_interface
        self.export_mms_message = export_mms_message
        self.update_mms_message = update_mms_message
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
        self.load_settings_from_file()

        self.loop = asyncio.get_event_loop()

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

        transaction_id = self.generate_random_string(length=40)
        mms.headers['Transaction-Id'] = transaction_id
        mmsd_print(f"Generated transaction ID: {transaction_id}", self.verbose)

        mms.headers['Content-Type'] = ('application/vnd.wap.multipart.mixed', {})
        mms.headers['Message-Class'] = 'Personal'
        mms.headers['Delivery-Report'] = self.props['UseDeliveryReports'].value

        has_pages = False

        for attachment in attachments:
            content_type = attachment[1].split('/')[0]
            mmsd_print(f"Attachment content type is {content_type}, attachment is {attachment[2]}", self.verbose)
            if content_type == 'text':
                try:
                    with open(attachment[2], 'r', encoding='utf-8') as file:
                        text_content = file.read()
                    text_slide = MMSMessagePage()
                    text_slide.add_text(text_content)
                    mms.add_page(text_slide)
                    has_pages = True
                except Exception as e:
                    mmsd_print(f"Failed to process text attachment: {e}", self.verbose)
            elif content_type == 'image':
                try:
                    image_slide = MMSMessagePage()
                    image_slide.add_image(attachment[2])
                    mms.add_page(image_slide)
                    has_pages = True
                except Exception as e:
                    mmsd_print(f"Failed to process image attachment: {e}", self.verbose)
            elif content_type == 'audio':
                try:
                    audio_slide = MMSMessagePage()
                    audio_slide.add_audio(attachment[2])
                    mms.add_page(audio_slide)
                    has_pages = True
                except Exception as e:
                    mmsd_print(f"Failed to process audio attachment: {e}", self.verbose)
            else:
                try:
                    data_part = DataPart(attachment[2])
                    mms.add_data_part(data_part)
                except Exception as e:
                    mmsd_print(f"Failed to process data part attachment: {e}", self.verbose)

        # without any pages we don't need any smil
        if has_pages:
            mms.headers['Content-Type'] = (
                'application/vnd.wap.multipart.related',
                {'Type': 'application/smil', 'Start': '<0000>'}
            )
            smil = ' '.join(mms.smil().split())
        else:
            smil = ''

        payload = mms.encode()

        return mms, payload, smil, transaction_id

    async def send_message_wrapper(self, payload, uuid):
        await self.send_message(payload, uuid)

    def update_message_status_file(self, uuid, updates):
        status_path = join(self.mms_dir, f"{uuid}.status")
        if not exists(status_path):
            mmsd_print(f"Status file for message {uuid} does not exist", self.verbose)
            return

        with open(status_path, 'r', encoding='utf-8') as status_file:
            lines = status_file.readlines()

        updated_keys = set()
        for index, line in enumerate(lines):
            if '=' not in line:
                continue

            key = line.split('=', 1)[0]
            if key in updates:
                lines[index] = f"{key}={updates[key]}\n"
                updated_keys.add(key)

        for key, value in updates.items():
            if key not in updated_keys:
                lines.append(f"{key}={value}\n")

        with open(status_path, 'w', encoding='utf-8') as status_file:
            status_file.writelines(lines)

    def process_send_conf(self, response_body, uuid):
        if not response_body:
            mmsd_print("MMSC returned an empty response body, keeping the send successful", self.verbose)
            return True

        try:
            response_mms = MMSMessage.from_data(response_body)
        except Exception as e:
            mmsd_print(f"Failed to parse MMSC response body: {e}, keeping the send successful", self.verbose)
            return True

        if response_mms.headers.get('Message-Type') != 'm-send-conf':
            mmsd_print(f"MMSC response is not m-send-conf: {response_mms.headers.get('Message-Type')}, keeping the send successful", self.verbose)
            return True

        message_id = response_mms.headers.get('Message-ID')
        if message_id:
            mmsd_print(f"MMSC assigned Message-ID {message_id} to message {uuid}", self.verbose)
            self.update_message_status_file(uuid, {'id': message_id})

        response_status = response_mms.headers.get('Response-Status')
        if response_status is None:
            mmsd_print("m-send-conf has no Response-Status, keeping the send successful", self.verbose)
            return True

        if response_status == 'Ok':
            return True

        response_text = response_mms.headers.get('Response-Text', '')
        properties = {
            'UUID': Variant('s', uuid),
            'ResponseStatus': Variant('s', str(response_status)),
        }
        if response_text:
            properties['ResponseText'] = Variant('s', str(response_text))

        self.report_send_failure(uuid, properties)
        mmsd_print(f"MMSC rejected message {uuid}: {response_status}", self.verbose)
        return False

    def report_send_failure(self, uuid, properties):
        self.update_message_status_file(uuid, {'state': 'sending_failed'})
        self.update_mms_message(uuid, {'Status': Variant('s', 'sending_failed')})
        self.MessageSendError(properties)

    async def send_message(self, payload, uuid):
        for _ in range(SEND_ATTEMPTS):
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
                    # IPv6 literals must be bracketed in a URL host component (RFC 3986 3.2.2)
                    proxy_host = f"[{proxy_ips[0]}]" if ":" in proxy_ips[0] else proxy_ips[0]
                    resolved_proxy = f"{proxy_host}:{proxy_port}"
                    candidate_ips = proxy_ips

                url_parts = urlparse(mmsc)

                if not proxy:
                    url_ips = await resolve_host(url_parts.hostname, self.verbose)
                    if not url_ips:
                        mmsd_print(f"Failed to resolve URL host: {url_parts.hostname}", self.verbose)
                        await asyncio.sleep(5)
                        continue
                    needed_ips.extend(url_ips)

                    # IPv6 literals must be bracketed in a URL host component (RFC 3986 3.2.2)
                    resolved_host = f"[{url_ips[0]}]" if ":" in url_ips[0] else url_ips[0]
                    resolved_url = url_parts._replace(
                        netloc=resolved_host + (f":{url_parts.port}" if url_parts.port else "")
                    ).geturl()
                    candidate_ips = url_ips
                else:
                    # Leave the URL as is, the proxy will deal with it (hopefully)
                    resolved_url = mmsc

                if not await setup_mms_routes(needed_ips):
                    mmsd_print("Failed to setup MMS routes, retrying...", self.verbose)
                    await asyncio.sleep(5)
                    continue

                # payload is an array('B', [...]), convert it to bytes
                payload = payload if isinstance(payload, bytes) else payload.tobytes()

                async with ClientSession(timeout=ClientTimeout(sock_connect=CONNECT_TIMEOUT, total=SEND_TIMEOUT)) as session:
                    headers = {
                        'Host': url_parts.netloc,
                        'Content-Type': 'application/vnd.wap.mms-message',
                        'User-Agent': 'Android MmsLib/1.0',
                        'Connection': 'Keep-Alive',
                    }
                    sent = False
                    for ip in candidate_ips:
                        host = f"[{ip}]" if ":" in ip else ip
                        if proxy:
                            current_proxy = f"{host}:{proxy_port}"
                            current_url = resolved_url
                        else:
                            current_proxy = None
                            current_url = url_parts._replace(
                                netloc=host + (f":{url_parts.port}" if url_parts.port else "")
                            ).geturl()

                        for attempt in range(ADDRESS_ATTEMPTS):
                            try:
                                mmsd_print(f"Sending message to: {current_url} using proxy: {current_proxy} (attempt {attempt + 1}/{ADDRESS_ATTEMPTS})", self.verbose)
                                if current_proxy:
                                    request = session.post(
                                        current_url, proxy=f"http://{current_proxy}",
                                        headers=headers, data=payload,
                                        skip_auto_headers=['User-Agent']
                                    )
                                else:
                                    request = session.post(current_url, headers=headers, data=bytes(payload))

                                async with request as response:
                                    mmsd_print(f"Response status: {response.status}", self.verbose)
                                    response.raise_for_status()
                                    response_body = await response.read()

                                if self.process_send_conf(response_body, uuid):
                                    mmsd_print(f"Message {uuid} sent successfully", self.verbose)
                                sent = True
                                break
                            except Exception as e:
                                mmsd_print(f"Error sending message: {e}", self.verbose)
                                if attempt < ADDRESS_ATTEMPTS - 1:
                                    await asyncio.sleep(5)
                        if sent:
                            break
                    if sent:
                        return
                    mmsd_print("All MMS addresses failed", self.verbose)
                    break
            finally:
                await cleanup_mms_routes()

        properties = {
            'UUID': Variant('s', uuid),
            'ResponseStatus': Variant('s', 'Error-transient-network-problem'),
        }

        self.report_send_failure(uuid, properties)
        mmsd_print(f"Giving up on message {uuid}", self.verbose)

    def load_settings_from_file(self):
        if not exists(self.mms_config_file):
            return

        config = ConfigParser(interpolation=None)
        config.optionxform = str

        try:
            with open(self.mms_config_file, 'r', encoding='utf-8') as f:
                config.read_file(f)
        except (OSError, Error) as e:
            mmsd_print(f"Failed to load settings from {self.mms_config_file}: {e}", self.verbose)
            return

        if not config.has_section('Settings'):
            return

        settings = config['Settings']
        boolean_properties = {
            'UseDeliveryReports',
            'AutoCreateSMIL',
            'ForceCAres'
        }
        integer_properties = {
            'TotalMaxAttachmentSize',
            'MaxAttachments',
            'NotificationInds'
        }

        for prop in boolean_properties:
            if prop not in settings:
                continue
            try:
                self.props[prop] = Variant('b', settings.getboolean(prop))
            except ValueError:
                mmsd_print(f"Invalid boolean value for {prop} in {self.mms_config_file}, keeping default", self.verbose)

        for prop in integer_properties:
            if prop not in settings:
                continue
            try:
                self.props[prop] = Variant('i', settings.getint(prop))
            except ValueError:
                mmsd_print(f"Invalid integer value for {prop} in {self.mms_config_file}, keeping default", self.verbose)

    def save_settings_to_file(self):
        mmsd_print(f"Saving settings to file {self.mms_config_file}", self.verbose)

        settings_section = '[Settings]\n'
        settings_content = ''.join(f'{key}={variant.value}\n' for key, variant in self.props.items())

        if exists(self.mms_config_file):
            with open(self.mms_config_file, 'r', encoding='utf-8') as f:
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

        with open(self.mms_config_file, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

    def create_message_files(self, pdu, uuid, date, transaction_id, delivery_report, delivery_status):
        mmsd_print(f"Saving message {uuid} to disk", self.verbose)
        pdu_path = join(self.mms_dir, uuid)
        with open(pdu_path, 'wb') as pdu_file:
            pdu_file.write(pdu)

        status_path = join(self.mms_dir, f"{uuid}.status")
        with open(status_path, 'w', encoding='utf-8') as status_file:
            status_file.write("[info]\n")
            status_file.write("read=false\n")
            status_file.write("state=sent\n")
            status_file.write(f"id={transaction_id}\n")
            status_file.write(f"date={date}\n")
            status_file.write(f"delivery_report={str(delivery_report).lower()}\n")
            status_file.write(f"delivery_status={delivery_status}\n")

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

        _mms, payload, smil, transaction_id = self.build_message(recipients, attachments)
        delivery_report = self.props['UseDeliveryReports'].value
        delivery_recipients = [sub(r'[^0-9+]', '', recipient) for recipient in recipients]
        delivery_status = ','.join(f"{recipient}=none" for recipient in delivery_recipients) if delivery_report else ''
        self.loop.create_task(self.send_message_wrapper(payload, uuid))
        date = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.create_message_files(payload, uuid, date, transaction_id, delivery_report, delivery_status)
        object_path = self.export_mms_message(
            uuid,
            'sent',
            date,
            self.ofono_mms_modemmanager_interface.props['ModemNumber'].value,
            delivery_report,
            recipients,
            smil,
            attachments,
            delivery_status
        )

        return object_path

    @method()
    def SetProperty(self, prop: 's', value: 'v'):
        mmsd_print(f"Setting property {prop} to value {value}", self.verbose)
        if prop in self.props:
            self.props[prop] = value
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
