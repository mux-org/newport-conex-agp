# Newport CONEX-AGP Stage API

A containerised FastAPI service for controlling **one** Newport **CONEX-AGP**
linear stage over a REST API, plus an in-process **protocol-level emulator** for
development and testing without hardware.

Each container controls a single stage. **To control several stages, run several
instances of this container** — one per stage, each with its own config, device
passthrough, and published port.

The controller speaks a 2-letter ASCII command set over USB serial (921600 8-N-1).
This service wraps that protocol behind an HTTP API, serialises access to the
serial port, and surfaces the controller's deferred error model as meaningful
HTTP status codes.

## Quick start (no hardware)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

cp config.example.yaml config.yaml          # uses an 'emulator://' device
CONFIG=config.yaml stage-api                # serves on :8000

# in another shell:
curl localhost:8000/                         # stage summary
curl -X POST 'localhost:8000/home?wait=5'
curl -X POST 'localhost:8000/move_abs?wait=5' \
     -H 'content-type: application/json' -d '{"position": 13.5}'
curl localhost:8000/state
```

Interactive API docs are at `http://localhost:8000/docs`.

## Configuration

Configuration is a single flat YAML file, read once at startup. A `device` of
`emulator://` is backed by the simulator; everything else is identical.

```yaml
device: /dev/newport-focus   # a STABLE udev symlink, not /dev/ttyUSB0
address: 1
label: focus
auto_home: false
travel: [0.0, 27.0]
units: mm
poll_interval: 0.25
default_timeout: 30.0
```

| Field | Meaning |
|---|---|
| `device` | Serial symlink, or `emulator://` |
| `address` | RS-485 controller address (default `1`) |
| `label` | Human-readable name, returned by `GET /` and `/health`; defaults to the device basename |
| `auto_home` | Home the stage on startup — **causes physical motion**; default `false` |
| `travel` | `[min, max]` software limits; queried from the controller if omitted |
| `units` | Display metadata only |
| `speed`/`home_position`/`home_time` | Emulator behaviour (ignored for hardware) |
| `poll_interval`/`default_timeout` | `?wait` polling period and bound |

## API

| Method & path | Purpose |
|---|---|
| `GET /` | Stage summary (label, device, address, emulated, connected, units) |
| `GET /health` | Service + stage status |
| `GET /state` | State, position, target, referenced, fault flags |
| `GET /position` | Current encoder position |
| `GET /target` | Commanded target |
| `GET /limits` | Software travel limits |
| `GET /info` | Controller version + stage model |
| `POST /home` | Execute HOME search |
| `POST /move_abs` | Absolute move — body `{"position": …}` |
| `POST /move_rel` | Relative move — body `{"displacement": …}` |
| `POST /stop` | Stop motion |
| `POST /reset` | Reboot the controller (`RS`) |
| `POST /disable` / `enable` | Open / close the servo loop (`MM0`/`MM1`) |

### Move semantics

Moves are **non-blocking**: they validate, issue the motion, and return `202`
with the current state immediately. Poll `GET /state` to track progress, or add
`?wait=<seconds>` to block server-side until the stage is READY (bounded by
`default_timeout`).

```
POST /move_abs   {"position": 13.5}   -> 202 {state: MOVING}
GET  /state                            -> {state: READY, position: 13.5}
POST /move_abs?wait=10  {...}          -> blocks <=10s, returns READY
```

### Homing

Nothing homes automatically by default. A move issued while `NOT_REFERENCED`
returns `409`; call `POST /home` first. Set `auto_home: true` only where
unattended homing on container restart is safe.

### Errors

| Situation | Status |
|---|---|
| Position outside travel limits (pre-validated) | `422` |
| Controller rejected command (out-of-limits `G`/`N`) | `422` |
| Wrong state (e.g. move while NOT_REFERENCED `H`) | `409` |
| Stage unplugged / port won't open | `503` |

Controller errors include the raw `TE` code and its documented string. **Every
command is verified via `TE`** so a silently-rejected command raises instead of
looking like success.

## Deployment (Podman)

1. Install the udev rules so each controller gets a stable name:
   ```bash
   sudo cp udev/60-newport-stage.rules /etc/udev/rules.d/
   sudo udevadm control --reload && sudo udevadm trigger
   ```
   Map the resulting `/dev/newport-*` symlink in `config.yaml`.

2. Build and run:
   ```bash
   podman build -t newport-stage-api -f Dockerfile .
   podman run -d --name stage-api \
     -p 127.0.0.1:8000:8000 \
     -v ./config.yaml:/app/config.yaml:ro \
     --device=/dev/newport-focus \
     newport-stage-api
   ```
   or `podman-compose up` using `compose.yaml`.

   For a second stage, run a second container with its own config, `--device`,
   and host port (e.g. `-p 127.0.0.1:8001:8000`).

**Hotplug caveat:** with per-device passthrough, a stage that is physically
unplugged and replugged may reappear on a new `/dev/ttyUSB*` minor the running
container can't see — restart the container to recover. The udev rules name ports
*by physical path*, so replugging into the **same** USB port tends to reuse the
node and reconnect on its own. A transiently dropped/reopened port (same node)
reconnects lazily on the next request with no restart.

## Security

No authentication by default — bind to `127.0.0.1` or a trusted lab subnet (the
published-port host above binds to localhost). To require a shared key, set
`API_KEY=…`; clients then send `X-API-Key: …`.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

Tests run entirely against the emulator: the `StageController` framing/parsing is
exercised byte-for-byte (`tests/test_controller.py`) and the HTTP surface
end-to-end (`tests/test_api.py`).

## Project layout

```
src/stage_api/
  config.py      # flat YAML -> pydantic model
  transport.py   # Transport seam: SerialTransport | EmulatorTransport
  emulator.py    # Protocol-level fake CONEX-AGP controller
  controller.py  # Sync controller logic (refactored from the verified stage.py)
  service.py     # Stage service: transport, lock, lazy reconnect, ?wait polling
  models.py      # Request/response schemas
  app.py         # FastAPI routes + error mapping
udev/            # Stable device-naming rules
Dockerfile, compose.yaml
```

### Relationship to the original `stage.py`

`controller.py` preserves the verified command strings and the `TS` state-code
mapping verbatim; the only changes are the transport seam (so the emulator can be
substituted) and `TE` verification after every command.

## Deliberately out of scope

CONFIGURATION-state parameter writes — units (`SU`), software limits (`SL`/`SR`),
gains (`KP`/`KI`), deadband (`DB`) — are **not** exposed. They require entering
CONFIGURATION via `PW`, which writes to flash (documented ~100-write lifetime) and
reboots, and they change alignment-critical calibration. Do those once on the
bench, not over a runtime API.
