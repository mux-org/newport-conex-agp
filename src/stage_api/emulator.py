"""Protocol-level emulator of a CONEX-AGP controller.

This object speaks the *real* ASCII protocol: it consumes command bodies such as
``PA13.5`` / ``TS`` / ``TP`` and returns the bytes a real controller would, so the
:class:`~stage_api.controller.StageController` framing and parsing code runs
unchanged against it.  It maintains the documented six-state machine, simulates
motion over wall-clock time, enforces software limits, and memorises the same
``TE`` error codes for illegal commands (section 2.1 / 2.4 of the manual).

Behaviour is *nominal only*: it faithfully accepts valid commands and rejects
invalid ones, but it never fabricates faults (timeouts, stuck moves) that would
not naturally occur.
"""

from __future__ import annotations

import time

# TS state codes (low byte), per the "MM" controller-state table.  We keep the
# precise sub-codes the real firmware uses so the controller's parser is fully
# exercised; the parser groups them into the six logical states.
_NOT_REFERENCED_RESET = "0A"
_HOMING = "1E"
_MOVING = "28"
_READY_FROM_HOMING = "32"
_READY_FROM_MOVING = "33"
_READY_FROM_DISABLE = "34"
_DISABLE_FROM_READY = "3C"

_NO_FAULT = "0000"  # the 4 hex "positioner error" nibbles preceding the state


def _fmt(value: float) -> str:
    """Format a numeric value the way the controller reports positions/limits."""
    return f"{value:.6f}"


