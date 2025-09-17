# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2025 Bardia Moshiri <fakeshell@bardia.tech>

import asyncio
import socket
from typing import List

from mmsd.logging import mmsd_print

async def resolve_host(hostname: str, verbose) -> List[str]:
    try:
        addrinfo = await asyncio.get_event_loop().getaddrinfo(
            hostname, None, family=socket.AF_INET
        )
        return list(set(addr[4][0] for addr in addrinfo))
    except Exception as e:
        mmsd_print(f"Failed to resolve {hostname}: {e}", verbose)
        return []
