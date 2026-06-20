"""Synchronous CONEX-AGP controller logic, refactored from the verified ``stage.py``.

The exact command strings and the ``TS`` state-code mapping that were validated
against real hardware are preserved verbatim.  The only structural change is that
this class talks to a :class:`~stage_api.transport.Transport` instead of owning a
``serial.Serial`` directly, so the same logic drives both hardware and the
emulator.  A second deliberate change is robustness: every action command is
followed by a ``TE`` read so silently-rejected commands raise instead of looking
like success (the gap in the original script).

All methods here are *blocking*; the async API layer runs them in a worker thread
under the port lock.
"""

from __future__ import annotations

from enum import Enum

from .errors import ControllerError
from .transport import Transport


class ControllerState(Enum):
    NOT_REFERENCED = 1
    CONFIGURATION = 2
    HOMING = 3
    MOVING = 4
    READY = 5
    DISABLE = 6


# TS low-byte -> logical state.  Identical grouping to the verified stage.py.
_STATE_BY_CODE: dict[str, ControllerState] = {
    **{c: ControllerState.NOT_REFERENCED for c in
       ("0A", "0B", "0C", "0D", "0E", "0F", "10")},
    "14": ControllerState.CONFIGURATION,
    "1E": ControllerState.HOMING,
    "28": ControllerState.MOVING,
    **{c: ControllerState.READY for c in ("32", "33", "34")},
    **{c: ControllerState.DISABLE for c in ("3C", "3D")},
}


class StageController:
    """One CONEX-AGP axis on a shared transport, addressed by ``address``."""

    def __init__(self, transport: Transport, address: int = 1):
        self._transport = transport
        self._address = address

    @property
    def address(self) -> int:
        return self._address

    # -- low level framing (preserved from stage.py) ------------------------
    def write(self, command: str) -> None:
        cmd = f"{self._address}{command}\r\n"
        self._transport.write(cmd.encode("ascii"))

    def _readline(self) -> str:
        return self._transport.readline().decode("ascii", "replace").strip()

    def _query(self, code: str) -> str:
        """Send a query and return the value after the echoed ``{address}{code}``.

        Stripping the echoed prefix is equivalent to the original ``resp[3:]``
        slice for the verified single-digit-address case, but stays correct for
        two-digit RS-485 addresses too.
        """
        self.write(code)
        resp = self._readline()
        prefix = f"{self._address}{code[:2]}"
        if not resp.startswith(prefix):
            # Unexpected reply (or empty on timeout): surface the real cause.
            self._check_error()
            raise ControllerError("S", f"No/invalid response to {code!r}: {resp!r}")
        return resp[len(prefix):]

    def _check_error(self) -> None:
        """Read the memorised error (TE); raise if anything other than '@'."""
        self.write("TE")
        resp = self._readline()
        prefix = f"{self._address}TE"
        code = resp[len(prefix):].strip() if resp.startswith(prefix) else ""
        if code and code != "@":
            raise ControllerError(code)

    def _action(self, command: str) -> None:
        """Issue a command that returns no value, then verify via TE."""
        self.write(command)
        self._check_error()

    # -- queries -------------------------------------------------------------
    @property
    def state(self) -> ControllerState:
        payload = self._query("TS")  # "<4 hex error><2 hex state>"
        code = payload[-2:].upper()
        try:
            return _STATE_BY_CODE[code]
        except KeyError:
            raise ControllerError("V", f"Unknown controller state code {code!r}")

    @property
    def fault_flags(self) -> str:
        """The 4-hex 'positioner error' field from TS ('0000' when clear)."""
        payload = self._query("TS")
        return payload[:-2].upper()

    @property
    def position(self) -> float:
        return float(self._query("TP"))

    @property
    def target(self) -> float:
        return float(self._query("TH"))

    @property
    def is_referenced(self) -> bool:
        return self.state not in (
            ControllerState.NOT_REFERENCED,
            ControllerState.CONFIGURATION,
        )

    def limits(self) -> tuple[float, float]:
        low = float(self._query("SL?"))
        high = float(self._query("SR?"))
        return low, high

    def info(self) -> dict[str, str]:
        return {"version": self._query("VE"), "model": self._query("ID?")}

    # -- actions -------------------------------------------------------------
    def home(self) -> None:
        self._action("OR")

    def move_abs(self, position: float) -> None:
        self._action(f"PA{position}")

    def move_rel(self, displacement: float) -> None:
        self._action(f"PR{displacement}")

    def stop(self) -> None:
        self._action("ST")

    def reset(self) -> None:
        # RS reboots the controller; it returns no error frame to verify.
        self.write("RS")

    def disable(self) -> None:
        self._action("MM0")

    def enable(self) -> None:
        self._action("MM1")
