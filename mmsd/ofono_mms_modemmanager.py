# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next import Variant, DBusError

from mmsd.logging import mmsd_print

import asyncio

class OfonoMMSModemManagerInterface(ServiceInterface):
    def __init__(self, ofono_client, ofono_props, ofono_interfaces, ofono_interface_props, verbose=False):
        super().__init__('org.ofono.mms.ModemManager')
        mmsd_print("Initializing MMS modem manager interface", verbose)
        self.ofono_client = ofono_client
        self.verbose = verbose
        self.ofono_props = ofono_props
        self.ofono_interfaces = ofono_interfaces
        self.ofono_interface_props = ofono_interface_props
        self.props = {
            'CarrierMMSC': Variant('s', 'http://mms.invalid'),
            'MMS_APN': Variant('s', 'apn.invalid'),
            'CarrierMMSProxy': Variant('s', 'NULL'),
            'DefaultModemNumber': Variant('s', 'NULL'),
            'ModemNumber': Variant('s', ''),
            'AutoProcessOnConnection': Variant('b', True),
            'AutoProcessSMSWAP': Variant('b', True)
        }

    async def set_props(self):
        mmsd_print("Setting properties", self.verbose)
        if 'org.ofono.SimManager' in self.ofono_interface_props:
            if 'SubscriberNumbers' in self.ofono_interface_props['org.ofono.SimManager']:
                numbers = self.ofono_interface_props['org.ofono.SimManager']['SubscriberNumbers'].value
            else:
                numbers = []

            if numbers:
                self.props['ModemNumber'] = Variant('s', numbers[0])
                self.props['DefaultModemNumber'] = Variant('s', numbers[0])

        apn, mmsc, proxy = "", "", "NULL"
        if 'org.ofono.ConnectionManager' in self.ofono_interface_props:
            contexts = await self.ofono_interfaces['org.ofono.ConnectionManager'].call_get_contexts()
            for ctx in contexts:
                ctx_type = ctx[1].get('Type', Variant('s', '')).value
                if ctx_type.lower() == "mms":
                    apn = ctx[1].get('AccessPointName', Variant('s', '')).value
                    proxy = ctx[1].get('MessageProxy', Variant('s', '')).value
                    mmsc = ctx[1].get('MessageCenter', Variant('s', '')).value

                    self.props['CarrierMMSC']= Variant('s', mmsc)
                    self.props['MMS_APN']= Variant('s', apn)
                    self.props['CarrierMMSProxy']= Variant('s', proxy)
        self.SettingsChanged(apn, mmsc, proxy)

    @method()
    async def PushNotify(self, smswap: 'ay'):
        mmsd_print(f"Push notify smswap: {smswap}", self.verbose)

    @method()
    async def ViewSettings(self) -> 'a{sv}':
        mmsd_print("View settings", self.verbose)
        return self.props

    @method()
    async def ChangeSettings(self, setting: 's', value: 'v'):
        mmsd_print(f"Changing setting {setting} to {value}", self.verbose)
        if setting in self.props:
            self.props[setting] = value

    @method()
    async def ChangeAllSettings(self, options: 'a{sv}'):
        mmsd_print(f"Changing settings {options}", self.verbose)

    @method()
    async def ProcessMessageQueue(self):
        mmsd_print("Process message queue", self.verbose)

    @signal()
    def BearerHandlerError(self, error) -> 'h':
        mmsd_print(f"Bearer handler error emitted with error {error}", self.verbose)
        return error

    @signal()
    def SettingsChanged(self, apn, mmsc, proxy) -> 'sss':
        mmsd_print(f"Settings changed emitted, APN: {apn}, MMSC, {mmsc}, proxy: {proxy}", self.verbose)
        return [apn, mmsc, proxy]

    @dbus_property(access=PropertyAccess.READ)
    def CarrierMMSC(self) -> 's':
        return self.props['CarrierMMSC'].value

    @dbus_property(access=PropertyAccess.READ)
    def MMS_APN(self) -> 's':
        return self.props['MMS_APN'].value

    @dbus_property(access=PropertyAccess.READ)
    def CarrierMMSProxy(self) -> 's':
        return self.props['CarrierMMSProxy'].value

    @dbus_property(access=PropertyAccess.READ)
    def DefaultModemNumber(self) -> 's':
        return self.props['DefaultModemNumber'].value

    @dbus_property(access=PropertyAccess.READ)
    def ModemNumber(self) -> 's':
        return self.props['ModemNumber'].value

    @dbus_property(access=PropertyAccess.READ)
    def AutoProcessOnConnection(self) -> 'b':
        return self.props['AutoProcessOnConnection'].value

    @dbus_property(access=PropertyAccess.READ)
    def AutoProcessSMSWAP(self) -> 'b':
        return self.props['AutoProcessSMSWAP'].value

    def ofono_changed(self, name, varval):
        self.ofono_props[name] = varval
        asyncio.create_task(self.set_props())

    def ofono_client_changed(self, ofono_client):
        self.ofono_client = ofono_client

    def ofono_interface_changed(self, iface):
        def ch(name, varval):
            if iface in self.ofono_interface_props:
                self.ofono_interface_props[iface][name] = varval
            asyncio.create_task(self.set_props())

        return ch
