"""
Settings for the catalog service.

Everything that differs between environments comes from environment variables
(twelve-factor style); see `.env.example` for the full list. A local `.env`
file is read when present, real environment variables always win.
"""

from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# --- Core ---------------------------------------------------------------------

DEBUG = env.bool("DJANGO_DEBUG", default=False)
SECRET_KEY = env.str("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "drf_spectacular",
    "catalog",
    # Deletes image files when a product is deleted or its image is replaced.
    # Must stay last so it sees every model.
    "django_cleanup.apps.CleanupConfig",
]

# This is a stateless JSON API authenticated with JWTs: there are no sessions,
# cookies or HTML forms, so the session/CSRF/auth middleware are left out.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",  # the Swagger UI page is HTML
]

# `manage.py check --deploy` findings that do not apply here, silenced on purpose:
SILENCED_SYSTEM_CHECKS = [
    # CSRF abuses credentials the browser attaches by itself (cookies). This API
    # only accepts a JWT in the Authorization header, which a browser never adds.
    "security.W003",
    # HSTS preloading is a commitment for the whole domain, not one service's call.
    "security.W021",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# No URL here ends with a slash (see catalog/api/urls.py), so Django's
# "append a slash and redirect" has nothing to redirect to.
APPEND_SLASH = False

# Only needed to render the Swagger UI page.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
    }
]

# --- Database -----------------------------------------------------------------
# MariaDB, configured by DATABASE_URL alone. Application code only talks to the
# Django ORM: there is no raw SQL anywhere.

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

if DATABASES["default"]["ENGINE"] == "django.db.backends.mysql":
    # Full Unicode (Cyrillic product titles, emoji) needs utf8mb4 on MariaDB.
    # Guarded, because these options would break a quick local run on SQLite.
    DATABASES["default"].setdefault("OPTIONS", {}).setdefault("charset", "utf8mb4")
    DATABASES["default"]["TEST"] = {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Staff accounts are created with `createsuperuser`; these are Django's stock rules.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{rule}"}
    for rule in (
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    )
]

# --- Internationalisation -----------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# --- Media (product images) ---------------------------------------------------
# Where uploads go is a deployment decision, not a code decision. Models,
# serializers and the clean-up of old files only ever talk to Django's storage
# API (its built-in Strategy pattern), so nothing in the application asks
# "which storage is this?". This file only picks one, by name:
#
#   MEDIA_STORAGE=local   the local disk (default): fine for one node
#   MEDIA_STORAGE=gcs     a Google Cloud Storage bucket, optionally behind a CDN

MEDIA_STORAGE = env.str("MEDIA_STORAGE", default="local")

MEDIA_URL = "/media/"
MEDIA_ROOT = env.path("DJANGO_MEDIA_ROOT", default=str(BASE_DIR / "media"))


def _local_disk() -> dict:
    return {"BACKEND": "django.core.files.storage.FileSystemStorage"}


def _google_cloud_storage() -> dict:
    # Credentials are found the standard Google way: the GOOGLE_APPLICATION_CREDENTIALS
    # file, or the service account of the machine (GKE, Cloud Run). Nothing secret here.
    options = {
        "bucket_name": env.str("GCS_BUCKET_NAME"),
        "location": env.str("GCS_LOCATION", default=""),  # a folder inside the bucket
        # Product images are public. Plain URLs (no signature) can be cached by
        # a CDN, and because file names are random and never reused, forever.
        "querystring_auth": False,
        "object_parameters": {"cache_control": "public, max-age=31536000, immutable"},
    }
    if env.str("GCS_CDN_URL", default=""):
        # URLs then point at the CDN domain in front of the bucket.
        options["custom_endpoint"] = env.str("GCS_CDN_URL")
    return {"BACKEND": "storages.backends.gcloud.GoogleCloudStorage", "OPTIONS": options}


# The available storages, by name. Another provider (S3, Azure) is one more
# function and one more entry here, plus its django-storages extra; nothing
# that exists has to be edited. They are functions rather than plain dicts so
# that a storage asks for its environment variables only when it is the chosen
# one: GCS_BUCKET_NAME is required for "gcs" and irrelevant for "local".
MEDIA_STORAGES = {
    "local": _local_disk,
    "gcs": _google_cloud_storage,
}

if MEDIA_STORAGE not in MEDIA_STORAGES:
    # A typo must stop the process, not silently fall back to the local disk.
    raise ImproperlyConfigured(
        f'MEDIA_STORAGE must be one of {sorted(MEDIA_STORAGES)}, not "{MEDIA_STORAGE}".'
    )

STORAGES = {
    "default": MEDIA_STORAGES[MEDIA_STORAGE](),
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Django serves uploaded files itself only from the local disk, and only when
# asked to (development and the demo). Files in a bucket are served by Google.
SERVE_MEDIA = MEDIA_STORAGE == "local" and env.bool("DJANGO_SERVE_MEDIA", default=DEBUG)

PRODUCT_IMAGE_MAX_BYTES = 5 * 1024 * 1024

# --- API ----------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    # Anyone may read the catalog; only staff users may change it.
    "DEFAULT_PERMISSION_CLASSES": ["catalog.api.permissions.IsStaffOrReadOnly"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "catalog.api.parsers.NestingSafeJSONParser",  # JSON; absurd nesting is a 400, not a 500
        "rest_framework.parsers.MultiPartParser",  # product image upload
    ],
    "DEFAULT_PAGINATION_CLASS": "catalog.api.pagination.StandardPagination",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "catalog.api.exception_handler.catalog_exception_handler",
    # Applied only to views that declare `throttle_scope` (the token endpoints).
    # Counters live in the default cache: per-process memory here, a shared
    # cache such as Redis once there is more than one worker.
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"auth": env.str("AUTH_THROTTLE_RATE", default="10/min")},
    # Who "a client" is for the throttle. Left unset, DRF believes whatever the
    # X-Forwarded-For header says, so a fresh value per request is a fresh
    # client with a fresh allowance. 0: ignore the header and use the address of
    # the connection. Behind N trusted reverse proxies set N, and the address
    # is taken from what the last N proxies wrote.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=1)),
    # A dedicated key, so rotating it does not invalidate everything else
    # that depends on SECRET_KEY (and vice versa).
    "SIGNING_KEY": env.str("JWT_SIGNING_KEY", default=SECRET_KEY),
    "ALGORITHM": "HS256",
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "eBag Catalog API",
    "DESCRIPTION": "Products, a category tree and product search.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,  # separate request/response schemas (image upload vs URL)
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
}

# --- Security (effective behind a TLS-terminating proxy) ----------------------

if env.bool("DJANGO_BEHIND_TLS_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    # Health probes come from inside the network over plain HTTP, not through
    # the proxy. They need their 200; a redirect to https fails the check on
    # load balancers that accept nothing else.
    SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# --- Logging ------------------------------------------------------------------
# Log to stdout and let the platform (Docker, systemd, ...) collect it.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
}
