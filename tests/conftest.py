from __future__ import annotations

import pytest

from app.url_policy import get_public_allowlist


@pytest.fixture(autouse=True)
def _clear_allowlist_cache():
    get_public_allowlist.cache_clear()
    yield
    get_public_allowlist.cache_clear()
