import pytest

from app.auth import api_key, audit

KEY_ALICE = 'aLiCe-a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6'      # 37 chars, strong
KEY_BOB = 'bOb-Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4'          # 36 chars, strong
ENTRIES = f'alice:{KEY_ALICE},bob:{KEY_BOB}'


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / '.env'
    path.write_text(f'OTHER=1\nVEGA_API_KEY={ENTRIES}\n')
    api_key.configure(str(path))
    yield path
    api_key.configure(api_key._ENV_FILE)


@pytest.fixture
def audit_dir(tmp_path):
    d = tmp_path / 'logs'
    d.mkdir()
    audit.configure(str(d))
    yield d
    from app.shared.logger import LOG_DIR
    audit.configure(LOG_DIR)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(api_key.settings, 'vega_api_key', '')
    monkeypatch.setattr(api_key.settings, 'vega_auth_disabled', False)
