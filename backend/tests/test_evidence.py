from datetime import date
from pathlib import Path
import pytest

from ledgerline.evidence import extract_evidence


SAMPLE_TEXT_INVOICE = """                      SITARA INDUSTRIAL SUPPLIES PVT. LTD.                     
                   Bhosari, Pune - Manufacturers & Suppliers                   
GSTIN/UIN: 27AAKCS4821M1ZW          State Name : Maharashtra, Code : 27
                                 TAX INVOICE                                  
------------------------------------------------------------------------------
Invoice No. : SIS/26-27/0042               Dated : 25/04/2026
Buyer (Bill to) : Meridian Precision Components Pvt. Ltd.
                  Gat No. 112, Chakan Industrial Area Phase II, Pune, Maharashtra 410501
GSTIN/UIN : 27AAECM7310K1ZE    State Name : Maharashtra, Code : 27
Buyer's Order No. : PO-2026-0117
------------------------------------------------------------------------------
Sl  Description of Goods                     HSN/SAC     Qty Unit       Rate       Amount
 1  SS Hex Bolt M10x40 (Box/100)             7318         12 BOX    1,450.00    17,400.00
 2  Spring Washer M10 (Pkt/500)              7318          4 PKT      620.00     2,480.00
------------------------------------------------------------------------------
                                                Sub Total      19,880.00
                                                CGST @ 9%       1,789.20
                                                SGST @ 9%       1,789.20
                                                Round Off          -0.40
                                                 TOTAL  ₹      23,458.00
Amount Chargeable (in words): INR Twenty Three Thousand Four Hundred Fifty Eight Only
Company's Bank Details
  Bank Name : Zephyr Bank, Bhosari
  A/c No.   : 50200012345678
  IFS Code  : ZEPH0001234
                                      for Sitara Industrial Supplies Pvt. Ltd.
                                                          Authorised Signatory
"""


def test_exact_character_offsets():
    data = {
        "invoice_number": "SIS/26-27/0042",
        "invoice_date": "2026-04-25",
        "supplier_gstin": "27AAKCS4821M1ZW",
        "supplier_name": "SITARA INDUSTRIAL SUPPLIES PVT. LTD.",
        "total": 23458.0,
        "subtotal": 19880.0,
        "cgst": 1789.2,
        "sgst": 1789.2,
        "igst": 0.0,
        "bank_account": "50200012345678",
        "ifsc": "ZEPH0001234",
        "po_number": "PO-2026-0117",
    }
    evidence = extract_evidence(SAMPLE_TEXT_INVOICE, data)
    assert isinstance(evidence, dict)

    # Invariant: raw_text[start:end] == text for all returned fields
    for field, ev in evidence.items():
        assert "start" in ev
        assert "end" in ev
        assert "text" in ev
        start, end, text = ev["start"], ev["end"], ev["text"]
        assert SAMPLE_TEXT_INVOICE[start:end] == text, f"Mismatch for field {field}: '{SAMPLE_TEXT_INVOICE[start:end]}' != '{text}'"

    # Specific field checks
    assert evidence["invoice_number"]["text"] == "SIS/26-27/0042"
    assert evidence["supplier_gstin"]["text"] == "27AAKCS4821M1ZW"
    assert evidence["po_number"]["text"] == "PO-2026-0117"
    assert evidence["bank_account"]["text"] == "50200012345678"
    assert evidence["ifsc"]["text"] == "ZEPH0001234"


def test_repeated_values():
    doc = """
Item 1: Rate 100.00 Qty 1 Amount 100.00
Item 2: Rate 100.00 Qty 1 Amount 100.00
Sub Total: 200.00
Tax: 0.00
Total Amount: 200.00
"""
    data = {
        "subtotal": 200.0,
        "total": 200.0,
    }
    ev = extract_evidence(doc, data)
    assert "subtotal" in ev
    assert "total" in ev
    # Subtotal and Total must point to their respective anchored occurrences
    assert ev["subtotal"]["start"] != ev["total"]["start"]
    assert doc[ev["subtotal"]["start"]:ev["subtotal"]["end"]] == "200.00"
    assert doc[ev["total"]["start"]:ev["total"]["end"]] == "200.00"
    # Subtotal should be near "Sub Total:"
    assert "Sub Total:" in doc[ev["subtotal"]["start"]-15:ev["subtotal"]["start"]]
    # Total should be near "Total Amount:"
    assert "Total Amount:" in doc[ev["total"]["start"]-20:ev["total"]["start"]]


