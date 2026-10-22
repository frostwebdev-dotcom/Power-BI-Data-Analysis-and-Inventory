"""Approved vendor rosters reject incomplete, ambiguous or malformed input."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailedError
from app.services.vendor_roster import parse_roster


def test_csv_handles_bom_quotes_normalization_and_commercial_terms() -> None:
    rows, payloads = parse_roster(
        "\ufeffcode,name,contact_email,payment_terms,minimum_order_value\n"
        ' nyb ,"NYB International, Inc",SALES@NYB.TEST,Net 30,250.00\n'
    )
    assert rows[0].code == "NYB"
    assert rows[0].name == "NYB International, Inc"
    assert rows[0].action == "create"
    assert payloads[0] is not None
    assert payloads[0].contact_email == "sales@nyb.test"
    assert payloads[0].purchasing_terms == {"payment": "Net 30"}
    assert str(payloads[0].minimum_order_value) == "250.00"


@pytest.mark.parametrize(
    "content",
    [
        "code,name\nAA,One\naa,Two\n",
        "code,name\nAA,One Supplier\nBB, one   supplier \n",
        "code,name,contact_email\nAA,One,invalid\n",
        "code,name,default_lead_time_days\nAA,One,-1\n",
        "code,name\nAA\n",
        "code,name\nAA,One,Extra\n",
        "code,name\nAA,\n",
        "code,name,purchasing_terms\nAA,One,[]\n",
    ],
)
def test_bad_rows_are_not_ready_for_creation(content: str) -> None:
    rows, payloads = parse_roster(content)
    assert rows[-1].action == "invalid"
    assert rows[-1].issues
    assert payloads[-1] is None


@pytest.mark.parametrize(
    "content",
    [
        "name\nOne\n",
        "code,name,name\nAA,One,Two\n",
        "code,name,unknown\nAA,One,x\n",
        'code,name\nAA,"unclosed\n',
        "code,name\n",
        "code,name\n" + "".join(f"V{i},Vendor {i}\n" for i in range(501)),
    ],
)
def test_bad_files_are_refused(content: str) -> None:
    with pytest.raises(ValidationFailedError):
        parse_roster(content)
