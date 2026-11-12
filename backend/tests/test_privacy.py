import json
import re
import sqlite3

import pytest

from ledgerline import db
from ledgerline.extraction import (
    _few_shot_examples,
    build_extraction_prompt,
    build_masked_vendors_csv,
    build_verification_prompt,
    mask_bank_account,
)
from ledgerline.llm.fake import CANNED_EXTRACTION, FakeClient
from ledgerline.models import Invoice, LineItem
from ledgerline.pipeline import process_document
from ledgerline.reference import load_reference
from ledgerline.validation import check_bank_details


@pytest.fixture
def ref():
    return load_reference()


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_mask_bank_account_helper():
    assert mask_bank_account(None) is None
    assert mask_bank_account("") == ""
    assert mask_bank_account("1234") == "****"
    assert mask_bank_account("50200012345678") == "**********5678"
    assert mask_bank_account("  31840100077120  ") == "**********7120"
    assert mask_bank_account("5020-0012-3456 78") == "**********5678"
    assert mask_bank_account(" 5020 0012 3456 78 ") == "**********5678"


def test_prompts_do_not_contain_full_bank_account_numbers(ref):
    prompt = build_extraction_prompt("SAMPLE INVOICE TEXT", ref)
    # Check every vendor bank account with >4 digits
    for vendor in ref.vendors:
        acct = vendor.get("bank_account")
        if acct and len(acct.strip()) > 4:
            clean_acct = acct.strip()
            assert clean_acct not in prompt, f"Vendor bank account {clean_acct} leaked in extraction prompt!"


def test_verification_prompt_does_not_contain_full_bank_account(ref):
    extraction = dict(CANNED_EXTRACTION)
    prompt = build_verification_prompt("SAMPLE INVOICE TEXT", extraction, ref)
    acct = extraction.get("bank_account")
    if acct and len(acct.strip()) > 4:
        assert acct.strip() not in prompt, f"Extracted bank account {acct} leaked in verification prompt!"
    for vendor in ref.vendors:
        vacct = vendor.get("bank_account")
        if vacct and len(vacct.strip()) > 4:
            assert vacct.strip() not in prompt, f"Vendor bank account {vacct} leaked in verification prompt!"


def test_few_shot_examples_do_not_leak_full_bank_accounts(ref):
    examples = _few_shot_examples(ref=ref, limit=3)
    for vendor in ref.vendors:
        acct = vendor.get("bank_account")
        if acct and len(acct.strip()) > 4:
            assert acct.strip() not in examples, f"Vendor bank account {acct} leaked in few-shot examples!"


def test_original_bank_details_preserved_in_reference_data(ref):
    # Reference data for local validation must retain full original bank details
    sitara = next(v for v in ref.vendors if v["legal_name"] == "Sitara Industrial Supplies Pvt. Ltd.")
    assert sitara["bank_account"] == "50200012345678"
    assert sitara["ifsc"] == "ZEPH0001234"


def test_bank_account_change_detected():
    vendor = {
        "legal_name": "Sitara Industrial Supplies Pvt. Ltd.",
        "bank_account": "50200012345678",
        "ifsc": "ZEPH0001234",
    }
    # Invoice has a changed bank account number, same IFSC
    inv = Invoice(
        document_type="invoice",
        supplier_name="Sitara Industrial Supplies Pvt. Ltd.",
        supplier_gstin="27AAKCS4821M1ZW",
        invoice_number="SIS/26-27/0051",
        invoice_date=None,
        currency="INR",
        subtotal=100.0,
        cgst=9.0,
        sgst=9.0,
        igst=0.0,
        round_off=0.0,
        total=118.0,
        bank_account="50200012349999",  # Changed!
        ifsc="ZEPH0001234",
        po_number=None,
        notes=None,
        line_items=[],
    )
    reasons = check_bank_details(inv, vendor)
    assert "bank_details_changed" in reasons, "check_bank_details failed to detect changed bank account number!"


def test_ifsc_comparison_case_insensitive():
    vendor = {
        "legal_name": "Sitara Industrial Supplies Pvt. Ltd.",
        "bank_account": "50200012345678",
        "ifsc": "ZEPH0001234",
    }
    # Matching account and lowercase IFSC should NOT flag changed
    inv_matching = Invoice(
        document_type="invoice",
        supplier_name="Sitara Industrial Supplies Pvt. Ltd.",
        supplier_gstin="27AAKCS4821M1ZW",
        invoice_number="SIS/26-27/0042",
        invoice_date=None,
        currency="INR",
        subtotal=100.0,
        cgst=9.0,
        sgst=9.0,
        igst=0.0,
        round_off=0.0,
        total=118.0,
        bank_account="50200012345678",
        ifsc="zeph0001234",  # lowercase
        po_number=None,
        notes=None,
        line_items=[],
    )
    assert check_bank_details(inv_matching, vendor) == []


