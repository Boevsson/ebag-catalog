"""
Turns errors that DRF does not know about into proper HTTP responses.

Errors tied to one input field are converted by the serializers (they know the
field names). What arrives here is everything else.
"""

import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from catalog.exceptions import CatalogError, CategoryInUse

logger = logging.getLogger(__name__)


def catalog_exception_handler(exc: Exception, context: dict) -> Response | None:
    response = exception_handler(exc, context)  # DRF's own errors, Http404, PermissionDenied
    if response is not None:
        return response

    if isinstance(exc, CategoryInUse):
        # The request is valid; the current state of the resource forbids it.
        return _error(str(exc), status.HTTP_409_CONFLICT)

    if isinstance(exc, CatalogError):
        return _error(str(exc), status.HTTP_400_BAD_REQUEST)

    if isinstance(exc, ObjectDoesNotExist):
        # The object was deleted between the view's lookup and the service's re-read.
        return _error("Not found.", status.HTTP_404_NOT_FOUND)

    if isinstance(exc, IntegrityError):
        # A database constraint caught what the validation could not see: two
        # requests racing for the same SKU / category name, or a category
        # deleted while a product was being added to it.
        logger.warning("Integrity error in %s: %s", context.get("view"), exc)
        return _error(
            "The request conflicts with a concurrent change. Please retry.",
            status.HTTP_409_CONFLICT,
        )

    return None  # a genuine bug: let Django log it and answer 500


def _error(detail: str, status_code: int) -> Response:
    # Same shape as DRF's own non-field errors.
    return Response({"detail": detail}, status=status_code)
