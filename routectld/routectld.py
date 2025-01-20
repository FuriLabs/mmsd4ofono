#!/usr/bin/env python3

import asyncio
import json
import pwd
import sys
from argparse import ArgumentParser
from os import chown, chmod, environ, unlink
from os.path import realpath, join, dirname
from pathlib import Path
from typing import List

import dbus
import pyroute2

topdir = realpath(join(dirname(__file__) + "/.."))
sys.path.insert(0, topdir)

mmsd_dir = "/usr/lib/mmsd"
sys.path.insert(0, mmsd_dir)

from mmsd.logging import mmsd_print

class MMSRouteController:
    def __init__(self, verbose=False):
        mmsd_print("Initializing MMS route controller", verbose)
        self.verbose = verbose
        self.socket_path = "/run/mmsroutectl.sock"
        self.allowed_uid = None
        self.ipr = pyroute2.IPRoute()
        self.active_routes = []

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
        mmsd_print(f"Setting up route for interface: {interface} with ips: {ips}", self.verbose)
        try:
            idx = self.ipr.link_lookup(ifname=interface)[0]

            addrs = self.ipr.get_addr(index=idx)
            if not addrs:
                return False
            src_addr = [x.get_attr('IFA_ADDRESS') for x in addrs][0]

            for ip in ips:
                mmsd_print(f"Adding route for {ip} via {interface}", self.verbose)
                self.ipr.route('add', dst=ip, oif=idx, src=src_addr)
                self.active_routes.append({
                    'dst': ip,
                    'oif': idx,
                    'src': src_addr
                })
            return True
        except Exception as e:
            mmsd_print(f"Failed to setup routes: {e}", self.verbose)
            self.cleanup_routes()
            return False

    def cleanup_routes(self):
        mmsd_print(f"Cleaning up routes", self.verbose)
        for route in self.active_routes:
            try:
                self.ipr.route('del', **route)
            except Exception as e:
                mmsd_print(f"Failed to remove route: {e}", self.verbose)

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
            mmsd_print(f"Error handling client: {e}", self.verbose)
            writer.close()

    async def run(self):
        socket_dir = dirname(self.socket_path)
        Path(socket_dir).mkdir(parents=True, exist_ok=True)

        try:
            unlink(self.socket_path)
        except FileNotFoundError:
            pass

        server = await asyncio.start_unix_server(
            self.handle_client,
            path=self.socket_path
        )

        chmod(self.socket_path, 0o660)
        chown(self.socket_path, 0, pwd.getpwnam('furios').pw_uid)

        async with server:
            await server.serve_forever()

def main():
    # Disable buffering for stdout and stderr so that logs are written immediately
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    parser = ArgumentParser(description="Run the MMS route controller", add_help=False)
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output.')
    args = parser.parse_args()

    if environ.get('MODEM_DEBUG', 'false').lower() == 'true':
        verbose = True
    else:
        verbose = args.verbose

    controller = MMSRouteController(verbose=verbose)
    asyncio.run(controller.run())

if __name__ == "__main__":
    main()
