"""
ProductSearchCriteria is a plain value object: these tests need no database.
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from catalog.exceptions import InvalidSearchCriteria
from catalog.search import ProductOrdering, ProductSearchCriteria


class TestDefaults:
    def test_empty_criteria_filter_nothing_and_sort_by_title(self):
        criteria = ProductSearchCriteria()

        assert criteria.text is None
        assert criteria.sku is None
        assert criteria.min_price is None
        assert criteria.max_price is None
        assert criteria.category is None
        assert criteria.ordering is ProductOrdering.TITLE


class TestNormalization:
    def test_text_is_trimmed(self):
        assert ProductSearchCriteria(text="  fresh milk ").text == "fresh milk"

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_text_means_no_text_filter(self, blank):
        assert ProductSearchCriteria(text=blank).text is None

    def test_terms_are_the_words_of_the_text(self):
        assert ProductSearchCriteria(text=" fresh   milk ").terms == ("fresh", "milk")

    def test_no_text_means_no_terms(self):
        assert ProductSearchCriteria().terms == ()

    def test_sku_is_compared_in_its_canonical_form(self):
        assert ProductSearchCriteria(sku=" milk-1l ").sku == "MILK-1L"

    def test_blank_sku_means_no_sku_filter(self):
        assert ProductSearchCriteria(sku="  ").sku is None


class TestPriceRange:
    def test_open_ended_ranges_are_valid(self):
        assert ProductSearchCriteria(min_price=Decimal("5")).max_price is None
        assert ProductSearchCriteria(max_price=Decimal("5")).min_price is None

    def test_a_single_price_is_a_valid_range(self):
        criteria = ProductSearchCriteria(min_price=Decimal("5.00"), max_price=Decimal("5.00"))

        assert criteria.min_price == criteria.max_price

    def test_minimum_above_maximum_is_rejected(self):
        with pytest.raises(InvalidSearchCriteria) as error:
            ProductSearchCriteria(min_price=Decimal("10"), max_price=Decimal("9.99"))

        assert error.value.field == "max_price"

    @pytest.mark.parametrize("field", ["min_price", "max_price"])
    def test_negative_prices_are_rejected(self, field):
        with pytest.raises(InvalidSearchCriteria) as error:
            ProductSearchCriteria(**{field: Decimal("-0.01")})

        assert error.value.field == field


class TestImmutability:
    def test_criteria_cannot_be_changed_after_validation(self):
        criteria = ProductSearchCriteria(max_price=Decimal("5"))

        with pytest.raises(FrozenInstanceError):
            criteria.max_price = Decimal("-1")
