import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat

# 1-64 characters: latin letters, digits and the separators . _ -
# It has to start and end with a letter or digit ("-AB" and "AB-" are typos).
SKU_PATTERN = re.compile(r"[A-Z0-9](?:[A-Z0-9._-]{0,62}[A-Z0-9])?")


def normalize_sku(value: str) -> str:
    """
    The canonical form of a SKU: trimmed and upper-case.

    SKUs are stored and compared in this form only. That makes "ab-12" and
    "AB-12" the same product by an explicit rule in the code, instead of by
    the column collation happening to be case-insensitive (MariaDB's default,
    which a DBA can change).
    """
    return value.strip().upper()


def validate_sku(value: str) -> None:
    if not SKU_PATTERN.fullmatch(normalize_sku(value)):
        raise ValidationError(
            "A SKU is 1-64 characters long, contains only latin letters, digits and "
            "the separators '.', '_' and '-', and starts and ends with a letter or digit.",
            code="invalid_sku",
        )


def validate_image_size(image) -> None:
    limit = settings.PRODUCT_IMAGE_MAX_BYTES
    if image.size > limit:
        raise ValidationError(
            f"The image is {filesizeformat(image.size)}; the limit is {filesizeformat(limit)}.",
            code="image_too_large",
        )
