#!/usr/bin/env python3

import asyncio
import json
import os
import logging
from typing import List
import pyroute2
from pathlib import Path
import dbus
import pwd

class MMSRouteController:
    def __init__(self):
        self.socket_path = "/run/mmsroutectl.sock"
        self.allowed_uid = None
        self.ipr = pyroute2.IPRoute()
        self.active_routes = []
        self.logger = logging.getLogger("mmsroutectl")

    def _get_mms_interface(self) -> str:
        bus = dbus.SystemBus()
        manager = dbus.Interface(bus.get_object('org.ofono', '/'), 'org.ofono.Manager')
        modems = manager.GetModems()

        for path, properties in modems:
            if "org.ofono.ConnectionManager" not in properties['Interfaces']:
                continue

            connman = dbus.Interface(bus.get_object('org.ofono', path), 'org.ofono.ConnectionManager')
            contexts = connman.GetContexts()

            for path, properties in contexts:
                if properties['Type'] == 'mms':
                    if "Settings" in properties:
                        return properties["Settings"]["Interface"]
                    elif "IPv6.Settings" in properties:
                        return properties["IPv6.Settings"]["Interface"]

    async def setup_routes(self, interface: str, ips: List[str]) -> bool:
        try:
            idx = self.ipr.link_lookup(ifname=interface)[0]

            addrs = self.ipr.get_addr(index=idx)
            if not addrs:
                return False
            src_addr = [x.get_attr('IFA_ADDRESS') for x in addrs][0]

            for ip in ips:
                self.logger.info(f"Adding route for {ip} via {interface}")
                self.ipr.route('add', dst=ip, oif=idx, src=src_addr)
                self.active_routes.append({
                    'dst': ip,
                    'oif': idx,
                    'src': src_addr
                })
            return True

        except Exception as e:
            self.logger.error(f"Failed to setup routes: {e}")
            self.cleanup_routes()
            return False

    def cleanup_routes(self):
        for route in self.active_routes:
            try:
                self.ipr.route('del', **route)
            except Exception as e:
                self.logger.error(f"Failed to remove route: {e}")

        self.active_routes.clear()

    async def handle_client(self, reader, writer):
        try:
            data = await reader.read()
            request = json.loads(data.decode())

            if request['action'] == 'setup':
                await self.setup_routes(
                    self._get_mms_interface(),
                    request['ips']
                )
            elif request['action'] == 'cleanup':
                self.cleanup_routes()

            writer.close()

        except Exception as e:
            self.logger.error(f"Error handling client: {e}")
            writer.close()

    async def run(self):
        socket_dir = os.path.dirname(self.socket_path)
        Path(socket_dir).mkdir(parents=True, exist_ok=True)

        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

        server = await asyncio.start_unix_server(
            self.handle_client,
            path=self.socket_path
        )

        os.chmod(self.socket_path, 0o660)
        os.chown(self.socket_path, 0, pwd.getpwnam('furios').pw_uid)

        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    controller = MMSRouteController()
    asyncio.run(controller.run())