def test_formatted_currency():
    doc = "Invoice total: ₹1,41,600.00\nSubtotal: ₹1,20,000.00"
    data = {
        "total": 141600.0,
        "subtotal": "120000.00",
    }
    ev = extract_evidence(doc, data)
    assert "total" in ev
    assert doc[ev["total"]["start"]:ev["total"]["end"]] == ev["total"]["text"]
    assert ev["total"]["text"] in ("1,41,600.00", "₹1,41,600.00")

    assert "subtotal" in ev
    assert doc[ev["subtotal"]["start"]:ev["subtotal"]["end"]] == ev["subtotal"]["text"]
    assert ev["subtotal"]["text"] in ("1,20,000.00", "₹1,20,000.00")


def test_date_normalization():
    doc = "Invoice No: INV-101   Dated : 25/04/2026"
    data = {
        "invoice_date": "2026-04-25",
    }
    ev = extract_evidence(doc, data)
    assert "invoice_date" in ev
    start, end, text = ev["invoice_date"]["start"], ev["invoice_date"]["end"], ev["invoice_date"]["text"]
    assert doc[start:end] == "25/04/2026"
    assert text == "25/04/2026"


def test_missing_values():
    doc = "Simple Invoice\nTotal: 500.00"
    data = {
        "total": 500.0,
        "po_number": None,
        "igst": 0.0,  # Not in doc
        "bank_account": None,
    }
    ev = extract_evidence(doc, data)
    assert "total" in ev
    assert "po_number" not in ev
    assert "igst" not in ev
    assert "bank_account" not in ev


def test_ambiguous_values():
    # Value '50' appears multiple times with no contextual label
    doc = "50 50 50 50"
    data = {
        "total": 50.0,
    }
    ev = extract_evidence(doc, data)
    # Ambiguous without anchor -> must be omitted rather than guessing
    assert "total" not in ev


def test_leading_zeros():
    doc = "Invoice Number: 0007\nA/c No: 0219201000751"
    data = {
        "invoice_number": "0007",
        "bank_account": "0219201000751",
    }
    ev = extract_evidence(doc, data)
    assert ev["invoice_number"]["text"] == "0007"
    assert doc[ev["invoice_number"]["start"]:ev["invoice_number"]["end"]] == "0007"
    assert ev["bank_account"]["text"] == "0219201000751"
    assert doc[ev["bank_account"]["start"]:ev["bank_account"]["end"]] == "0219201000751"


def test_unicode_and_whitespace():
    doc = "Invoice No. :\t\tINV-888\nTOTAL\t₹\t 99,999.00"
    data = {
        "invoice_number": "INV-888",
        "total": 99999.0,
    }
    ev = extract_evidence(doc, data)
    assert ev["invoice_number"]["text"] == "INV-888"
    assert doc[ev["invoice_number"]["start"]:ev["invoice_number"]["end"]] == "INV-888"
    assert doc[ev["total"]["start"]:ev["total"]["end"]] == ev["total"]["text"]
    assert "99,999.00" in ev["total"]["text"]


def test_bank_account_evidence():
    doc = "Company Bank Account: 50200012345678  IFSC Code: ZEPH0001234"
    data = {
        "bank_account": "50200012345678",
        "ifsc": "ZEPH0001234",
    }
    ev = extract_evidence(doc, data)
    assert ev["bank_account"]["text"] == "50200012345678"
    assert doc[ev["bank_account"]["start"]:ev["bank_account"]["end"]] == "50200012345678"
    assert ev["ifsc"]["text"] == "ZEPH0001234"
    assert doc[ev["ifsc"]["start"]:ev["ifsc"]["end"]] == "ZEPH0001234"


def test_real_dev_invoice_evidence():
    dev_dir = Path(__file__).parent.parent.parent / "data" / "invoices" / "dev"
    if not dev_dir.exists():
        pytest.skip("Dev directory not found")

    # dev_001.txt
    text_001 = (dev_dir / "dev_001.txt").read_text(encoding="utf-8")
    data_001 = {
        "invoice_number": "SIS/26-27/0042",
        "invoice_date": "2026-04-25",
        "supplier_gstin": "27AAKCS4821M1ZW",
        "total": 23458.0,
        "subtotal": 19880.0,
        "cgst": 1789.2,
        "sgst": 1789.2,
        "bank_account": "50200012345678",
        "ifsc": "ZEPH0001234",
        "po_number": "PO-2026-0117",
    }
    ev_001 = extract_evidence(text_001, data_001)
    for k, v in ev_001.items():
        assert text_001[v["start"]:v["end"]] == v["text"]

    assert ev_001["invoice_number"]["text"] == "SIS/26-27/0042"
    assert ev_001["supplier_gstin"]["text"] == "27AAKCS4821M1ZW"
    assert ev_001["bank_account"]["text"] == "50200012345678"


