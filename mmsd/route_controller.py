import asyncio
from typing import List
import json

from mmsd.logging import mmsd_print

_ROUTE_DAEMON_PATH = "/run/mmsroutectl.sock"

async def setup_mms_routes(ips: List[str]) -> bool:
    try:
        reader, writer = await asyncio.open_unix_connection(_ROUTE_DAEMON_PATH)
        request = {
            'action': 'setup',
            'ips': ips
        }

        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()

        response = await asyncio.wait_for(reader.readline(), timeout=5.0)

        writer.close()
        await writer.wait_closed()

        if not response:
            mmsd_print("Route daemon closed the connection without a response", True)
            return False

        return bool(json.loads(response.decode()).get('ok'))

    except Exception as e:
        mmsd_print(f"Failed to setup MMS routes: {e}", True)
        return False

async def cleanup_mms_routes() -> None:
    try:
        _, writer = await asyncio.open_unix_connection(_ROUTE_DAEMON_PATH)
        request = {'action': 'cleanup'}

        writer.write(json.dumps(request).encode())
        await writer.drain()

        writer.close()
        await writer.wait_closed()

    except Exception as e:
        mmsd_print(f"Failed to cleanup MMS routes: {e}", True)
