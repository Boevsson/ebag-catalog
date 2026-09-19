import uuid
from collections.abc import Iterable
from decimal import Decimal
from pathlib import PurePath

from django.core.validators import MinValueValidator
from django.db import DatabaseError, models
from django.db.models import Q

from catalog.models.base import TimestampedModel
from catalog.models.category import Category
from catalog.validators import normalize_sku, validate_image_size, validate_sku


def product_image_path(instance: "Product | None", filename: str) -> str:
    """
    Where an uploaded image is stored.

    Only the (lower-cased) extension of the client's file name is kept. A
    random name means uploads can never overwrite each other, cannot smuggle in
    path segments, and cannot be enumerated by guessing names.
    """
    extension = PurePath(filename).suffix.lower()
    return f"products/{uuid.uuid4().hex}{extension}"


class ProductQuerySet(models.QuerySet):
    """
    Filters in the language of the catalog.

    Each method narrows the queryset by one idea and returns a queryset again,
    so they chain in any combination (`catalog.search` does exactly that):

        Product.objects.matching_terms(["oat", "milk"]).priced_between(None, 5)
    """

    def matching_terms(self, terms: Iterable[str]) -> "ProductQuerySet":
        """
        Products where *every* term appears in the title or in the SKU.

        One `filter()` per term is an AND between terms, so "oat milk" and
        "milk oat" find the same products and each extra word narrows the result.
        `icontains` is case-insensitive and escapes `%` and `_`, so user input
        can never act as a LIKE wildcard.
        """
        products = self
        for term in terms:
            products = products.filter(Q(title__icontains=term) | Q(sku__icontains=term))
        return products

    def with_sku(self, sku: str) -> "ProductQuerySet":
        """Exact match on the SKU, compared in its canonical (upper-case) form."""
        return self.filter(sku=normalize_sku(sku))

    def priced_between(self, minimum: Decimal | None, maximum: Decimal | None) -> "ProductQuerySet":
        """Inclusive on both ends; `None` leaves that end open."""
        products = self
        if minimum is not None:
            products = products.filter(price__gte=minimum)
        if maximum is not None:
            products = products.filter(price__lte=maximum)
        return products

    def in_category_tree(self, category: Category) -> "ProductQuerySet":
        """
        Products in `category` or in any category below it.

        The subtree is a sub-query on the (small) category table - a prefix
        match on the materialized path - and the products are then found
        through the index of their `category_id` foreign key.
        """
        return self.filter(category__in=Category.objects.subtree_of(category))


class Product(TimestampedModel):
    sku = models.CharField(
        "SKU",
        max_length=64,
        unique=True,
        validators=[validate_sku],
        help_text="Unique product identifier. Stored upper-case; cannot be changed later.",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image = models.ImageField(
        upload_to=product_image_path,
        blank=True,
        validators=[validate_image_size],
    )
    # A single, implicit currency (EUR). Decimal, never float, for money.
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    # A product cannot outlive its category by accident: see Category.parent.
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["title", "id"]
        constraints = [
            # Validators only run in forms/serializers; this holds for every writer.
            models.CheckConstraint(condition=Q(price__gte=0), name="product_price_not_negative"),
        ]
        indexes = [
            # Price range filter and the three sort orders offered by the search.
            models.Index(fields=["price"], name="product_price_idx"),
            models.Index(fields=["title"], name="product_title_idx"),
            models.Index(fields=["created_at"], name="product_created_at_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.sku} {self.title}"

    def save(self, *args, **kwargs) -> None:
        self.sku = normalize_sku(self.sku)
        image_before = self.image.name
        try:
            super().save(*args, **kwargs)
        except DatabaseError:
            self._discard_image_stored_by_this_save(image_before)
            raise

    def _discard_image_stored_by_this_save(self, image_before: str | None) -> None:
        """
        Django stores an uploaded file *before* it writes the row. If the row is
        then refused (a lost race for the SKU, a category deleted meanwhile),
        nothing refers to that file and nothing would ever remove it.

        A new upload is recognised by its name: storing it replaced the
        client's file name with the generated one (`product_image_path`). An
        image that was already there keeps its name and is left alone.
        """
        if self.image and self.image.name != image_before:
            self.image.delete(save=False)