def test_numeric_candidate_not_matching_substring_of_longer_number():
    # Candidate is 100, but text contains '2100' without any standalone 100
    doc = "Some doc text with 2100 items."
    data = {"total": 100.0}
    ev = extract_evidence(doc, data)
    # Must NOT match the '100' inside '2100'
    assert "total" not in ev


def test_generic_total_anchor_does_not_match_subtotal():
    doc = "Sub Total: 100.00\nTotal: 100.00"
    data = {"total": 100.0}
    ev = extract_evidence(doc, data)
    assert "total" in ev
    # Must point to the second line ("Total: 100.00"), not "Sub Total: 100.00"
    start = ev["total"]["start"]
    assert doc[start:ev["total"]["end"]] == "100.00"
    preceding = doc[max(0, start - 15):start]
    assert "Sub Total" not in preceding
    assert "Total:" in preceding


def test_wide_column_spacing():
    # Label and value separated by 56 spaces on the same line, with repeated amount
    spaces = " " * 56
    doc = f"Subtotal{spaces}$1,298.00\nTotal due (USD){spaces}$1,298.00"
    data = {"total": 1298.0}
    ev = extract_evidence(doc, data)
    assert "total" in ev
    assert ev["total"]["text"] == "1,298.00"
    assert doc[ev["total"]["start"]:ev["total"]["end"]] == "1,298.00"
    # Ensure it anchored to Total due, not Subtotal
    assert ev["total"]["start"] > doc.find("Total due")


def test_invoice_number_variation_invoice_prefix():
    # Appears in subject and body as 'invoice VL/0931' without 'No.' or '#'
    doc = "Subject: Invoice VL/0931 - Vardhan Logistics\nPlease find attached our invoice VL/0931 dated 31/03/2026. PO reference: PO-9988."
    data = {
        "invoice_number": "VL/0931",
        "po_number": "PO-9988",
    }
    ev = extract_evidence(doc, data)
    assert "invoice_number" in ev
    assert ev["invoice_number"]["text"] == "VL/0931"
    assert doc[ev["invoice_number"]["start"]:ev["invoice_number"]["end"]] == "VL/0931"
    assert ev["po_number"]["text"] == "PO-9988"


def test_supplier_name_whitespace_variations():
    # Source has multiple spaces / tabs: 'Narmada  Polymers   LLP'
    # Data has normalized: 'Narmada Polymers LLP'
    doc = "TAX INVOICE\nNarmada  Polymers   LLP\nGSTIN: 24AAOFN2207Q1ZX"
    data = {
        "supplier_name": "Narmada Polymers LLP",
        "supplier_gstin": "24AAOFN2207Q1ZX",
    }
    ev = extract_evidence(doc, data)
    assert "supplier_name" in ev
    # Sliced text must match original source exactly (with its extra spaces)
    assert ev["supplier_name"]["text"] == "Narmada  Polymers   LLP"
    assert doc[ev["supplier_name"]["start"]:ev["supplier_name"]["end"]] == "Narmada  Polymers   LLP"


def test_non_bmp_unicode_emoji_offsets():
    # Document contains non-BMP Unicode characters (e.g. 📦, ✨, 🚀) whose codepoints > 0xFFFF
    # In Python, each counts as 1 code point. In JS UTF-16, each counts as 2 code units.
    doc = "📦 Order Delivered!\n✨ INVOICE 🚀\nInvoice No: SIS/26-27/0042\nTotal: 100.00"
    data = {
        "invoice_number": "SIS/26-27/0042",
        "total": 100.0,
    }
    ev = extract_evidence(doc, data)
    assert "invoice_number" in ev
    assert "total" in ev

    # 1. Verify Python code point invariant
    for field, item in ev.items():
        assert doc[item["start"]:item["end"]] == item["text"]

    # 2. Verify conversion to JavaScript UTF-16 code units
    def py_to_js_offset(text: str, offset: int) -> int:
        return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text[:offset])

    # Convert Python offsets to JS UTF-16 positions
    js_start = py_to_js_offset(doc, ev["invoice_number"]["start"])
    js_end = py_to_js_offset(doc, ev["invoice_number"]["end"])

    # Decode UTF-16 code units at these positions to prove frontend slice matches
    utf16_bytes = doc.encode("utf-16-le")
    utf16_slice = utf16_bytes[js_start * 2 : js_end * 2].decode("utf-16-le")
    assert utf16_slice == "SIS/26-27/0042"

