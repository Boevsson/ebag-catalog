"""
search_products(): which products come back for which criteria.

The catalog used by every test:

    Food
    ├── Dairy            BUTTER-250      Butter 82% 250g               4.10
    │   ├── Milk         MILK-FRESH-1L   Fresh Milk 1L                 2.49
    │   │                MILK-OAT-1L     Oat Milk Barista 1L           3.99
    │   │                MLYAKO-3        Прясно мляко 3%               1.99
    │   └── Cheese       CHEESE-FETA-400 Bulgarian White Cheese 400g   5.20
    └── Bakery           BREAD-WHITE     White Bread Sliced            1.60
    Drinks               WATER-15        Mineral Water 1.5L            0.89
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from catalog.models import Product
from catalog.search import ProductOrdering, ProductSearchCriteria, search_products
from catalog.tests.factories import CategoryFactory, ProductFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def categories():
    food = CategoryFactory(name="Food")
    dairy = CategoryFactory(name="Dairy", parent=food)
    return {
        "food": food,
        "dairy": dairy,
        "milk": CategoryFactory(name="Milk", parent=dairy),
        "cheese": CategoryFactory(name="Cheese", parent=dairy),
        "bakery": CategoryFactory(name="Bakery", parent=food),
        "drinks": CategoryFactory(name="Drinks"),
    }


@pytest.fixture(autouse=True)
def catalog(categories):
    rows = [
        ("BUTTER-250", "Butter 82% 250g", "4.10", "dairy"),
        ("MILK-FRESH-1L", "Fresh Milk 1L", "2.49", "milk"),
        ("MILK-OAT-1L", "Oat Milk Barista 1L", "3.99", "milk"),
        ("MLYAKO-3", "Прясно мляко 3%", "1.99", "milk"),
        ("CHEESE-FETA-400", "Bulgarian White Cheese 400g", "5.20", "cheese"),
        ("BREAD-WHITE", "White Bread Sliced", "1.60", "bakery"),
        ("WATER-15", "Mineral Water 1.5L", "0.89", "drinks"),
    ]
    for sku, title, price, category in rows:
        ProductFactory(sku=sku, title=title, price=Decimal(price), category=categories[category])


def found(**criteria) -> list[str]:
    """SKUs returned for the given criteria, in result order."""
    return [product.sku for product in search_products(ProductSearchCriteria(**criteria))]


class TestNoCriteria:
    def test_returns_the_whole_catalog(self):
        assert len(found()) == 7


class TestText:
    def test_matches_part_of_the_title(self):
        assert found(text="Bread") == ["BREAD-WHITE"]

    def test_ignores_case(self):
        assert set(found(text="mILk")) == {"MILK-FRESH-1L", "MILK-OAT-1L"}

    @pytest.mark.skipif(
        connection.vendor == "sqlite",
        reason="SQLite's LIKE is case-insensitive for ASCII only; it is not a production engine.",
    )
    def test_ignores_case_in_cyrillic_too(self):
        assert found(text="МЛЯКО") == ["MLYAKO-3"]

    def test_every_word_has_to_match_in_any_order(self):
        assert found(text="milk oat") == ["MILK-OAT-1L"]
        assert found(text="milk bread") == []

    def test_matches_part_of_the_sku(self):
        assert found(text="feta") == ["CHEESE-FETA-400"]

    def test_words_can_match_title_and_sku_independently(self):
        # "barista" is only in the title, "OAT-1L" only in the SKU.
        assert found(text="barista oat-1l") == ["MILK-OAT-1L"]

    def test_like_wildcards_are_ordinary_characters(self):
        """'%' and '_' must not act as SQL wildcards coming from user input."""
        assert set(found(text="%")) == {"BUTTER-250", "MLYAKO-3"}  # the two titles with a '%'
        assert found(text="_") == []
        assert found(text="82%") == ["BUTTER-250"]

    def test_no_match_gives_an_empty_result(self):
        assert found(text="caviar") == []


class TestSku:
    def test_finds_exactly_one_product(self):
        assert found(sku="MILK-FRESH-1L") == ["MILK-FRESH-1L"]

    def test_input_is_normalized_like_stored_skus(self):
        assert found(sku="  milk-fresh-1l ") == ["MILK-FRESH-1L"]

    def test_partial_sku_does_not_match(self):
        """`sku` is an exact lookup for integrations; partial matching is what `text` is for."""
        assert found(sku="MILK") == []


class TestPriceRange:
    def test_minimum_is_inclusive(self):
        assert set(found(min_price=Decimal("4.10"))) == {"BUTTER-250", "CHEESE-FETA-400"}

    def test_maximum_is_inclusive(self):
        assert set(found(max_price=Decimal("1.60"))) == {"BREAD-WHITE", "WATER-15"}

    def test_both_bounds(self):
        assert set(found(min_price=Decimal("1.99"), max_price=Decimal("3.99"))) == {
            "MLYAKO-3",
            "MILK-FRESH-1L",
            "MILK-OAT-1L",
        }

    def test_equal_bounds_select_a_single_price(self):
        assert found(min_price=Decimal("2.49"), max_price=Decimal("2.49")) == ["MILK-FRESH-1L"]

    def test_range_without_products_is_empty(self):
        assert found(min_price=Decimal("100")) == []


class TestCategory:
    def test_leaf_category_returns_its_own_products(self, categories):
        assert found(category=categories["cheese"]) == ["CHEESE-FETA-400"]

    def test_parent_category_includes_products_of_every_level_below(self, categories):
        assert set(found(category=categories["dairy"])) == {
            "BUTTER-250",  # directly in Dairy
            "MILK-FRESH-1L",  # Dairy > Milk
            "MILK-OAT-1L",
            "MLYAKO-3",
            "CHEESE-FETA-400",  # Dairy > Cheese
        }

    def test_other_branches_are_excluded(self, categories):
        results = found(category=categories["food"])

        assert "WATER-15" not in results  # Drinks is not under Food
        assert len(results) == 6

    def test_products_of_ancestors_are_excluded(self, categories):
        assert "BUTTER-250" not in found(category=categories["milk"])

    def test_category_without_products_is_empty(self):
        assert found(category=CategoryFactory(name="Frozen")) == []

    def test_category_whose_id_is_a_prefix_of_another_id(self):
        """Category 9007 must not pick up the products of category 90070."""
        short = CategoryFactory(pk=9007)
        long = CategoryFactory(pk=90070)
        ProductFactory(sku="IN-SHORT", category=short)
        ProductFactory(sku="IN-LONG", category=long)
        ProductFactory(sku="UNDER-LONG", category=CategoryFactory(parent=long))

        assert found(category=short) == ["IN-SHORT"]


class TestCombinedCriteria:
    def test_all_criteria_must_hold(self, categories):
        results = found(
            text="milk",
            min_price=Decimal("2.00"),
            max_price=Decimal("3.00"),
            category=categories["dairy"],
        )

        assert results == ["MILK-FRESH-1L"]

    def test_criteria_that_exclude_each_other_give_nothing(self, categories):
        assert found(text="milk", category=categories["bakery"]) == []


class TestOrdering:
    def test_sorted_by_title_by_default(self, categories):
        assert found(category=categories["milk"]) == ["MILK-FRESH-1L", "MILK-OAT-1L", "MLYAKO-3"]

    def test_cheapest_first(self, categories):
        assert found(category=categories["milk"], ordering=ProductOrdering.PRICE) == [
            "MLYAKO-3",
            "MILK-FRESH-1L",
            "MILK-OAT-1L",
        ]

    def test_most_expensive_first(self, categories):
        assert found(category=categories["milk"], ordering=ProductOrdering.PRICE_DESC) == [
            "MILK-OAT-1L",
            "MILK-FRESH-1L",
            "MLYAKO-3",
        ]

    def test_title_descending(self, categories):
        assert found(category=categories["milk"], ordering=ProductOrdering.TITLE_DESC) == [
            "MLYAKO-3",
            "MILK-OAT-1L",
            "MILK-FRESH-1L",
        ]

    def test_newest_first(self):
        newest = ProductFactory(sku="JUST-ADDED")
        # auto_now_add cannot be passed in; set it explicitly so the test does
        # not depend on how fast the rows above were inserted.
        Product.objects.filter(pk=newest.pk).update(created_at=timezone.now() + timedelta(days=1))

        assert found(ordering=ProductOrdering.NEWEST)[0] == "JUST-ADDED"
        assert found(ordering=ProductOrdering.OLDEST)[-1] == "JUST-ADDED"

    def test_equal_sort_values_keep_a_stable_order(self, categories):
        """Without a tie-breaker, rows with the same price could swap between pages."""
        first = ProductFactory(
            sku="SAME-PRICE-A", price=Decimal("7.77"), category=categories["bakery"]
        )
        second = ProductFactory(
            sku="SAME-PRICE-B", price=Decimal("7.77"), category=categories["bakery"]
        )

        for _ in range(3):
            results = found(
                min_price=Decimal("7.77"), max_price=Decimal("7.77"), ordering=ProductOrdering.PRICE
            )
            assert results == [first.sku, second.sku]

    @pytest.mark.parametrize("ordering", list(ProductOrdering))
    def test_every_ordering_ends_with_the_id(self, ordering):
        """
        The test above cannot fail on InnoDB: its indexes happen to return equal
        values in primary-key order. That is luck, not a guarantee, so the
        tie-breaker is checked where it is promised - in the query itself.
        """
        products = search_products(ProductSearchCriteria(ordering=ordering))

        assert products.query.order_by == (ordering.value, "id")


class TestQuerySetFilters:
    """The building blocks are usable on their own, outside search_products()."""

    def test_filters_without_arguments_do_not_filter(self):
        assert Product.objects.matching_terms([]).count() == 7
        assert Product.objects.priced_between(None, None).count() == 7

    def test_filters_chain_in_any_order(self, categories):
        one_way = (
            Product.objects.in_category_tree(categories["dairy"])
            .priced_between(Decimal("2"), Decimal("4"))
            .matching_terms(["milk"])
        )
        other_way = (
            Product.objects.matching_terms(["milk"])
            .priced_between(Decimal("2"), Decimal("4"))
            .in_category_tree(categories["dairy"])
        )

        assert set(one_way) == set(other_way)
        assert {product.sku for product in one_way} == {"MILK-FRESH-1L", "MILK-OAT-1L"}


class TestQueryCost:
    def test_one_query_however_many_products_and_categories(
        self, categories, django_assert_num_queries
    ):
        criteria = ProductSearchCriteria(text="milk", category=categories["food"])

        with django_assert_num_queries(1):
            names = [product.category.name for product in search_products(criteria)]

        assert names == ["Milk", "Milk"]
