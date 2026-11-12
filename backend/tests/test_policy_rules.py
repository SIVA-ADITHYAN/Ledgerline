from datetime import date
import sqlite3

import pytest

from ledgerline import db
from ledgerline.models import Invoice, LineItem
from ledgerline.pipeline import process_document
from ledgerline.reference import load_reference
from ledgerline.validation import (
    check_credit_note,
    check_duplicate,
    check_gstin,
    check_tax_type,
    check_totals,
    check_vendor,
    find_vendor,
    gstin_checksum,
    is_valid_gstin,
    run_checks,
)

try:
    from ledgerline.validation import check_vendor_new
except ImportError:
    check_vendor_new = None

SITARA_GSTIN = "27AAKCS4821M1ZW"
NARMADA_GSTIN = "24AAOFN2207Q1ZX"


@pytest.fixture
def ref():
    return load_reference()


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def make_inv(**overrides) -> Invoice:
    fields = dict(
        document_type="invoice",
        supplier_name="Sitara Industrial Supplies Pvt. Ltd.",
        supplier_gstin=SITARA_GSTIN,
        invoice_number="SIS/26-27/0042",
        invoice_date=date(2026, 4, 25),
        currency="INR",
        subtotal=100.0,
        cgst=9.0,
        sgst=9.0,
        igst=0.0,
        round_off=0.0,
        total=118.0,
        bank_account=None,
        ifsc=None,
        po_number=None,
        notes=None,
        line_items=[LineItem("Widget", 1.0, 100.0, 100.0)],
    )
    fields.update(overrides)
    return Invoice(**fields)


# ==============================================================================
# Task 2: GSTIN Checksum & Validation
# ==============================================================================

def test_valid_gstin_checksum():
    # Worked example from AP_POLICY.md Appendix A
    assert is_valid_gstin("27AAPFU0939F1ZV")


def test_invalid_gstin_format():
    assert not is_valid_gstin("27AAKCS4821M")  # Too short
    assert not is_valid_gstin("27AAKCS4821M1Z")  # 14 chars
    assert not is_valid_gstin("27AAKCS4821M1ZW99")  # Too long
    assert not is_valid_gstin("27AAKCS4821M!ZW")  # Special char


def test_invalid_gstin_checksum_despite_valid_format():
    # Format matches regex, but check character is 'X' instead of 'V'
    assert not is_valid_gstin("27AAPFU0939F1ZX")


def test_gstin_lowercase_handling():
    # Lowercase input should be normalized and validated
    assert is_valid_gstin("27aapfu0939f1zv")


def test_missing_gstin_rejected():
    assert not is_valid_gstin(None)
    assert not is_valid_gstin("")
    assert not is_valid_gstin("   ")


def test_vendor_master_gstins_valid(ref):
    # All vendor master GSTINs must pass validation
    for vendor in ref.vendors:
        g = vendor.get("gstin")
        if g:
            assert is_valid_gstin(g), f"Vendor GSTIN {g} failed validation!"


# ==============================================================================
# Task 3: Duplicate Detection
# ==============================================================================

def test_duplicate_same_domestic_gstin_and_normalized_number(conn, ref):
    # In paid_invoices.csv: Sitara has SIS/26-27/0018 on 2026-04-02 (FY 2026-27)
    inv = make_inv(
        supplier_gstin=SITARA_GSTIN,
        invoice_number="SIS / 26-27 / 0018",  # formatting variation with spaces
        invoice_date=date(2026, 4, 15),       # same FY 2026-27
    )
    assert check_duplicate(conn, ref, inv) == ["duplicate"]


def test_duplicate_different_gstin_same_number_not_duplicate(conn, ref):
    # Same invoice number but different vendor GSTIN is NOT a duplicate
    inv = make_inv(
        supplier_gstin=NARMADA_GSTIN,
        invoice_number="SIS/26-27/0018",
        invoice_date=date(2026, 4, 15),
    )
    assert check_duplicate(conn, ref, inv) == []


def test_duplicate_different_financial_year_not_duplicate(conn, ref):
    # Same supplier and invoice number, but in FY 2025-26 instead of FY 2026-27
    inv = make_inv(
        supplier_gstin=SITARA_GSTIN,
        invoice_number="SIS/26-27/0018",
        invoice_date=date(2026, 3, 25),  # March 2026 -> FY 2025-26
    )
    assert check_duplicate(conn, ref, inv) == []


def test_duplicate_formatting_variations(conn, ref):
    variations = [
        "SIS-26-27-0018",
        "SIS.26.27.0018",
        "#SIS26270018",
        "SIS / 26 - 27 / 0018",
        "SIS26270018",
    ]
    for var in variations:
        inv = make_inv(
            supplier_gstin=SITARA_GSTIN,
            invoice_number=var,
            invoice_date=date(2026, 4, 20),
        )
        assert check_duplicate(conn, ref, inv) == ["duplicate"], f"Variation {var} not recognized as duplicate!"


def test_duplicate_leading_zeros_preserved(conn, ref):
    # Malwa Steel Traders has invoice '0007' on 2025-06-10 in paid_invoices.csv
    # '7' must NOT match '0007' because leading zeros are significant
    malwa_gstin = "23AAHFM7702K1Z9"
    inv_different = make_inv(
        supplier_gstin=malwa_gstin,
        supplier_name="Malwa Steel Traders",
        invoice_number="7",
        invoice_date=date(2025, 6, 15),
    )
    assert check_duplicate(conn, ref, inv_different) == []


