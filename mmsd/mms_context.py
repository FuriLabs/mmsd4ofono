# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2025 Bardia Moshiri <fakeshell@bardia.tech>

import asyncio

from mmsd.logging import mmsd_print

# activation sometimes answers "Operation failed", a few attempts are not enough
ACTIVATION_ATTEMPTS = 10
# oFono reports Active before Settings carries an Interface
READY_TIMEOUT = 20
# don't drop the bearer between a send and the fetch that follows it
LINGER = 30

class MMSContext:
    def __init__(self, activate, deactivate, verbose=False):
        self.activate = activate
        self.deactivate = deactivate
        self.verbose = verbose
        self.users = 0
        self.ready = asyncio.Event()
        self.linger_task = None
        self.restore_task = None
        self.reconfiguring = False

    def is_ready(self):
        return self.ready.is_set()

    def settings_carry_interface(self, settings):
        interface = settings.get('Interface')
        return bool(interface and interface.value)

    def context_changed(self, prop, propvalue):
        if prop == 'Settings':
            if self.settings_carry_interface(propvalue.value):
                self.ready.set()
            else:
                self.ready.clear()
        elif prop == 'Active' and not propvalue.value:
            self.ready.clear()
            if self.users > 0 and not self.reconfiguring:
                self.restore()

    def wants_context(self):
        return self.users > 0

    def adopt(self, settings):
        """Without this a context oFono already had up stays active with
        nobody accounting for it, until something else takes it down."""
        if not self.settings_carry_interface(settings):
            return

        self.ready.set()
        self.schedule_teardown()

    def restore(self):
        if self.restore_task and not self.restore_task.done():
            return

        mmsd_print("MMS context dropped while still needed, bringing it back", self.verbose)
        self.restore_task = asyncio.get_event_loop().create_task(self.ensure_up())

    def cancel_linger(self):
        if self.linger_task and not self.linger_task.done():
            self.linger_task.cancel()
        self.linger_task = None

    async def ensure_up(self):
        if self.ready.is_set():
            return True

        for attempt in range(ACTIVATION_ATTEMPTS):
            mmsd_print(f"Bringing up the MMS context, attempt {attempt + 1}", self.verbose)
            await self.activate()

            try:
                await asyncio.wait_for(self.ready.wait(), timeout=READY_TIMEOUT)
                mmsd_print("MMS context is up", self.verbose)
                return True
            except asyncio.TimeoutError:
                mmsd_print("MMS context did not come up in time", self.verbose)

        return False

    async def acquire(self):
        self.users += 1
        self.cancel_linger()

        if await self.ensure_up():
            return True

        self.users -= 1
        return False

    async def reconfigure(self, apply):
        """oFono only accepts these while the bearer is down. Counting as a
        user keeps the teardown and the drop handler from racing the change."""
        self.reconfiguring = True
        self.users += 1
        self.cancel_linger()

        try:
            self.ready.clear()
            await self.deactivate()
            await apply()
        finally:
            self.users -= 1
            self.reconfiguring = False

        if self.users > 0:
            return await self.ensure_up()

        self.schedule_teardown()
        return True

    async def release(self):
        if self.users > 0:
            self.users -= 1

        self.schedule_teardown()

    def schedule_teardown(self):
        if self.users > 0:
            return

        if self.linger_task and not self.linger_task.done():
            return

        self.linger_task = asyncio.get_event_loop().create_task(self.linger_then_deactivate())

    async def linger_then_deactivate(self):
        try:
            await asyncio.sleep(LINGER)
        except asyncio.CancelledError:
            return

        if self.users > 0:
            return

        mmsd_print("Nothing left needs the MMS context, taking it down", self.verbose)
        self.ready.clear()
        await self.deactivate()
