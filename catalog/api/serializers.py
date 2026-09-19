from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from catalog import validators
from catalog.exceptions import (
    CategoryTreeTooDeep,
    DuplicateCategoryName,
    InvalidCategoryMove,
    InvalidSearchCriteria,
)
from catalog.models import Category, Product
from catalog.search import ProductOrdering, ProductSearchCriteria
from catalog.services.categories import create_category, update_category

# --- categories -------------------------------------------------------------------


@contextmanager
def category_rules_as_field_errors() -> Iterator[None]:
    """
    The domain raises errors in its own language; the API answers with a 400
    that names the input field the client has to fix.
    """
    try:
        yield
    except DuplicateCategoryName as error:
        raise serializers.ValidationError({"name": [str(error)]}) from error
    except (InvalidCategoryMove, CategoryTreeTooDeep) as error:
        raise serializers.ValidationError({"parent": [str(error)]}) from error


class CategorySerializer(serializers.ModelSerializer):
    path = serializers.CharField(
        read_only=True,
        help_text='Ids from the top-level category down to this one, e.g. "/1/5/23/".',
    )

    class Meta:
        model = Category
        fields = ["id", "name", "parent", "path", "created_at", "updated_at"]

    # Writes go through the category service: it owns the rules of the tree.

    def create(self, validated_data: dict) -> Category:
        with category_rules_as_field_errors():
            return create_category(
                name=validated_data["name"],
                parent=validated_data.get("parent"),
            )

    def update(self, instance: Category, validated_data: dict) -> Category:
        # PATCH changes only what was sent. The rest is NOT filled in from
        # `instance`: the view read it before the service takes its lock, so it
        # may be stale, and writing it back would undo somebody else's change.
        # PUT replaces everything, so a missing parent means "none", exactly as
        # it does in a POST.
        if self.partial:
            changes = {
                field: validated_data[field]
                for field in ("name", "parent")
                if field in validated_data
            }
        else:
            changes = {"name": validated_data["name"], "parent": validated_data.get("parent")}

        with category_rules_as_field_errors():
            return update_category(instance, **changes)


# --- products ---------------------------------------------------------------------


class SkuField(serializers.CharField):
    """
    Converts input to the canonical SKU *before* the validators run, so the
    uniqueness check compares the stored form "AB-12" with "AB-12", not "ab-12".
    """

    def to_internal_value(self, data) -> str:
        return validators.normalize_sku(super().to_internal_value(data))


class ProductSerializer(serializers.ModelSerializer):
    sku = SkuField(
        max_length=64,
        validators=[
            validators.validate_sku,
            UniqueValidator(
                queryset=Product.objects.all(),
                message="A product with this SKU already exists.",
            ),
        ],
        help_text="Unique product identifier. Stored upper-case; cannot be changed later.",
    )
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "title",
            "description",
            "image",
            "price",
            "category",
            "category_name",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            # `"image": null` removes the image. Uploads use multipart/form-data.
            "image": {"allow_null": True},
        }

    def update(self, instance: Product, validated_data: dict) -> Product:
        # PATCH writes only the columns it was given. DRF saves the whole row,
        # and the view read that row before this save: a price somebody else
        # changed in between would be put back by a request that only sent a
        # title. PUT replaces everything, so there the whole row is the intent.
        if not self.partial:
            return super().update(instance, validated_data)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        # `auto_now` is skipped for a field that is not listed.
        instance.save(update_fields=[*validated_data, "updated_at"])
        return instance

    def validate_sku(self, sku: str) -> str:
        # Orders, stock and suppliers refer to a product by its SKU, so it is
        # fixed once the product exists. A typo is fixed by delete + create.
        if self.instance is not None and sku != self.instance.sku:
            raise serializers.ValidationError("The SKU of an existing product cannot be changed.")
        return sku


# --- search -----------------------------------------------------------------------

MAX_SEARCH_WORDS = 10


class ProductSearchQuerySerializer(serializers.Serializer):
    """
    The query string of the search endpoint.

    It parses and type-checks the parameters (and documents them in the OpenAPI
    schema). Rules that involve more than one parameter belong to
    ProductSearchCriteria; `validate` only turns them into a 400 response.
    """

    # `allow_blank`: a search box submitted with only a space in it means "no
    # text filter" (ProductSearchCriteria normalizes it away), not a 400.
    q = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        help_text=(
            "Words to look for in the title or SKU. Every word has to match. "
            f"At most {MAX_SEARCH_WORDS} words."
        ),
    )
    sku = serializers.CharField(
        required=False, allow_blank=True, max_length=64, help_text="Exact SKU."
    )
    min_price = serializers.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0"),
        help_text="Lowest price, inclusive.",
    )
    max_price = serializers.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0"),
        help_text="Highest price, inclusive.",
    )
    category = serializers.PrimaryKeyRelatedField(
        required=False,
        queryset=Category.objects.all(),
        help_text="Category id. Products of all its sub-categories are included.",
    )
    ordering = serializers.ChoiceField(
        required=False,
        choices=[ordering.value for ordering in ProductOrdering],
        default=ProductOrdering.TITLE.value,
    )

    def validate_q(self, text: str) -> str:
        # Every word becomes two LIKE conditions in the query. Capping them keeps
        # a single request from making the database do an arbitrary amount of work.
        if len(text.split()) > MAX_SEARCH_WORDS:
            raise serializers.ValidationError(f"Use at most {MAX_SEARCH_WORDS} words.")
        return text

    def validate(self, attrs: dict) -> dict:
        try:
            attrs["criteria"] = ProductSearchCriteria(
                text=attrs.get("q"),
                sku=attrs.get("sku"),
                min_price=attrs.get("min_price"),
                max_price=attrs.get("max_price"),
                category=attrs.get("category"),
                ordering=ProductOrdering(attrs["ordering"]),
            )
        except InvalidSearchCriteria as error:
            raise serializers.ValidationError({error.field: [error.message]}) from error
        return attrs
