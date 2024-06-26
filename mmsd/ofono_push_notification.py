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
from mmsdecoder.wsp_pdu import WELL_KNOWN_CONTENT_TYPES

class OfonoPushNotification(ServiceInterface):
    def __init__(self, bus, ofono_client, ofono_props, ofono_interfaces, ofono_interface_props, mms_dir, verbose=False):
        super().__init__("org.ofono.PushNotificationAgent")
        self.bus = bus
        self.verbose = verbose
        self.ofono_client = ofono_client
        self.verbose = verbose
        self.ofono_props = ofono_props
        self.ofono_interfaces = ofono_interfaces
        self.ofono_interface_props = ofono_interface_props
        self.mms_dir = mms_dir
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

        content_type = None
        for key, value in mms.headers.items():
            if value in WELL_KNOWN_CONTENT_TYPES:
                content_type = value

        mmsd_print(f"Received MMS: {mms.headers}, info: {info}", self.verbose)

        message_type = mms.headers.get('Message-Type')
        transaction_id = mms.headers.get('Transaction-Id')
        mms_version = mms.headers.get('MMS-Version')
        wap_application_id = mms.headers.get('X-Wap-Application-Id')
        message_class = mms.headers.get('Message-Class')
        message_size = mms.headers.get('Message-Size')
        expiry = mms.headers.get('Expiry')
        content_location = mms.headers.get('Content-Location')
        sender = mms.headers.get('From')

        mmsd_print(f"Message-Type: {message_type}, Transaction-Id: {transaction_id}, MMS-Version: {mms_version}, X-Wap-Application-Id: {wap_application_id}, Message-Class: {message_class}, Message-Size: {message_size}, Expiry: {expiry}, Content-Location: {content_location}, From: {sender}", self.verbose)
        if content_type:
            mmsd_print(f"Content-Type: {content_type}", self.verbose)

        proxy = ''
        proxy = await self.get_mms_context_info()
        if proxy != '':
            proxies = {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
            try:
                response = get(content_location, proxies=proxies)
                if response.status_code == 200:
                    uuid = str(uuid4())
                    smil_path = join(self.mms_dir, uuid)
                    with open(smil_path, 'wb') as file:
                        file.write(response.content)
                    mmsd_print(f"SMIL successfully saved to {smil_path}", self.verbose)
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
