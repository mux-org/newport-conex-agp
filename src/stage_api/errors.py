"""Error model for the CONEX-AGP stage API.

The CONEX protocol is fire-and-forget with *deferred* errors: an illegal command
(out-of-limits move, move while NOT REFERENCED, bad parameter, ...) is silently
accepted by the controller, does nothing, and memorises a one-character error
code that is only revealed by a subsequent ``TE`` query (with ``TB`` giving the
human string).  We therefore read ``TE`` after every command and translate the
code into a meaningful exception that the API layer maps to an HTTP status.

The code/string table mirrors the ``TE`` command documentation (section 2.4).
"""

from __future__ import annotations

# Error code -> human readable string, from the controller "TE" documentation.
ERROR_STRINGS: dict[str, str] = {
    "@": "No error.",
    "A": "Unknown message code or floating point controller address.",
    "B": "Controller address not correct.",
    "C": "Parameter missing or out of range.",
    "D": "Command not allowed.",
    "E": "Home sequence already started.",
    "G": "Displacement out of limits.",
    "H": "Command not allowed in NOT REFERENCED state.",
    "I": "Command not allowed in CONFIGURATION state.",
    "J": "Command not allowed in DISABLE state.",
    "K": "Command not allowed in READY state.",
    "L": "Command not allowed in HOMING state.",
    "M": "Command not allowed in MOVING state.",
    "N": "Current position out of software limit.",
    "S": "Communication time out.",
    "U": "Error during EEPROM access.",
    "V": "Error during command execution.",
}

# Error code -> HTTP status code used by the API layer.
#   out-of-range / out-of-limits  -> 422 Unprocessable Entity
#   wrong controller state        -> 409 Conflict
#   communication timeout         -> 503 Service Unavailable
#   everything else               -> 500 Internal Server Error
ERROR_HTTP_STATUS: dict[str, int] = {
    "C": 422,
    "G": 422,
    "N": 422,
    "A": 422,
    "B": 422,
    "D": 409,
    "E": 409,
    "H": 409,
    "I": 409,
    "J": 409,
    "K": 409,
    "L": 409,
    "M": 409,
    "S": 503,
    "U": 500,
    "V": 500,
}


class StageError(Exception):
    """Base class for all stage-API errors."""


class StageUnavailable(StageError):
    """The stage's serial port could not be opened (unplugged / in use / dropped)."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(f"Stage is unavailable: {detail}")


class ControllerError(StageError):
    """The controller memorised an error code (read back via ``TE``)."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or ERROR_STRINGS.get(code, "Unknown controller error.")
        self.http_status = ERROR_HTTP_STATUS.get(code, 500)
        super().__init__(f"Controller error {code!r}: {self.message}")


class PositionOutOfRange(StageError):
    """A requested position fell outside the stage's known software limits.

    Raised by the API's *pre-validation* layer, before any serial round-trip, so
    that out-of-range requests get a clean 422 without touching the hardware.
    """

    http_status = 422

    def __init__(self, requested: float, low: float, high: float):
        self.requested = requested
        self.low = low
        self.high = high
        super().__init__(
            f"Requested position {requested} is outside travel limits "
            f"[{low}, {high}]."
        )
