from __future__ import annotations

import pytest

from app import config
from app.models import CallRecord
from app.world import load_world


@pytest.fixture(scope="session")
def world():
    return load_world()


@pytest.fixture
def make_call():
    def _make(**overrides) -> CallRecord:
        data = dict(
            id="D1S1", day=1, slot=1, time=config.SLOT_TIMES[1], is_con=True,
            caller_name="Alex", claims="Alex, new temp", caller_id="555-0391", patience=10,
        )
        data.update(overrides)
        return CallRecord(**data)
    return _make
