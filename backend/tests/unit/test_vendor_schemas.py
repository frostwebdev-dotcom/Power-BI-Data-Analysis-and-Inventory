"""Vendor schema validation (AC-4). Mirrors the database constraints exactly."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.enums import VendorStatus
from app.schemas.vendors import (
    VendorContactCreate,
    VendorContactUpdate,
    VendorCreate,
    VendorUpdate,
)


def create(**overrides: object) -> VendorCreate:
    payload: dict[str, object] = {"code": "acme", "name": "Acme Distribution"}
    payload.update(overrides)
    return VendorCreate(**payload)


class TestVendorCreate:
    def test_minimal_payload_applies_defaults(self) -> None:
        vendor = create()

        assert vendor.code == "ACME"  # upper-cased, as the check constraint requires
        assert vendor.status is VendorStatus.ACTIVE
        assert vendor.currency == "USD"
        assert vendor.timezone == "UTC"
        assert vendor.purchasing_terms == {}
        assert vendor.minimum_order_quantity is None

    @pytest.mark.parametrize("code", ["acme", " ACME ", "ac-me_1", "A1"])
    def test_codes_are_normalised(self, code: str) -> None:
        assert create(code=code).code == code.strip().upper()

    @pytest.mark.parametrize("code", ["A", "-ACME", "ACME CO", "acme.co", "X" * 33, ""])
    def test_bad_codes_are_refused(self, code: str) -> None:
        with pytest.raises(ValidationError, match="code"):
            create(code=code)

    @pytest.mark.parametrize("currency", ["usd", " eur ", "GBP"])
    def test_currency_is_upper_cased_iso_4217(self, currency: str) -> None:
        assert create(currency=currency).currency == currency.strip().upper()

    @pytest.mark.parametrize("currency", ["US", "USDD", "U5D", "$"])
    def test_bad_currency_is_refused(self, currency: str) -> None:
        with pytest.raises(ValidationError, match="currency"):
            create(currency=currency)

    def test_blank_name_is_refused(self) -> None:
        with pytest.raises(ValidationError, match=r"blank|at least"):
            create(name="   ")

    @pytest.mark.parametrize("email", ["buyer@acme.test", " Buyer@Acme.Test "])
    def test_contact_email_is_normalised(self, email: str) -> None:
        assert create(contact_email=email).contact_email == "buyer@acme.test"

    @pytest.mark.parametrize("email", ["buyer", "buyer@", "@acme.test", "buyer@acme", "a b@c.d"])
    def test_bad_contact_email_is_refused(self, email: str) -> None:
        with pytest.raises(ValidationError, match="email"):
            create(contact_email=email)

    def test_empty_contact_email_becomes_none(self) -> None:
        assert create(contact_email="").contact_email is None

    @pytest.mark.parametrize(
        "field", ["default_lead_time_days", "minimum_order_quantity", "minimum_order_value"]
    )
    def test_negative_numbers_are_refused(self, field: str) -> None:
        with pytest.raises(ValidationError, match=field):
            create(**{field: -1})

    def test_minimum_order_value_keeps_two_decimals(self) -> None:
        vendor = create(minimum_order_value="250.50")

        assert vendor.minimum_order_value == Decimal("250.50")

    def test_minimum_order_value_with_too_many_decimals_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="decimal"):
            create(minimum_order_value="0.001")

    def test_purchasing_terms_is_free_form(self) -> None:
        terms = {"payment": "Net 30", "freight": "FOB origin", "cutoff": "14:00 ET"}

        assert create(purchasing_terms=terms).purchasing_terms == terms

    def test_purchasing_terms_must_be_an_object(self) -> None:
        with pytest.raises(ValidationError, match="purchasing_terms"):
            create(purchasing_terms=["Net 30"])


class TestVendorUpdate:
    def test_only_set_fields_are_reported(self) -> None:
        update = VendorUpdate(name="New Name")

        assert update.model_dump(exclude_unset=True) == {"name": "New Name"}

    def test_code_cannot_be_changed(self) -> None:
        """The business key is not part of the update schema at all."""
        assert "code" not in VendorUpdate.model_fields

    def test_the_same_rules_apply_on_update(self) -> None:
        assert VendorUpdate(currency="eur").currency == "EUR"
        with pytest.raises(ValidationError):
            VendorUpdate(currency="euro")
        with pytest.raises(ValidationError):
            VendorUpdate(minimum_order_quantity=-5)

    def test_an_explicit_null_clears_a_field(self) -> None:
        update = VendorUpdate(notes=None)

        assert update.model_dump(exclude_unset=True) == {"notes": None}


class TestVendorContact:
    def test_email_is_normalised_and_required(self) -> None:
        contact = VendorContactCreate(name="Ann", email=" Ann@Acme.Test ")

        assert contact.email == "ann@acme.test"
        assert contact.is_primary is False

    def test_bad_email_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="email"):
            VendorContactCreate(name="Ann", email="not-an-address")

    def test_blank_name_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            VendorContactCreate(name=" ", email="ann@acme.test")

    def test_update_is_partial(self) -> None:
        assert VendorContactUpdate(is_primary=True).model_dump(exclude_unset=True) == {
            "is_primary": True
        }
