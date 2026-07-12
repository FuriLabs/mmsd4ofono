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
        return None

    async def setup_routes(self, interface: str, ips: List[str]) -> bool:
        mmsd_print(f"Setting up route for interface: {interface} with ips: {ips}", self.verbose)
        try:
            idx = self.ipr.link_lookup(ifname=interface)[0]
            addrs = self.ipr.get_addr(index=idx)

            # Select a source address that matches the destination address
            # family so the kernel can install a valid route. For IPv6,
            # ignore link-local addresses since they are only reachable on
            # the local link and cannot be used to reach remote MMS servers.
            def global_src(want_ipv6):
                for a in addrs:
                    is_ipv6 = a['family'] == 10
                    if is_ipv6 != want_ipv6:
                        continue
                    if is_ipv6 and a['scope'] == 253:  # RT_SCOPE_LINK
                        continue
                    return a.get_attr('IFA_ADDRESS')
                return None

            # Prefer the MMS-dedicated CLAT instance (clat-mms) set up by
            # clatd handler for this carrier's IPv6-only MMS PDN
            # it's routed specifically to the carrier-private address space the MMSC
            # lives in, unlike the general 'clat' instance (general internet NAT64),
            clat_idx = self.ipr.link_lookup(ifname='clat-mms')
            if not clat_idx:
                clat_idx = self.ipr.link_lookup(ifname='clat')
            clat_idx = clat_idx[0] if clat_idx else None

            ok = False
            for ip in ips:
                is_ipv6 = ':' in ip
                src_addr = global_src(is_ipv6)

                if src_addr is not None:
                    mmsd_print(f"Adding route for {ip} via {interface}", self.verbose)
                    self.ipr.route('add', dst=ip, oif=idx, src=src_addr)
                    self.active_routes.append({'dst': ip, 'oif': idx, 'src': src_addr})
                    ok = True
                elif not is_ipv6 and clat_idx is not None:
                    # No IPv4 on this bearer, but the box's CLAT/NAT64
                    # translator is up - route the IPv4 destination through
                    # it rather than the raw (IPv6-only) cellular interface.
                    mmsd_print(f"Adding route for {ip} via clat (bearer is IPv6-only)", self.verbose)
                    self.ipr.route('add', dst=ip, oif=clat_idx)
                    self.active_routes.append({'dst': ip, 'oif': clat_idx})
                    ok = True
                else:
                    mmsd_print(f"No usable route to {ip}: bearer {interface} has no "
                               f"{'IPv6' if is_ipv6 else 'IPv4'} address"
                               + ("" if is_ipv6 else " and CLAT is not up"), self.verbose)

            return ok
        except Exception as e:
            mmsd_print(f"Failed to setup routes: {e}", self.verbose)
            self.cleanup_routes()
            return False

    def cleanup_routes(self):
        mmsd_print("Cleaning up routes", self.verbose)
        for route in self.active_routes:
            try:
                self.ipr.route('del', **route)
            except Exception as e:
                mmsd_print(f"Failed to remove route: {e}", self.verbose)

        self.active_routes.clear()

    async def handle_client(self, reader, writer):
        try:
            data = await reader.readline()
            request = json.loads(data.decode())

            if request['action'] == 'setup':
                ok = await self.setup_routes(
                    self._get_mms_interface(),
                    request['ips']
                )
                writer.write((json.dumps({'ok': ok}) + "\n").encode())
                await writer.drain()
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
