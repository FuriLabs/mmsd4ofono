# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from os.path import join
from requests import get
from array import array
from uuid import uuid4
from re import sub
import asyncio

from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import Variant, DBusError

from mmsd.logging import mmsd_print

from mmsdecoder.message import MMSMessage

class OfonoPushNotification(ServiceInterface):
    def __init__(self, bus, ofono_client, ofono_props, ofono_interfaces, ofono_interface_props, mms_dir, export_mms_message, verbose=False):
        super().__init__("org.ofono.PushNotificationAgent")
        self.bus = bus
        self.verbose = verbose
        self.ofono_client = ofono_client
        self.verbose = verbose
        self.ofono_props = ofono_props
        self.ofono_interfaces = ofono_interfaces
        self.ofono_interface_props = ofono_interface_props
        self.mms_dir = mms_dir
        self.export_mms_message = export_mms_message
        self.agent_path = False
        self.registered = False

    async def RegisterAgent(self, path: 'o'):
        if self.registered:
            mmsd_print(f"Agent already registered at path {path}", self.verbose)
            return

        await self.ofono_interfaces['org.ofono.PushNotification'].call_register_agent(path)
        self.bus.export(path, self)

        self.agent_path = path
        self.registered = True
        mmsd_print(f"Agent Registered at path {path}", self.verbose)

    async def UnregisterAgent(self, path: 'o'):
        if not self.registered:
            mmsd_print(f"Agent not registered at path {path}", self.verbose)
            return

        await self.ofono_interfaces['org.ofono.PushNotification'].call_unregister_agent(path)

        self.agent_path = False
        self.registered = False
        mmsd_print(f"Agent Unregistered at path {path}", self.verbose)

    @method()
    async def ReceiveNotification(self, notification: 'ay', info: 'a{sv}'):
        data = array("B", notification)
        mms = MMSMessage.from_data(data)
        for key, value in mms.headers.items():
            if isinstance(value, str):
                mms.headers[key] = sub(r'[^\x20-\x7E]+', '', value).replace('"', '')

        mmsd_print(f"Received MMS: {mms.headers}, info: {info}", self.verbose)

        transaction_id = mms.headers.get('Transaction-Id')
        content_location = mms.headers.get('Content-Location')
        sender = mms.headers.get('From')

        mmsd_print(f"Transaction-Id: {transaction_id}, Content-Location: {content_location}, From: {sender}", self.verbose)

        proxy = ''
        proxy = await self.get_mms_context_info()
        if proxy != '':
            proxies = {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
            try:
                response = get(content_location, proxies=proxies)
                if response.status_code == 200:
                    uuid = str(uuid4())
                    smil_path = join(self.mms_dir, uuid)
                    status_path = join(self.mms_dir, f"{uuid}.status")
                    headers_path = join(self.mms_dir, f"{uuid}.headers")

                    with open(smil_path, 'wb') as file:
                        file.write(response.content)
                        mmsd_print(f"SMIL successfully saved to {smil_path}", self.verbose)

                    mms_smil = MMSMessage.from_data(response.content)

                    with open(headers_path, 'w') as headers_file:
                        for header_key, header_value in mms_smil.headers.items():
                            headers_file.write(f"{header_key}={header_value}\n")
                        mmsd_print(f"Headers successfully saved to {headers_path}", self.verbose)

                    sent_time = info['SentTime'].value if 'SentTime' in info else ''
                    message_id = mms_smil.headers.get('Transaction-Id') or mms_smil.headers.get('Message-ID') or transaction_id or ''

                    meta_info = f"""
[info]
read=false
state=received
id={message_id}
date={sent_time}
                    """

                    with open(status_path, 'w') as status_file:
                        status_file.write(meta_info)
                        mmsd_print(f"Meta info successfully saved to {status_path}", self.verbose)

                    attachments = []
                    for index, part in enumerate(mms_smil.data_parts):
                        attachment_path = join(self.mms_dir, f"{uuid}.attachment.{index}")
                        with open(attachment_path, 'wb') as file:
                            mmsd_print(f"Writing attachment with index {index} of content type {part.content_type} to {attachment_path}", self.verbose)
                            file.write(part.data)

                        attachment_info = [str(index), part.content_type, attachment_path, 0, len(part.data)]
                        attachments.append(attachment_info)

                        if 'application/smil' in part.content_type:
                            smil_data = part.data.decode('utf-8')

                    if smil_data:
                        recipients = [] # need a way to check if its a group, then query for recipients
                        sender_number = sender.split('/')[0]
                        self.export_mms_message('received', sent_time, sender_number, mms_smil.headers.get('Delivery-Report'), recipients, smil_data, attachments)
                else:
                    mmsd_print(f"Failed to retrieve SMIL. Status code: {response.status_code}", self.verbose)
            except Exception as e:
                mmsd_print(f"Failed to download SMIL: {e}", self.verbose)
                pass # we should handle messages that could not be fetched from message center
        else:
            mmsd_print(f"Could not pull down the mms message. proxy is empty", self.verbose)

    @method()
    async def Release(self):
        mmsd_print(f"Agent released on path {self.agent_path}", self.verbose)
        self.registered = False
        self.bus.unexport(self.agent_path)

    async def get_mms_context_info(self):
        proxy = ''
        if 'org.ofono.ConnectionManager' in self.ofono_interfaces:
            contexts = await self.ofono_interfaces['org.ofono.ConnectionManager'].call_get_contexts()
            for ctx in contexts:
                name = ctx[1].get('Type', Variant('s', '')).value
                if name.lower() == "mms":
                    proxy = ctx[1]['MessageProxy'].value
        return proxy

    def ofono_changed(self, name, varval):
        self.ofono_props[name] = varval

    def ofono_client_changed(self, ofono_client):
        self.ofono_client = ofono_client

    def ofono_interface_changed(self, iface):
        def ch(name, varval):
            if iface in self.ofono_interface_props:
                self.ofono_interface_props[iface][name] = varval

        return ch
