"""NVS deadline store (§4.4): fake esp32.NVS on CPython."""

import sys
import types

import pytest

import store


class FakeNVS:
    """Minimal esp32.NVS: int32 keys per namespace, shared across instances."""

    storage = {}

    def __init__(self, namespace):
        self.ns = namespace
        self.storage.setdefault(namespace, {})

    def get_i32(self, key):
        try:
            return self.storage[self.ns][key]
        except KeyError:
            raise OSError("ESP_ERR_NVS_NOT_FOUND") from None

    def set_i32(self, key, value):
        self.storage[self.ns][key] = int(value)

    def erase_key(self, key):
        self.storage[self.ns].pop(key, None)

    def commit(self):
        pass


@pytest.fixture
def fake_esp32(monkeypatch):
    module = types.ModuleType("esp32")
    module.NVS = FakeNVS
    monkeypatch.setitem(sys.modules, "esp32", module)
    FakeNVS.storage.clear()
    return module


def test_without_esp32_the_store_is_a_noop(monkeypatch):
    monkeypatch.setitem(sys.modules, "esp32", None)
    timer = store.TimerStore()
    assert timer.load() is None
    timer.save(1234)
    timer.clear()
    assert timer.load() is None


def test_save_load_clear_roundtrip(fake_esp32):
    timer = store.TimerStore()
    assert timer.load() is None
    timer.save(1_793_012_100)
    assert timer.load() == 1_793_012_100
    # A reboot creates a new object but reads the same NVS namespace.
    assert store.TimerStore().load() == 1_793_012_100
    timer.clear()
    assert store.TimerStore().load() is None


def test_zero_deadline_is_treated_as_empty(fake_esp32):
    timer = store.TimerStore()
    timer.save(0)
    assert timer.load() is None
