# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

import array
import asyncio

from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import Variant, DBusError

from mmsd.logging import mmsd_print

from mmsdecoder.message import MMSMessage

class OfonoPushNotification(ServiceInterface):
    def __init__(self, bus, ofono_client, ofono_props, ofono_interfaces, ofono_interface_props, verbose=False):
        super().__init__("org.ofono.PushNotificationAgent")
        self.bus = bus
        self.verbose = verbose
        self.ofono_client = ofono_client
        self.verbose = verbose
        self.ofono_props = ofono_props
        self.ofono_interfaces = ofono_interfaces
        self.ofono_interface_props = ofono_interface_props
        self.push_channel = asyncio.Queue()
        self.message_channel = asyncio.Queue()
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
        data = array.array("B", notification)
        mms = MMSMessage.from_data(data)
        mmsd_print(f"Received MMS: {mms.headers}, info: {info}", self.verbose)
        await self.push_channel.put((mms.headers, info))

    @method()
    async def Release(self):
        mmsd_print(f"Agent released on path {self.agent_path}", self.verbose)
        self.registered = False
        self.bus.unexport(self.agent_path)