def test_duplicate_same_batch(conn, ref):
    # First invoice in batch is processed and recorded in DB
    inv1 = make_inv(
        invoice_number="NEW-INV-999",
        invoice_date=date(2026, 5, 10),
    )
    # Simulate DB entry from earlier document in batch
    conn.execute(
        """INSERT INTO invoices (org_id, document_id, filename, raw_text, document_type, supplier_name,
               supplier_gstin, invoice_number, invoice_date, currency, subtotal, cgst, sgst, igst, round_off,
               total, bank_account, ifsc, po_number, notes, line_items_json, warnings_json, needs_review,
               review_reasons_json, required_approvals, status)
           VALUES ('meridian', 'doc_1', 'doc_1.txt', '', 'invoice', ?, ?, ?, ?, 'INR', 100, 9, 9, 0, 0,
                   118, NULL, NULL, NULL, '', '[]', '[]', 0, '[]', 1, 'ready_for_approval')""",
        (inv1.supplier_name, inv1.supplier_gstin, inv1.invoice_number, inv1.invoice_date.isoformat()),
    )
    conn.commit()

    # Second invoice in same batch with formatting differences
    inv2 = make_inv(
        invoice_number="NEW / INV / 999",
        invoice_date=date(2026, 5, 20),  # Same FY 2026-27
    )
    assert check_duplicate(conn, ref, inv2) == ["duplicate"]


def test_duplicate_foreign_supplier_matching(conn, ref):
    # CloudNimbus Inc. has invoice CN-2026-03288 on 2026-03-03 in paid_invoices.csv
    inv_foreign = make_inv(
        currency="USD",
        supplier_gstin=None,
        supplier_name="Cloud-Nimbus, Inc.",  # Case/punctuation variation
        invoice_number="CN / 2026 / 03288",
        invoice_date=date(2026, 3, 10),      # FY 2025-26
    )
    assert check_duplicate(conn, ref, inv_foreign) == ["duplicate"]


def test_duplicate_credit_note_excluded(conn, ref):
    # Credit note matching an existing invoice number must NOT be flagged duplicate
    cn = make_inv(
        document_type="credit_note",
        supplier_gstin=SITARA_GSTIN,
        invoice_number="SIS/26-27/0018",
        invoice_date=date(2026, 4, 15),
    )
    assert check_duplicate(conn, ref, cn) == []


# ==============================================================================
# Task 4: Vendor Onboarding Validation (vendor_new)
# ==============================================================================

def test_vendor_new_recently_onboarded():
    # Onboarded 2026-03-20, invoice date 2026-04-10 (21 days < 90)
    assert check_vendor_new is not None, "check_vendor_new is not implemented!"
    vendor = {
        "legal_name": "Shakti Castings Pvt. Ltd.",
        "onboarded_on": "2026-03-20",
    }
    inv = make_inv(invoice_date=date(2026, 4, 10))
    assert check_vendor_new(inv, vendor) == ["vendor_new"]


def test_vendor_new_exactly_90_days():
    # 2026 is non-leap year: Jan (31) + Feb (28) + Mar (31) = 90 days
    # Onboarded 2026-01-01, invoice 2026-04-01 is exactly 90 days -> not fewer than 90
    assert check_vendor_new is not None, "check_vendor_new is not implemented!"
    vendor = {
        "legal_name": "Test Vendor",
        "onboarded_on": "2026-01-01",
    }
    inv = make_inv(invoice_date=date(2026, 4, 1))
    assert check_vendor_new(inv, vendor) == []


def test_vendor_new_more_than_90_days():
    # Onboarded 2025-01-01, invoice 2026-04-01 (>400 days)
    assert check_vendor_new is not None, "check_vendor_new is not implemented!"
    vendor = {
        "legal_name": "Sitara Industrial Supplies Pvt. Ltd.",
        "onboarded_on": "2023-02-10",
    }
    inv = make_inv(invoice_date=date(2026, 4, 1))
    assert check_vendor_new(inv, vendor) == []


def test_vendor_new_missing_onboarded_date():
    assert check_vendor_new is not None, "check_vendor_new is not implemented!"
    vendor = {
        "legal_name": "Unknown Onboarding Vendor",
        "onboarded_on": "",
    }
    inv = make_inv(invoice_date=date(2026, 4, 1))
    assert check_vendor_new(inv, vendor) == []


def test_vendor_new_uses_invoice_date_not_processing_date():
    # If today is in November 2026, but invoice was dated 2026-03-25 for vendor onboarded 2026-03-20
    # Difference is 5 days (< 90) -> must flag vendor_new based on invoice_date!
    assert check_vendor_new is not None, "check_vendor_new is not implemented!"
    vendor = {
        "legal_name": "Shakti Castings Pvt. Ltd.",
        "onboarded_on": "2026-03-20",
    }
    inv = make_inv(invoice_date=date(2026, 3, 25))
    assert check_vendor_new(inv, vendor) == ["vendor_new"]


def test_totals_calculation_uses_decimal_precision():
    # Exactly 1.00 difference is within tolerance
    inv_exact_tolerance = make_inv(
        total=119.00,  # items(100) + cgst(9) + sgst(9) = 118.00; diff is 1.00
    )
    assert check_totals(inv_exact_tolerance) == []

    # 1.01 difference exceeds tolerance
    inv_over_tolerance = make_inv(
        total=119.01,
    )
    assert check_totals(inv_over_tolerance) == ["totals_mismatch"]
