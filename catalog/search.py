"""
Product search.

    HTTP query string                      (catalog.api: parsing, 400 responses)
        -> ProductSearchCriteria           (this module: what is being asked, validated)
        -> search_products()               (this module: how it is answered)
        -> ProductQuerySet filters         (catalog.models.product: one idea each)

The criteria object is the seam of this design. It knows nothing about HTTP,
so the same search can be called from a task or a management command, and
nothing about SQL, so `search_products` could later be answered by
Elasticsearch without touching the API layer.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from catalog.exceptions import InvalidSearchCriteria
from catalog.models import Category, Product
from catalog.models.product import ProductQuerySet
from catalog.validators import normalize_sku


class ProductOrdering(StrEnum):
    """The sort orders a client may ask for. The value is the public API value."""

    TITLE = "title"
    TITLE_DESC = "-title"
    PRICE = "price"
    PRICE_DESC = "-price"
    NEWEST = "-created_at"
    OLDEST = "created_at"


@dataclass(frozen=True, slots=True)
class ProductSearchCriteria:
    """
    What the client is looking for. Every criterion is optional; the ones that
    are given must all hold (AND).

    It is a value object: it normalizes and validates itself on creation and
    cannot change afterwards, so code that receives one never has to re-check it.
    """

    text: str | None = None  # words to find in the title or the SKU
    sku: str | None = None  # exact SKU
    min_price: Decimal | None = None  # inclusive
    max_price: Decimal | None = None  # inclusive
    category: Category | None = None  # this category and everything below it
    ordering: ProductOrdering = ProductOrdering.TITLE

    def __post_init__(self) -> None:
        # A frozen dataclass blocks normal assignment, even in here;
        # object.__setattr__ is the documented way to normalize fields.
        object.__setattr__(self, "text", (self.text or "").strip() or None)
        object.__setattr__(self, "sku", normalize_sku(self.sku or "") or None)

        for field in ("min_price", "max_price"):
            price = getattr(self, field)
            if price is not None and price < 0:
                raise InvalidSearchCriteria(field, "A price cannot be negative.")

        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise InvalidSearchCriteria("max_price", "max_price cannot be lower than min_price.")

    @property
    def terms(self) -> tuple[str, ...]:
        """The individual words of `text`."""
        return tuple(self.text.split()) if self.text else ()


def search_products(criteria: ProductSearchCriteria) -> ProductQuerySet:
    """
    The products matching `criteria`, as a lazy queryset.

    Nothing is fetched here: the caller paginates the queryset, so the database
    only ever returns one page. `select_related` loads each product's category
    in the same query instead of one extra query per product.
    """
    products = Product.objects.select_related("category")

    if criteria.terms:
        products = products.matching_terms(criteria.terms)
    if criteria.sku:
        products = products.with_sku(criteria.sku)
    if criteria.min_price is not None or criteria.max_price is not None:
        products = products.priced_between(criteria.min_price, criteria.max_price)
    if criteria.category is not None:
        products = products.in_category_tree(criteria.category)

    # `id` breaks ties: without it, products with equal titles/prices may come
    # back in a different order on every query and jump between pages.
    return products.order_by(criteria.ordering.value, "id")
