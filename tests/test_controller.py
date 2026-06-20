"""Drive the StageController against the protocol-level emulator (no hardware)."""

import time

import pytest

from stage_api.controller import ControllerState, StageController
from stage_api.emulator import EmulatedController
from stage_api.errors import ControllerError
from stage_api.transport import EmulatorTransport


def make_controller(**kw):
    transport = EmulatorTransport()
    transport.open()
    params = dict(travel=(0.0, 27.0), speed=1000.0, home_position=13.5, home_time=0.02)
    params.update(kw)
    emu = EmulatedController(1, **params)
    transport.add_controller(emu)
    return StageController(transport, 1), emu


def wait_ready(ctrl, timeout=2.0):
    deadline = time.monotonic() + timeout
    while ctrl.state in (ControllerState.MOVING, ControllerState.HOMING):
        if time.monotonic() > deadline:
            raise AssertionError("did not become ready")
        time.sleep(0.005)


def test_starts_not_referenced():
    ctrl, _ = make_controller()
    assert ctrl.state is ControllerState.NOT_REFERENCED
    assert ctrl.is_referenced is False


def test_home_then_ready_at_home_position():
    ctrl, _ = make_controller()
    ctrl.home()
    wait_ready(ctrl)
    assert ctrl.state is ControllerState.READY
    assert ctrl.position == pytest.approx(13.5)


def test_move_abs():
    ctrl, _ = make_controller()
    ctrl.home()
    wait_ready(ctrl)
    ctrl.move_abs(5.0)
    wait_ready(ctrl)
    assert ctrl.position == pytest.approx(5.0)
    assert ctrl.target == pytest.approx(5.0)


def test_move_rel():
    ctrl, _ = make_controller()
    ctrl.home()
    wait_ready(ctrl)
    ctrl.move_rel(-3.5)
    wait_ready(ctrl)
    assert ctrl.position == pytest.approx(10.0)


def test_move_before_home_raises_not_referenced():
    ctrl, _ = make_controller()
    with pytest.raises(ControllerError) as exc:
        ctrl.move_abs(5.0)
    assert exc.value.code == "H"
    assert exc.value.http_status == 409


def test_move_out_of_limits_raises():
    ctrl, _ = make_controller()
    ctrl.home()
    wait_ready(ctrl)
    with pytest.raises(ControllerError) as exc:
        ctrl.move_abs(99.0)
    assert exc.value.code == "G"
    assert exc.value.http_status == 422


def test_limits_and_info():
    ctrl, _ = make_controller()
    assert ctrl.limits() == (0.0, 27.0)
    info = ctrl.info()
    assert "CONEX-AGP" in info["version"]
    assert info["model"] == "CONEX-AGP"


def test_disable_enable_cycle():
    ctrl, _ = make_controller()
    ctrl.home()
    wait_ready(ctrl)
    ctrl.disable()
    assert ctrl.state is ControllerState.DISABLE
    ctrl.enable()
    assert ctrl.state is ControllerState.READY


def test_stop_during_move():
    ctrl, emu = make_controller(speed=2.0)  # slow so motion is observable
    ctrl.home()
    wait_ready(ctrl)
    ctrl.move_abs(13.5 + 5.0)
    assert ctrl.state is ControllerState.MOVING
    ctrl.stop()
    assert ctrl.state is ControllerState.READY


def test_reset_returns_to_not_referenced():
    ctrl, _ = make_controller()
    ctrl.home()
    wait_ready(ctrl)
    ctrl.reset()
    assert ctrl.state is ControllerState.NOT_REFERENCED


def test_two_digit_address_parsing():
    # Guards the prefix-stripping fix vs. the original hard-coded resp[3:].
    transport = EmulatorTransport()
    transport.open()
    transport.add_controller(
        EmulatedController(12, travel=(0.0, 27.0), speed=1000.0, home_time=0.02)
    )
    ctrl = StageController(transport, 12)
    ctrl.home()
    wait_ready(ctrl)
    ctrl.move_abs(7.25)
    wait_ready(ctrl)
    assert ctrl.position == pytest.approx(7.25)
