"""FastAPI application exposing one CONEX-AGP stage over REST.

This container controls a single stage; every endpoint operates on it directly
(no stage name in the path).  To control several stages, run several instances.

Move endpoints are non-blocking by default: they validate, issue the motion, and
return ``202`` immediately with the current state; clients poll ``/state`` (or
pass ``?wait=<seconds>`` for server-side blocking).  Controller errors surface as
``409``/``422``/``503`` via the exception handlers below.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .config import load_config
from .controller import ControllerState
from .errors import (
    ControllerError,
    PositionOutOfRange,
    StageUnavailable,
)
from .models import (
    CommandAccepted,
    HealthResponse,
    InfoResponse,
    LimitsResponse,
    MoveAbsRequest,
    MoveRelRequest,
    StageSummary,
    StateResponse,
)
from .service import StageService

log = logging.getLogger("stage_api")

CONFIG_PATH = os.environ.get("CONFIG", "/app/config.yaml")
API_KEY = os.environ.get("API_KEY")  # optional; auth disabled when unset


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config(CONFIG_PATH)
    service = StageService(config)
    app.state.service = service
    await service.startup()
    log.info("started with stage: %s (%s)", config.label, config.device)
    try:
        yield
    finally:
        await service.shutdown()


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if API_KEY is not None and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


app = FastAPI(
    title="Newport CONEX-AGP Stage API",
    version="0.1.0",
    dependencies=[Depends(require_api_key)],
    lifespan=lifespan,
)


def _service(request: Request) -> StageService:
    return request.app.state.service


# -- exception handlers -----------------------------------------------------
@app.exception_handler(StageUnavailable)
async def _unavailable(_: Request, exc: StageUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ControllerError)
async def _controller_error(_: Request, exc: ControllerError):
    return JSONResponse(
        status_code=exc.http_status,
        content={"detail": exc.message, "error_code": exc.code},
    )


@app.exception_handler(PositionOutOfRange)
async def _out_of_range(_: Request, exc: PositionOutOfRange):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


# -- identity / health ------------------------------------------------------
@app.get("/", response_model=StageSummary)
async def summary(request: Request):
    svc = _service(request)
    cfg = svc.config
    return StageSummary(
        label=cfg.label,
        device=cfg.device,
        address=cfg.address,
        emulated=cfg.is_emulated,
        connected=svc.connected,
        units=cfg.units,
    )


@app.get("/health", response_model=HealthResponse)
async def health(request: Request):
    svc = _service(request)
    if not svc.connected:
        return HealthResponse(status="degraded", connected=False, state="disconnected")
    try:
        st = await svc.state()
        state = st.name.lower()
        status = "ok"
    except StageUnavailable:
        return HealthResponse(status="degraded", connected=False, state="disconnected")
    except ControllerError as exc:
        state = f"error:{exc.code}"
        status = "degraded"
    return HealthResponse(status=status, connected=svc.connected, state=state)


# -- reads ------------------------------------------------------------------
@app.get("/state", response_model=StateResponse)
async def get_state(request: Request):
    svc = _service(request)

    def read(c):
        st = c.state
        pos = c.position if st is not ControllerState.NOT_REFERENCED else None
        tgt = c.target if st not in (
            ControllerState.NOT_REFERENCED, ControllerState.CONFIGURATION
        ) else None
        return st, pos, tgt, c.fault_flags

    st, pos, tgt, flags = await svc.transact(read)
    return StateResponse(
        state=st.name,
        position=pos,
        target=tgt,
        referenced=st not in (ControllerState.NOT_REFERENCED, ControllerState.CONFIGURATION),
        fault_flags=flags,
        connected=svc.connected,
    )


@app.get("/position")
async def get_position(request: Request):
    pos = await _service(request).position()
    return {"position": pos}


@app.get("/target")
async def get_target(request: Request):
    tgt = await _service(request).transact(lambda c: c.target)
    return {"target": tgt}


@app.get("/limits", response_model=LimitsResponse)
async def get_limits(request: Request):
    svc = _service(request)
    low, high = await svc.limits()
    return LimitsResponse(low=low, high=high, units=svc.config.units)


@app.get("/info", response_model=InfoResponse)
async def get_info(request: Request):
    info = await _service(request).transact(lambda c: c.info())
    return InfoResponse(version=info["version"], model=info["model"])


# -- actions ----------------------------------------------------------------
async def _finish(svc: StageService, wait: float | None) -> CommandAccepted:
    """Build the response after issuing a motion, optionally blocking for ?wait."""
    waited = False
    if wait is not None:
        await svc.wait_ready(wait)
        waited = True
    st = await svc.state()
    pos = None if st is ControllerState.NOT_REFERENCED else await svc.position()
    tgt = await svc.transact(lambda c: c.target) if st not in (
        ControllerState.NOT_REFERENCED, ControllerState.CONFIGURATION
    ) else None
    return CommandAccepted(state=st.name, position=pos, target=tgt, waited=waited)


def _wait_param(wait: float | None, default_timeout: float) -> float | None:
    if wait is None:
        return None
    return min(wait, default_timeout) if wait > 0 else default_timeout


@app.post("/home", status_code=202, response_model=CommandAccepted)
async def home(request: Request, wait: float | None = Query(default=None)):
    svc = _service(request)
    await svc.home()
    return await _finish(svc, _wait_param(wait, svc.config.default_timeout))


@app.post("/move_abs", status_code=202, response_model=CommandAccepted)
async def move_abs(
    request: Request,
    body: MoveAbsRequest,
    wait: float | None = Query(default=None),
):
    svc = _service(request)
    # Pre-validate against known limits for a clean 422 with no serial round-trip.
    low, high = await svc.limits()
    if not (low <= body.position <= high):
        raise PositionOutOfRange(body.position, low, high)
    if not await _is_referenced(svc):
        raise ControllerError("H")
    await svc.transact(lambda c: c.move_abs(body.position))
    return await _finish(svc, _wait_param(wait, svc.config.default_timeout))


@app.post("/move_rel", status_code=202, response_model=CommandAccepted)
async def move_rel(
    request: Request,
    body: MoveRelRequest,
    wait: float | None = Query(default=None),
):
    svc = _service(request)
    if not await _is_referenced(svc):
        raise ControllerError("H")
    low, high = await svc.limits()
    target = await svc.transact(lambda c: c.target)
    if not (low <= target + body.displacement <= high):
        raise PositionOutOfRange(target + body.displacement, low, high)
    await svc.transact(lambda c: c.move_rel(body.displacement))
    return await _finish(svc, _wait_param(wait, svc.config.default_timeout))


@app.post("/stop", status_code=202, response_model=CommandAccepted)
async def stop(request: Request):
    svc = _service(request)
    await svc.transact(lambda c: c.stop())
    return await _finish(svc, None)


@app.post("/reset", status_code=202)
async def reset(request: Request):
    svc = _service(request)
    await svc.transact(lambda c: c.reset())
    return {"detail": "reset issued (controller reboots)"}


@app.post("/disable", status_code=202, response_model=CommandAccepted)
async def disable(request: Request):
    svc = _service(request)
    await svc.transact(lambda c: c.disable())
    return await _finish(svc, None)


@app.post("/enable", status_code=202, response_model=CommandAccepted)
async def enable(request: Request):
    svc = _service(request)
    await svc.transact(lambda c: c.enable())
    return await _finish(svc, None)


async def _is_referenced(svc: StageService) -> bool:
    st = await svc.state()
    return st not in (ControllerState.NOT_REFERENCED, ControllerState.CONFIGURATION)
