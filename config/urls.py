from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from config import health

urlpatterns = [
    path("healthz", health.healthz, name="healthz"),
    path("readyz", health.readyz, name="readyz"),
    # The version lives in the URL so a breaking v2 can run next to v1.
    path("api/v1/", include("catalog.api.urls")),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

# Errors that never reach a DRF view are answered in JSON as well.
handler400 = "config.errors.bad_request"
handler404 = "config.errors.not_found"
handler500 = "config.errors.server_error"

if settings.SERVE_MEDIA:
    # Development/demo convenience. In production the images are served by
    # the object storage or the reverse proxy, never by Django.
    urlpatterns.append(
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})
    )
