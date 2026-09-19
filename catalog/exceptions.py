"""
Domain errors of the catalog.

They describe *what* rule was broken in the language of the domain and know
nothing about HTTP. The API layer decides how each one is presented
(see `catalog.api.exception_handler` and the serializers).
"""


class CatalogError(Exception):
    """Base class for every rule violation raised by the catalog domain."""


class DuplicateCategoryName(CatalogError):
    """Two categories under the same parent may not share a name."""


class InvalidCategoryMove(CatalogError):
    """A category may not be moved under itself or under one of its descendants."""


class CategoryTreeTooDeep(CatalogError):
    """The move/creation would produce a path longer than the path column."""


class CategoryInUse(CatalogError):
    """A category that still has sub-categories or products cannot be deleted."""


class InvalidSearchCriteria(CatalogError, ValueError):
    """Search criteria that contradict themselves, e.g. min_price > max_price."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message
