"""Transport seam between the :class:`StageController` and a serial line.

The controller speaks the raw CONEX ASCII protocol (``1PA13.5\\r\\n`` etc.).  A
``Transport`` is the thin byte pipe it writes to and reads lines from.  Two
implementations exist:

* :class:`SerialTransport`  -- a real ``pyserial`` connection to a ``/dev/ttyUSB*``.
* :class:`EmulatorTransport` -- an in-process fake bus that routes frames to one
  or more :class:`~stage_api.emulator.EmulatedController` objects.

Because both honour the same ``write(bytes)`` / ``readline() -> bytes`` contract,
the controller logic runs byte-for-byte unchanged against real hardware and the
emulator.  A transport corresponds to one *port* (one ``/dev`` node or one
emulated bus); several controllers may share it via RS-485 multidrop addressing.
"""

from __future__ import annotations

import abc

import serial


class Transport(abc.ABC):
    """A byte pipe carrying CONEX ASCII frames for one serial port."""

    @abc.abstractmethod
    def open(self) -> None:
        """Open the underlying resource. Raise on failure."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the underlying resource (idempotent)."""

    @property
    @abc.abstractmethod
    def is_open(self) -> bool:
        ...

    @abc.abstractmethod
    def write(self, data: bytes) -> None:
        ...

    @abc.abstractmethod
    def readline(self) -> bytes:
        """Read one ``\\r\\n``-terminated frame, or whatever arrived before timeout."""


class SerialTransport(Transport):
    """Real serial connection.

    The port settings are exactly those the verified ``stage.py`` used and that
    the controller documentation (section 1.5) fixes: 921600 8-N-1.
    """

    def __init__(self, device: str, *, timeout: float = 1.0):
        self._device = device
        self._timeout = timeout
        self._conn: serial.Serial | None = None

    def open(self) -> None:
        if self._conn is not None and self._conn.is_open:
            return
        self._conn = serial.Serial(
            self._device,
            baudrate=921600,
            timeout=self._timeout,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
        )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None and self._conn.is_open

    def write(self, data: bytes) -> None:
        if self._conn is None:
            raise serial.SerialException("port not open")
        self._conn.reset_input_buffer()
        self._conn.write(data)

    def readline(self) -> bytes:
        if self._conn is None:
            raise serial.SerialException("port not open")
        return self._conn.readline()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"SerialTransport(device={self._device!r}, open={self.is_open})"


class EmulatorTransport(Transport):
    """In-process fake bus routing frames to emulated controllers by address.

    A write is parsed for its leading address prefix and dispatched to the
    matching :class:`EmulatedController`; the controller's reply (if any) is
    buffered for the next :meth:`readline`.  Frames addressed to an unknown
    controller produce no reply -- exactly like a silent RS-485 node, which the
    controller layer surfaces as a communication timeout.
    """

    def __init__(self) -> None:
        from .emulator import EmulatedController  # local import avoids cycle

        self._controllers: dict[int, EmulatedController] = {}
        self._rx = b""
        self._open = False

    def add_controller(self, controller) -> None:
        self._controllers[controller.address] = controller

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, data: bytes) -> None:
        if not self._open:
            raise OSError("emulator transport not open")
        line = data.decode("ascii", "replace").strip()
        if not line:
            return
        address, command = _split_address(line)
        if address is None:
            # Broadcast (e.g. addressless "ST"/"MM0"): apply to every controller,
            # no reply is sent on the bus.
            for ctrl in self._controllers.values():
                ctrl.handle(command)
            return
        ctrl = self._controllers.get(address)
        if ctrl is None:
            return  # silent node -> read timeout upstream
        payload = ctrl.handle(command)
        if payload is not None:
            self._rx += f"{address}{payload}\r\n".encode("ascii")

    def readline(self) -> bytes:
        if b"\n" in self._rx:
            line, _, rest = self._rx.partition(b"\n")
            self._rx = rest
            return line + b"\n"
        out, self._rx = self._rx, b""
        return out

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        addrs = sorted(self._controllers)
        return f"EmulatorTransport(addresses={addrs}, open={self._open})"


def _split_address(line: str) -> tuple[int | None, str]:
    """Split a frame's leading integer address from the command body.

    ``"1PA13.5"`` -> ``(1, "PA13.5")``; ``"ST"`` -> ``(None, "ST")``.
    """
    i = 0
    while i < len(line) and line[i].isdigit():
        i += 1
    if i == 0:
        return None, line
    return int(line[:i]), line[i:]
