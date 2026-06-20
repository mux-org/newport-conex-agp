import textwrap

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def config_file(tmp_path):
    cfg = tmp_path / "stage.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            poll_interval: 0.02
            default_timeout: 5.0
            device: emulator://
            address: 1
            label: test
            travel: [0.0, 27.0]
            speed: 500.0
            home_position: 13.5
            home_time: 0.05
            units: mm
            """
        )
    )
    return cfg


@pytest.fixture
def client(config_file):
    # The app reads its config path from a module global; point it at our temp
    # file before the lifespan startup runs.
    import stage_api.app as appmod

    appmod.CONFIG_PATH = str(config_file)
    with TestClient(appmod.app) as c:
        yield c
