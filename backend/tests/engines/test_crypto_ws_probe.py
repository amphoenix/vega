import importlib.util

from app.engines import crypto_fo_scanner as cfo


def test_probe_false_when_package_missing(monkeypatch):
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None)
    assert cfo._ccxt_pro_available() is False


def test_probe_false_when_import_machinery_fails(monkeypatch):
    def boom(name):
        raise ModuleNotFoundError(name)
    monkeypatch.setattr(importlib.util, 'find_spec', boom)
    assert cfo._ccxt_pro_available() is False


def test_probe_true_when_spec_found(monkeypatch):
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: object())
    assert cfo._ccxt_pro_available() is True


def test_start_ws_falls_back_to_rest_without_ccxt_pro(monkeypatch):
    monkeypatch.setattr(cfo, '_ccxt_pro_available', lambda: False)
    client = cfo.DeribitClient()
    client.start_ws(['BTC'])
    assert client._ws_running is False          # returned early, no thread started