def test_ifsc_change_detected_when_account_matches():
    vendor = {
        "legal_name": "Sitara Industrial Supplies Pvt. Ltd.",
        "bank_account": "50200012345678",
        "ifsc": "ZEPH0001234",
    }
    inv = Invoice(
        document_type="invoice",
        supplier_name="Sitara Industrial Supplies Pvt. Ltd.",
        supplier_gstin="27AAKCS4821M1ZW",
        invoice_number="SIS/26-27/0042",
        invoice_date=None,
        currency="INR",
        subtotal=100.0,
        cgst=9.0,
        sgst=9.0,
        igst=0.0,
        round_off=0.0,
        total=118.0,
        bank_account="50200012345678",
        ifsc="ORBK0001109",  # Changed IFSC!
        po_number=None,
        notes=None,
        line_items=[],
    )
    assert "bank_details_changed" in check_bank_details(inv, vendor)


def test_formatting_normalization_with_leading_zero_preservation():
    vendor = {
        "legal_name": "Sitara Industrial Supplies Pvt. Ltd.",
        "bank_account": "50200012345678",
        "ifsc": "ZEPH0001234",
    }
    # Formatted with spaces and dashes -> matches
    inv_formatted = Invoice(
        document_type="invoice",
        supplier_name="Sitara Industrial Supplies Pvt. Ltd.",
        supplier_gstin="27AAKCS4821M1ZW",
        invoice_number="SIS/26-27/0042",
        invoice_date=None,
        currency="INR",
        subtotal=100.0,
        cgst=9.0,
        sgst=9.0,
        igst=0.0,
        round_off=0.0,
        total=118.0,
        bank_account="5020-0012 3456 78",
        ifsc="ZEPH0001234",
        po_number=None,
        notes=None,
        line_items=[],
    )
    assert check_bank_details(inv_formatted, vendor) == []

    # Leading zero difference -> does NOT match (leading zeros preserved)
    inv_leading_zero = Invoice(
        document_type="invoice",
        supplier_name="Sitara Industrial Supplies Pvt. Ltd.",
        supplier_gstin="27AAKCS4821M1ZW",
        invoice_number="SIS/26-27/0042",
        invoice_date=None,
        currency="INR",
        subtotal=100.0,
        cgst=9.0,
        sgst=9.0,
        igst=0.0,
        round_off=0.0,
        total=118.0,
        bank_account="050200012345678",  # Extra leading zero
        ifsc="ZEPH0001234",
        po_number=None,
        notes=None,
        line_items=[],
    )
    assert "bank_details_changed" in check_bank_details(inv_leading_zero, vendor)


def test_no_bank_details_on_invoice_does_not_flag():
    vendor = {
        "legal_name": "Sitara Industrial Supplies Pvt. Ltd.",
        "bank_account": "50200012345678",
        "ifsc": "ZEPH0001234",
    }
    inv_no_bank = Invoice(
        document_type="invoice",
        supplier_name="Sitara Industrial Supplies Pvt. Ltd.",
        supplier_gstin="27AAKCS4821M1ZW",
        invoice_number="SIS/26-27/0042",
        invoice_date=None,
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
        line_items=[],
    )
    assert check_bank_details(inv_no_bank, vendor) == []


def test_unknown_vendor_does_not_flag_bank_change():
    inv = Invoice(
        document_type="invoice",
        supplier_name="Unknown Vendor",
        supplier_gstin=None,
        invoice_number="INV-001",
        invoice_date=None,
        currency="INR",
        subtotal=100.0,
        cgst=0.0,
        sgst=0.0,
        igst=0.0,
        round_off=0.0,
        total=100.0,
        bank_account="1234567890",
        ifsc="ABCD0123456",
        po_number=None,
        notes=None,
        line_items=[],
    )
    assert check_bank_details(inv, None) == []


def test_error_and_log_privacy(conn, ref, monkeypatch):
    # Simulate an exception containing an account number during processing
    def bomb(*args, **kwargs):
        raise ValueError("Failed while resolving account 50200012345678 for vendor")

    from ledgerline import pipeline

    monkeypatch.setattr(pipeline, "extract", bomb)

    inv = process_document(conn, ref, "fake raw text", "err_doc_01")
    assert "50200012345678" not in inv["notes"], "Full bank account leaked into error notes!"
    assert "**********5678" in inv["notes"]