class EmulatedController:
    def __init__(
        self,
        address: int = 1,
        *,
        travel: tuple[float, float] = (0.0, 27.0),
        speed: float = 5.0,
        home_position: float = 0.0,
        home_time: float = 1.5,
        version: str = "CONEX-AGP-EMU 1.1.0",
        model: str = "CONEX-AGP",
    ):
        self.address = address
        self._sl, self._sr = travel
        self._speed = max(speed, 1e-6)
        self._home_position = home_position
        self._home_time = home_time
        self._version = version
        self._model = model

        self._state = _NOT_REFERENCED_RESET
        self._position = 0.0
        self._target = 0.0
        self._last_error = "@"

        # Motion interpolation bookkeeping.
        self._move_t0 = 0.0
        self._move_duration = 0.0
        self._move_from = 0.0

    # -- public test/debug helpers ------------------------------------------
    @property
    def state_code(self) -> str:
        self._advance()
        return self._state

    @property
    def position(self) -> float:
        self._advance()
        return self._position

    # -- frame handling ------------------------------------------------------
    def handle(self, command: str) -> str | None:
        """Process one command body (no address, no terminator).

        Returns the response payload (without address) for queries, or ``None``
        for commands that produce no reply.
        """
        self._advance()
        command = command.replace(" ", "")
        if len(command) < 2:
            self._last_error = "A"
            return None
        code = command[:2].upper()
        arg = command[2:]

        handler = getattr(self, f"_cmd_{code.lower()}", None)
        if handler is None:
            self._last_error = "A"
            return None
        return handler(arg)

    # -- time-based motion ---------------------------------------------------
    def _advance(self) -> None:
        if self._state not in (_MOVING, _HOMING):
            return
        elapsed = time.monotonic() - self._move_t0
        if self._move_duration <= 0 or elapsed >= self._move_duration:
            self._position = self._target
            self._state = (
                _READY_FROM_HOMING if self._state == _HOMING else _READY_FROM_MOVING
            )
        else:
            frac = elapsed / self._move_duration
            self._position = self._move_from + (self._target - self._move_from) * frac

    def _begin_move(self, target: float, *, homing: bool) -> None:
        self._move_from = self._position
        self._target = target
        distance = abs(target - self._move_from)
        if homing:
            self._move_duration = self._home_time
            self._state = _HOMING
        else:
            self._move_duration = distance / self._speed
            self._state = _MOVING
        self._move_t0 = time.monotonic()

    def _is_ready(self) -> bool:
        return self._state in (
            _READY_FROM_HOMING,
            _READY_FROM_MOVING,
            _READY_FROM_DISABLE,
        )

    # -- command implementations --------------------------------------------
    def _cmd_or(self, arg: str) -> None:  # Execute HOME search
        if self._state != _NOT_REFERENCED_RESET:
            # OR is only accepted in NOT REFERENCED; mirror the state errors.
            self._last_error = {
                _HOMING: "L",
                _MOVING: "M",
                _DISABLE_FROM_READY: "J",
            }.get(self._state, "K")
            return None
        self._begin_move(self._home_position, homing=True)
        return None

    def _cmd_pa(self, arg: str) -> str | None:  # Move absolute / query target
        if arg == "?":
            return f"PA{_fmt(self._target)}"
        if not (self._state == _MOVING or self._is_ready()):
            self._last_error = "H" if self._state == _NOT_REFERENCED_RESET else "J"
            return None
        target = self._parse_float(arg)
        if target is None:
            return None
        if not (self._sl <= target <= self._sr):
            self._last_error = "G"
            return None
        self._begin_move(target, homing=False)
        return None

    def _cmd_pr(self, arg: str) -> None:  # Move relative
        if not (self._state == _MOVING or self._is_ready()):
            self._last_error = "H" if self._state == _NOT_REFERENCED_RESET else "J"
            return None
        delta = self._parse_float(arg)
        if delta is None:
            return None
        target = self._target + delta
        if not (self._sl <= target <= self._sr):
            self._last_error = "G"
            return None
        self._begin_move(target, homing=False)
        return None

    def _cmd_st(self, arg: str) -> None:  # Stop motion
        if self._state in (_MOVING, _HOMING):
            self._target = self._position
            self._state = _READY_FROM_MOVING
        return None

    def _cmd_rs(self, arg: str) -> None:  # Reset controller (reboot)
        self.__init__(
            self.address,
            travel=(self._sl, self._sr),
            speed=self._speed,
            home_position=self._home_position,
            home_time=self._home_time,
            version=self._version,
            model=self._model,
        )
        return None

    def _cmd_mm(self, arg: str) -> str | None:  # Enter/leave DISABLE
        if arg == "?":
            return f"MM{self._state}"
        if arg == "0":
            if self._is_ready():
                self._state = _DISABLE_FROM_READY
            return None
        if arg == "1":
            if self._state == _DISABLE_FROM_READY:
                self._state = _READY_FROM_DISABLE
            return None
        self._last_error = "C"
        return None

    def _cmd_tp(self, arg: str) -> str:  # Tell current position
        return f"TP{_fmt(self._position)}"

    def _cmd_th(self, arg: str) -> str:  # Tell target position
        return f"TH{_fmt(self._target)}"

    def _cmd_ts(self, arg: str) -> str:  # Tell error flags + state
        return f"TS{_NO_FAULT}{self._state}"

    def _cmd_te(self, arg: str) -> str:  # Tell last command error (and clear)
        err = self._last_error
        self._last_error = "@"
        return f"TE{err}"

    def _cmd_tb(self, arg: str) -> str:  # Tell error string
        from .errors import ERROR_STRINGS

        code = arg[:1] if arg else self._last_error
        return f"TB{code} {ERROR_STRINGS.get(code, 'Unknown error.')}"

    def _cmd_sl(self, arg: str) -> str | None:  # Negative software limit (query only here)
        if arg == "?":
            return f"SL{_fmt(self._sl)}"
        self._last_error = "I"  # writing limits needs CONFIGURATION; unsupported
        return None

    def _cmd_sr(self, arg: str) -> str | None:  # Positive software limit (query only here)
        if arg == "?":
            return f"SR{_fmt(self._sr)}"
        self._last_error = "I"
        return None

    def _cmd_ve(self, arg: str) -> str:  # Controller revision
        return f"VE{self._version}"

    def _cmd_id(self, arg: str) -> str:  # Stage identifier
        return f"ID{self._model}"

    # -- helpers -------------------------------------------------------------
    def _parse_float(self, arg: str) -> float | None:
        try:
            return float(arg)
        except ValueError:
            self._last_error = "C"
            return None
