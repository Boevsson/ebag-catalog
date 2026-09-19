"""
Health endpoints for the orchestrator / load balancer.

`/healthz` answers "is the process alive?" and deliberately touches nothing
else, so a database outage does not get healthy workers restarted.
`/readyz` answers "can this instance serve traffic?" and checks the database.
"""

import logging

from django.db import connection
from django.http import HttpRequest, JsonResponse

logger = logging.getLogger(__name__)


def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def readyz(request: HttpRequest) -> JsonResponse:
    try:
        connection.ensure_connection()
    except Exception:
        logger.exception("Readiness check failed: database is unreachable")
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
