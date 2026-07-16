# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2025 Bardia Moshiri <fakeshell@bardia.tech>

import asyncio
import socket
from typing import List

from mmsd.logging import mmsd_print

async def resolve_host(hostname: str, verbose) -> List[str]:
    try:
        addrinfo = await asyncio.get_event_loop().getaddrinfo(
            hostname, None, family=socket.AF_UNSPEC
        )
        addrs = list(dict.fromkeys(addr[4][0] for addr in addrinfo))
        ipv6 = [a for a in addrs if ":" in a]
        ipv4 = [a for a in addrs if ":" not in a]
        return ipv6 + ipv4
    except Exception as e:
        mmsd_print(f"Failed to resolve {hostname}: {e}", verbose)
        return []

def sanitize_dbus_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    else:
        value = str(value)

    value = value.encode("utf-8", errors="replace").decode("utf-8", errors="replace")

    # strip NULs
    value = value.replace("\x00", "")

    return value
