"""Stage service: owns the single stage's transport, lock, and controller.

Responsibilities
----------------
* Build one :class:`~stage_api.transport.Transport` for the configured stage
  (real serial or in-process emulator).
* Guard it with one ``asyncio.Lock`` so concurrent requests can never interleave
  a write/read transaction on the serial port.
* Offload the blocking ``pyserial`` calls to a worker thread.
* Fail soft: a device that will not open marks the stage unavailable (503) and is
  lazily reconnected on the next request.

This container controls exactly one stage; for several stages, run several
instances.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, TypeVar

from .config import Config
from .controller import ControllerState, StageController
from .emulator import EmulatedController
from .errors import StageUnavailable
from .transport import EmulatorTransport, SerialTransport, Transport

log = logging.getLogger("stage_api")

T = TypeVar("T")


class StageService:
    def __init__(self, config: Config):
        self._config = config
        self._lock = asyncio.Lock()
        self.connected = False
        self.last_error: str | None = None

        self._transport: Transport = self._build_transport(config)
        self._controller = StageController(self._transport, config.address)

    # -- construction --------------------------------------------------------
    @staticmethod
    def _build_transport(config: Config) -> Transport:
        if config.is_emulated:
            transport = EmulatorTransport()
            transport.add_controller(
                EmulatedController(
                    config.address,
                    travel=config.travel or (0.0, 27.0),
                    speed=config.speed,
                    home_position=config.home_position,
                    home_time=config.home_time,
                )
            )
            return transport
        return SerialTransport(config.device)

    # -- lifecycle -----------------------------------------------------------
    async def startup(self) -> None:
        """Open the port (fail-soft) and auto-home if configured."""
        try:
            await self._ensure_open()
        except StageUnavailable as exc:
            log.warning("stage unavailable at startup: %s", exc.detail)
        if self.connected and self._config.auto_home:
            try:
                await self.home()
                log.info("auto-homed stage")
            except Exception as exc:  # noqa: BLE001 - never block startup
                log.warning("auto-home failed: %s", exc)

    async def shutdown(self) -> None:
        await asyncio.to_thread(self._transport.close)
        log.info("closed port %s", self._config.device)

    async def _ensure_open(self) -> None:
        if self._transport.is_open:
            self.connected = True
            return
        try:
            await asyncio.to_thread(self._transport.open)
            self.connected = True
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.connected = False
            self.last_error = str(exc)
            raise StageUnavailable(str(exc))

    # -- transaction primitive ----------------------------------------------
    async def transact(self, fn: Callable[[StageController], T]) -> T:
        """Run ``fn(controller)`` under the device lock, in a worker thread.

        Lazily (re)opens the port first; on a serial-layer failure the port is
        closed so the next call transparently reconnects (recovers a port that
        was reopened on the same node).
        """
        await self._ensure_open()
        async with self._lock:
            try:
                return await asyncio.to_thread(fn, self._controller)
            except (OSError,) as exc:  # serial dropped mid-transaction
                self.connected = False
                self.last_error = str(exc)
                await asyncio.to_thread(self._transport.close)
                raise StageUnavailable(str(exc))

    # -- high-level operations ----------------------------------------------
    async def state(self) -> ControllerState:
        return await self.transact(lambda c: c.state)

    async def position(self) -> float:
        return await self.transact(lambda c: c.position)

    async def home(self) -> None:
        await self.transact(lambda c: c.home())

    async def wait_ready(self, timeout: float) -> ControllerState:
        """Poll TS until the stage leaves MOVING/HOMING, releasing the lock between polls.

        Releasing per poll means a long ``?wait`` never starves a concurrent
        ``/state`` read.
        """
        deadline = time.monotonic() + timeout
        while True:
            st = await self.state()
            if st not in (ControllerState.MOVING, ControllerState.HOMING):
                return st
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"stage did not become ready within {timeout}s"
                )
            await asyncio.sleep(self._config.poll_interval)

    # -- limits --------------------------------------------------------------
    async def limits(self) -> tuple[float, float]:
        """Configured travel takes precedence; otherwise query the controller once."""
        if self._config.travel is not None:
            return self._config.travel
        return await self.transact(lambda c: c.limits())

    @property
    def config(self) -> Config:
        return self._config
