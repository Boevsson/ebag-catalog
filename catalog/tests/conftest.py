import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient


@pytest.fixture(autouse=True)
def _isolated_settings(settings, tmp_path):
    """Keep uploads out of the real media directory and make password hashing fast."""
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def _empty_cache():
    """Throttle counters live in the cache; one test must not rate-limit the next."""
    cache.clear()


@pytest.fixture
def api_client() -> APIClient:
    """An anonymous client."""
    return APIClient()


@pytest.fixture
def staff_client(db) -> APIClient:
    """A client authenticated as a staff user (allowed to change the catalog)."""
    user = get_user_model().objects.create_user("staff", password="pw", is_staff=True)
    client = APIClient()
    client.force_authenticate(user)
    return client
