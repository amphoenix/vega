import pytest

from app.state import db, settings


@pytest.fixture(autouse=True)
def state_db(tmp_path):
    """Each test gets its own empty state database."""
    original = db._path
    db.configure(str(tmp_path / 'state.db'))
    settings.clear_cache()
    yield
    db.configure(original)
    settings.clear_cache()
