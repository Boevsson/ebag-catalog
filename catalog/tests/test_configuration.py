"""
Environment-dependent configuration: config/settings.py and config/urls.py.

Which branch of these files runs depends on the environment the test suite
happens to run in (DEBUG is on on a laptop and off in Docker). To test
both sides everywhere, each test executes the file in a fresh namespace with
`runpy`, under the environment it wants. The settings Django is actually
running with are never touched.
"""

import runpy

import pytest
from django.core.exceptions import ImproperlyConfigured

import config.settings
import config.urls


@pytest.fixture
def settings_under(monkeypatch):
    """settings_under(NAME="value", ...) -> the settings module's globals for that environment."""

    def load(**environment: str) -> dict:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        return runpy.run_path(config.settings.__file__)

    return load


class TestDatabaseSettings:
    def test_mariadb_gets_full_unicode(self, settings_under):
        """Without utf8mb4, MariaDB cannot store every Unicode character."""
        loaded = settings_under(DATABASE_URL="mysql://user:pw@db:3306/catalog")

        database = loaded["DATABASES"]["default"]
        assert database["ENGINE"] == "django.db.backends.mysql"
        assert database["OPTIONS"]["charset"] == "utf8mb4"
        assert database["TEST"]["CHARSET"] == "utf8mb4"

    def test_mariadb_options_are_not_forced_on_a_quick_sqlite_run(self, settings_under):
        """sqlite3.connect() does not accept a charset and would refuse to start."""
        loaded = settings_under(DATABASE_URL="sqlite:///scratch.sqlite3")

        database = loaded["DATABASES"]["default"]
        assert database["ENGINE"] == "django.db.backends.sqlite3"
        assert "charset" not in database.get("OPTIONS", {})
        assert "TEST" not in database

    def test_connections_are_reused_and_health_checked(self, settings_under):
        database = settings_under(DATABASE_CONN_MAX_AGE="30")["DATABASES"]["default"]

        assert database["CONN_MAX_AGE"] == 30
        assert database["CONN_HEALTH_CHECKS"] is True


class TestHttpsSettings:
    def test_behind_a_tls_proxy_https_is_enforced(self, settings_under):
        loaded = settings_under(DJANGO_BEHIND_TLS_PROXY="true")

        assert loaded["SECURE_SSL_REDIRECT"] is True
        assert loaded["SECURE_PROXY_SSL_HEADER"] == ("HTTP_X_FORWARDED_PROTO", "https")
        assert loaded["SECURE_HSTS_SECONDS"] > 0

    @pytest.mark.django_db
    def test_health_probes_are_answered_not_redirected(self, settings_under, settings, client):
        """
        A probe calls the instance directly, over plain HTTP. Everything else is
        sent to https; the probe has to get its 200.
        """
        loaded = settings_under(DJANGO_BEHIND_TLS_PROXY="true")
        settings.SECURE_SSL_REDIRECT = loaded["SECURE_SSL_REDIRECT"]
        settings.SECURE_REDIRECT_EXEMPT = loaded["SECURE_REDIRECT_EXEMPT"]

        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/api/v1/products").status_code == 301

    def test_https_is_not_forced_otherwise(self, settings_under):
        """Plain HTTP must keep working for local development and the demo."""
        loaded = settings_under(DJANGO_BEHIND_TLS_PROXY="false")

        assert "SECURE_SSL_REDIRECT" not in loaded


class TestThrottleIdentity:
    def test_forwarded_for_header_is_not_trusted_by_default(self, settings_under, monkeypatch):
        """Anyone can send that header; without a proxy in front, it proves nothing."""
        monkeypatch.delenv("NUM_PROXIES", raising=False)

        assert settings_under()["REST_FRAMEWORK"]["NUM_PROXIES"] == 0

    def test_behind_a_reverse_proxy_the_header_is_used(self, settings_under):
        assert settings_under(NUM_PROXIES="1")["REST_FRAMEWORK"]["NUM_PROXIES"] == 1


class TestMediaStorageSettings:
    """Where uploaded images go is a deployment decision: MEDIA_STORAGE=local|gcs."""

    def test_local_disk_by_default(self, settings_under):
        loaded = settings_under(MEDIA_STORAGE="local", DJANGO_SERVE_MEDIA="true")

        assert loaded["STORAGES"]["default"]["BACKEND"].endswith("FileSystemStorage")
        assert loaded["SERVE_MEDIA"] is True

    def test_google_cloud_storage(self, settings_under):
        loaded = settings_under(MEDIA_STORAGE="gcs", GCS_BUCKET_NAME="shop-media")

        storage = loaded["STORAGES"]["default"]
        assert storage["BACKEND"] == "storages.backends.gcloud.GoogleCloudStorage"
        assert storage["OPTIONS"]["bucket_name"] == "shop-media"
        # Public, cacheable URLs: file names are random and never reused.
        assert storage["OPTIONS"]["querystring_auth"] is False
        assert "immutable" in storage["OPTIONS"]["object_parameters"]["cache_control"]
        assert "custom_endpoint" not in storage["OPTIONS"]

    def test_cdn_domain_in_front_of_the_bucket(self, settings_under):
        loaded = settings_under(
            MEDIA_STORAGE="gcs",
            GCS_BUCKET_NAME="shop-media",
            GCS_CDN_URL="https://cdn.example.com",
        )

        assert (
            loaded["STORAGES"]["default"]["OPTIONS"]["custom_endpoint"] == "https://cdn.example.com"
        )

    def test_django_never_serves_files_that_live_in_a_bucket(self, settings_under):
        loaded = settings_under(
            MEDIA_STORAGE="gcs", GCS_BUCKET_NAME="shop-media", DJANGO_SERVE_MEDIA="true"
        )

        assert loaded["SERVE_MEDIA"] is False

    def test_bucket_name_is_required_for_gcs(self, settings_under, monkeypatch):
        monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)

        with pytest.raises(ImproperlyConfigured, match="GCS_BUCKET_NAME"):
            settings_under(MEDIA_STORAGE="gcs")

    def test_unknown_storage_is_refused_at_start_up(self, settings_under):
        """A typo must stop the process, not silently fall back to local disk."""
        with pytest.raises(ImproperlyConfigured, match="MEDIA_STORAGE"):
            settings_under(MEDIA_STORAGE="ftp")

    def test_the_error_names_the_storages_that_do_exist(self, settings_under):
        """The list comes from the presets themselves, so it cannot go out of date."""
        with pytest.raises(ImproperlyConfigured) as error:
            settings_under(MEDIA_STORAGE="ftp")

        assert "'gcs'" in str(error.value)
        assert "'local'" in str(error.value)

    def test_every_preset_is_a_valid_storages_entry(self, settings_under):
        """Adding a provider = adding one preset. Each must build a Django STORAGES entry."""
        presets = settings_under(MEDIA_STORAGE="local", GCS_BUCKET_NAME="shop-media")[
            "MEDIA_STORAGES"
        ]

        assert set(presets) == {"local", "gcs"}
        for build in presets.values():
            assert "BACKEND" in build()


class TestMediaUrls:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_django_serves_uploaded_files_only_when_asked_to(self, settings, enabled):
        settings.SERVE_MEDIA = enabled

        urlpatterns = runpy.run_path(config.urls.__file__)["urlpatterns"]

        has_media_route = any(str(route.pattern).startswith("^media/") for route in urlpatterns)
        assert has_media_route is enabled
