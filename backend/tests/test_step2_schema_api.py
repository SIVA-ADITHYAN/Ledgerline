import json
import sqlite3
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from ledgerline import db
from ledgerline.api import app
from ledgerline.cli import to_contract
from ledgerline.pipeline import process_document
from ledgerline.reference import load_reference


OLD_SCHEMA = """
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    filename TEXT,
    raw_text TEXT NOT NULL,
    document_type TEXT,
    supplier_name TEXT,
    supplier_gstin TEXT,
    invoice_number TEXT,
    invoice_date TEXT,
    currency TEXT,
    subtotal REAL,
    cgst REAL,
    sgst REAL,
    igst REAL,
    round_off REAL,
    total REAL,
    bank_account TEXT,
    ifsc TEXT,
    po_number TEXT,
    notes TEXT,
    line_items_json TEXT,
    warnings_json TEXT,
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_reasons_json TEXT,
    required_approvals INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ready_for_approval',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    user_id TEXT NOT NULL,
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(invoice_id, user_id)
);
CREATE TABLE rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    user_id TEXT NOT NULL,
    note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL UNIQUE REFERENCES invoices(id),
    amount REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER REFERENCES invoices(id),
    document_id TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def tmp_db(tmp_path):
    db_file = tmp_path / "test.db"
    conn = db.connect(str(db_file))
    try:
        yield conn, str(db_file)
    finally:
        conn.close()


@pytest.fixture
def api_client(tmp_path, monkeypatch, fake_llm):
    monkeypatch.setenv("LEDGERLINE_DB", str(tmp_path / "api_test.db"))
    client = TestClient(app)
    return client


# ==========================================
# Phase 2: Database Persistence & Migration Tests
# ==========================================

def test_fresh_db_creates_evidence_column(tmp_db):
    conn, _ = tmp_db
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()]
    assert "evidence_json" in columns


def test_existing_db_migration(tmp_path):
    old_db_path = tmp_path / "old.db"
    # Create DB with old schema without evidence_json
    raw_conn = sqlite3.connect(str(old_db_path))
    raw_conn.executescript(OLD_SCHEMA)
    raw_conn.execute(
        """INSERT INTO invoices (org_id, document_id, raw_text, invoice_number, total, status)
           VALUES ('meridian', 'old_001', 'Old invoice', 'INV-OLD-1', 1000.0, 'ready_for_approval')"""
    )
    raw_conn.commit()
    raw_conn.close()

    # Now open using db.connect() - should safely run migration
    conn = db.connect(str(old_db_path))
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()]
    assert "evidence_json" in columns

    # Existing record must still exist and return empty evidence dict
    inv = db.get_invoice(conn, 1)
    assert inv is not None
    assert inv["invoice_number"] == "INV-OLD-1"
    assert inv["evidence"] == {}
    conn.close()


def test_get_invoice_evidence_parsing(tmp_db):
    conn, _ = tmp_db
    ev_data = {"invoice_number": {"start": 10, "end": 20, "text": "INV-12345"}}
    conn.execute(
        """INSERT INTO invoices (org_id, document_id, raw_text, invoice_number, evidence_json)
           VALUES ('meridian', 'doc_1', 'Invoice No INV-12345', 'INV-12345', ?)""",
        (json.dumps(ev_data),),
    )
    conn.commit()
    inv = db.get_invoice(conn, 1)
    assert inv is not None
    assert inv["evidence"] == ev_data


# ==========================================
# Phase 3: Pipeline & CLI Integration Tests
# ==========================================

def test_pipeline_extracts_and_persists_evidence(tmp_db):
    conn, _ = tmp_db
    ref = load_reference()
    raw_text = """Sitara Industrial Supplies Pvt. Ltd.
GSTIN/UIN: 27AAKCS4821M1ZW
Invoice No. : SIS/26-27/0042
Dated : 25/04/2026
TOTAL : 23,458.00
Bank Details:
A/c No: 50200012345678
IFS Code: ZEPH0001234"""

    inv = process_document(conn, ref, raw_text, "doc_test_1")
    assert "evidence" in inv
    evidence = inv["evidence"]
    assert isinstance(evidence, dict)
    assert "invoice_number" in evidence
    assert evidence["invoice_number"]["text"] == "SIS/26-27/0042"
    assert raw_text[evidence["invoice_number"]["start"]:evidence["invoice_number"]["end"]] == "SIS/26-27/0042"


def test_pipeline_evidence_invariant_on_all_fields(tmp_db):
    conn, _ = tmp_db
    ref = load_reference()
    raw_text = """Sitara Industrial Supplies Pvt. Ltd.
GSTIN/UIN: 27AAKCS4821M1ZW
Invoice No. : SIS/26-27/0042
Dated : 25/04/2026
Sub Total: 19,880.00
TOTAL : 23,458.00
A/c No: 50200012345678
IFS Code: ZEPH0001234"""

    inv = process_document(conn, ref, raw_text, "doc_test_inv")
    evidence = inv.get("evidence", {})
    for field, ev in evidence.items():
        s, e, t = ev["start"], ev["end"], ev["text"]
        assert raw_text[s:e] == t, f"Invariant violated for {field}"


def test_cli_to_contract_includes_evidence():
    inv_data = {
        "document_id": "doc_01",
        "document_type": "invoice",
        "supplier_name": "Test Supplier",
        "supplier_gstin": "27AAKCS4821M1ZW",
        "invoice_number": "INV-01",
        "invoice_date": "2026-04-25",
        "currency": "INR",
        "subtotal": 1000.0,
        "cgst": 90.0,
        "sgst": 90.0,
        "igst": 0.0,
        "round_off": 0.0,
        "total": 1180.0,
        "bank_account": "1234567890",
        "ifsc": "TEST0001234",
        "po_number": "PO-1",
        "line_items": [],
        "needs_review": False,
        "review_reasons": [],
        "required_approvals": 1,
        "usage": {"llm_calls": 1, "input_tokens": 10, "output_tokens": 10, "cost_inr": 0.01, "latency_ms": 100},
        "evidence": {
            "invoice_number": {"start": 0, "end": 6, "text": "INV-01"},
        },
    }
    contract = to_contract(inv_data)
    assert "evidence" in contract
    assert contract["evidence"]["invoice_number"]["text"] == "INV-01"


# ==========================================
# Phase 4: Structured Review Reasons Tests
# ==========================================

def test_api_detail_includes_review_reasons_detail(api_client, tmp_path):
    # Upload an invoice that triggers review reasons (e.g. duplicate or gstin_invalid)
    doc_content = "Invoice from Unknown Vendor without GSTIN\nInvoice No: INV-999\nTotal: 100.00"
    files = {"file": ("test_rev.txt", doc_content.encode("utf-8"), "text/plain")}
    res = api_client.post("/api/invoices", files=files)
    assert res.status_code == 200
    inv_id = res.json()["id"]

    detail_res = api_client.get(f"/api/invoices/{inv_id}")
    assert detail_res.status_code == 200
    data = detail_res.json()

    assert "review_reasons" in data
    assert isinstance(data["review_reasons"], list)
    assert "review_reasons_detail" in data
    assert isinstance(data["review_reasons_detail"], list)

    # Check structure of review_reasons_detail items
    for item in data["review_reasons_detail"]:
        assert "code" in item
        assert "label" in item
        assert "description" in item
        assert "target_field" in item


def test_bank_details_changed_masks_accounts_in_reasons_detail(api_client, fake_llm):
    from ledgerline.llm.fake import CANNED_EXTRACTION
    changed_extraction = dict(CANNED_EXTRACTION)
    changed_extraction["bank_account"] = "9999888877776666"
    fake_llm.responses = [json.dumps(changed_extraction)] * 2

    # Invoice with changed bank account from known vendor Sitara (GSTIN 27AAKCS4821M1ZW)
    doc_content = """SITARA INDUSTRIAL SUPPLIES PVT. LTD.
GSTIN: 27AAKCS4821M1ZW
Invoice No: SIS/26-27/9999
Dated: 2026-04-25
Total: 1,000.00
Bank Details:
A/c No: 9999888877776666
IFS Code: ZEPH0001234"""

    files = {"file": ("bank_change.txt", doc_content.encode("utf-8"), "text/plain")}
    res = api_client.post("/api/invoices", files=files)
    assert res.status_code == 200
    inv_id = res.json()["id"]

    detail_res = api_client.get(f"/api/invoices/{inv_id}")
    assert detail_res.status_code == 200
    data = detail_res.json()

    bank_details_reason = next(
        (r for r in data["review_reasons_detail"] if r["code"] == "bank_details_changed"),
        None,
    )
    assert bank_details_reason is not None
    assert bank_details_reason["target_field"] == "bank_account"

    # Verify no unmasked bank account numbers (16 digits) appear in description or evidence details
    res_str = json.dumps(bank_details_reason)
    assert "9999888877776666" not in res_str
    assert "50200012345678" not in res_str

    # Verify that evidence inside reason does not have fake 'text' matching asterisks
    ev = bank_details_reason.get("evidence")
    if ev:
        assert "masked_text" in ev
        assert ev["masked_text"] == "************6666"
        assert ev.get("text") != "************6666"


def test_api_responses_strictly_mask_bank_accounts(api_client, fake_llm):
    from ledgerline.llm.fake import CANNED_EXTRACTION
    changed_extraction = dict(CANNED_EXTRACTION)
    changed_extraction["bank_account"] = "9999888877776666"
    fake_llm.responses = [json.dumps(changed_extraction)] * 4

    doc_content = """SITARA INDUSTRIAL SUPPLIES PVT. LTD.
GSTIN: 27AAKCS4821M1ZW
Invoice No: SIS/26-27/9999
Dated: 2026-04-25
Total: 1,000.00
Bank Details:
A/c No: 9999888877776666
IFS Code: ZEPH0001234"""

    # 1. Test POST /api/invoices
    res = api_client.post("/api/invoices", files={"file": ("bank_privacy.txt", doc_content.encode("utf-8"), "text/plain")})
    assert res.status_code == 200
    upload_data = res.json()
    assert upload_data["bank_account"] == "************6666"
    assert "9999888877776666" not in json.dumps(upload_data["bank_account"])
    if "bank_account" in upload_data.get("evidence", {}):
        assert "9999888877776666" not in json.dumps(upload_data["evidence"]["bank_account"])

    inv_id = upload_data["id"]

    # 2. Test GET /api/invoices/{invoice_id}
    detail_res = api_client.get(f"/api/invoices/{inv_id}")
    assert detail_res.status_code == 200
    detail_data = detail_res.json()
    assert detail_data["bank_account"] == "************6666"
    assert "9999888877776666" not in json.dumps(detail_data["bank_account"])
    assert "9999888877776666" not in json.dumps(detail_data["evidence"]["bank_account"])
    assert "9999888877776666" not in json.dumps(detail_data["review_reasons_detail"])

    # 3. Verify raw_text in API response has bank account masked with identical character length
    assert "9999888877776666" not in detail_data["raw_text"]
    assert len(detail_data["raw_text"]) == len(doc_content)
    ba_ev = detail_data["evidence"]["bank_account"]
    s, e = ba_ev["start"], ba_ev["end"]
    # In privacy-safe raw_text, slicing [s:e] yields the masked account with exact offsets
    assert detail_data["raw_text"][s:e] == "************6666"
    assert ba_ev["masked_text"] == "************6666"


def test_repeated_bank_account_in_raw_text_all_masked(api_client, fake_llm):
    from ledgerline.llm.fake import CANNED_EXTRACTION
    changed_extraction = dict(CANNED_EXTRACTION)
    changed_extraction["bank_account"] = "9999888877776666"
    fake_llm.responses = [json.dumps(changed_extraction)] * 2

    # Document with account appearing twice (header and footer)
    doc_content = """INVOICE SIS/26-27/9999
Date: 25/04/2026 GSTIN: 27AAKCS4821M1ZW
Primary Account: 9999888877776666
Total Amount: 1,000.00
Reminder: Please remit to Account 9999888877776666 IFS Code: ZEPH0001234"""

    res = api_client.post("/api/invoices", files={"file": ("repeat.txt", doc_content.encode("utf-8"), "text/plain")})
    assert res.status_code == 200
    inv_id = res.json()["id"]

    detail = api_client.get(f"/api/invoices/{inv_id}").json()

    # Verify both occurrences are masked in raw_text
    assert "9999888877776666" not in detail["raw_text"]
    assert detail["raw_text"].count("************6666") == 2
    # Verify length is 100% identical (character count preserved)
    assert len(detail["raw_text"]) == len(doc_content)
    # Verify unrelated fields are NOT masked
    assert "SIS/26-27/9999" in detail["raw_text"]
    assert "27AAKCS4821M1ZW" in detail["raw_text"]
    assert "25/04/2026" in detail["raw_text"]
    assert "1,000.00" in detail["raw_text"]


def test_unextracted_bank_account_in_raw_text_masked_in_api(api_client, fake_llm, tmp_path):
    from ledgerline.llm.fake import CANNED_EXTRACTION
    # Extraction completely omits bank_account (e.g. None or not extracted by LLM)
    no_bank_extraction = dict(CANNED_EXTRACTION)
    no_bank_extraction["bank_account"] = None
    fake_llm.responses = [json.dumps(no_bank_extraction)] * 2

    # Raw document contains an unextracted bank account number
    unextracted_account = "31840100022917"
    doc_content = f"""SITARA INDUSTRIAL SUPPLIES PVT. LTD.
GSTIN: 27AAKCS4821M1ZW
Invoice No: SIS/26-27/1111
Dated: 2026-04-25
Total: 5,000.00
Orbit Bank, Vapi A/c {unextracted_account}
IFS Code: ZEPH0001234"""

    res = api_client.post("/api/invoices", files={"file": ("unextracted_bank.txt", doc_content.encode("utf-8"), "text/plain")})
    assert res.status_code == 200
    inv_id = res.json()["id"]

    detail = api_client.get(f"/api/invoices/{inv_id}").json()

    # 1. Full unextracted bank account digits must not be exposed anywhere in the API response
    assert unextracted_account not in detail["raw_text"]
    assert unextracted_account not in json.dumps(detail)

    # 2. Masked account preserves format and last 4 digits remain visible
    expected_masked = "**********2917"
    assert expected_masked in detail["raw_text"]

    # 3. Text length is strictly preserved (1-to-1 character count)
    assert len(detail["raw_text"]) == len(doc_content)

    # 4. Original unmasked raw_text is preserved in the database for internal processing
    db_file = tmp_path / "api_test.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    db_raw = cur.execute("SELECT raw_text FROM invoices WHERE id = ?", (inv_id,)).fetchone()[0]
    conn.close()
    assert unextracted_account in db_raw
    assert db_raw == doc_content


def test_unextracted_bank_account_multiple_occurrences_masked(api_client, fake_llm):
    from ledgerline.llm.fake import CANNED_EXTRACTION
    no_bank_extraction = dict(CANNED_EXTRACTION)
    no_bank_extraction["bank_account"] = None
    fake_llm.responses = [json.dumps(no_bank_extraction)] * 2

    unextracted_account = "50200012349999"
    doc_content = f"""SITARA INDUSTRIAL SUPPLIES PVT. LTD.
GSTIN: 27AAKCS4821M1ZW
Invoice No: SIS/26-27/2222
Dated: 2026-04-25
Bank Details: A/c No.   : {unextracted_account}
Sub Total: 1,000.00
TOTAL : 1,180.00
Payment Terms: Wire transfer directly to A/c {unextracted_account} within 30 days."""

    res = api_client.post("/api/invoices", files={"file": ("unextracted_multi.txt", doc_content.encode("utf-8"), "text/plain")})
    assert res.status_code == 200
    inv_id = res.json()["id"]

    detail = api_client.get(f"/api/invoices/{inv_id}").json()

    # Both occurrences are masked
    assert unextracted_account not in detail["raw_text"]
    expected_masked = "**********9999"
    assert detail["raw_text"].count(expected_masked) == 2

    # Length preserved
    assert len(detail["raw_text"]) == len(doc_content)

    # Other evidence offsets remain valid against raw_text
    ev = detail.get("evidence", {})
    for field_name in ["invoice_number", "total", "supplier_gstin"]:
        if field_name in ev:
            s, e, t = ev[field_name]["start"], ev[field_name]["end"], ev[field_name]["text"]
            assert detail["raw_text"][s:e] == t, f"Evidence mismatch for {field_name}"


def test_non_bmp_unicode_api_offsets_and_js_conversion(api_client, fake_llm):
    from ledgerline.llm.fake import CANNED_EXTRACTION
    fake_llm.responses = [json.dumps(CANNED_EXTRACTION)] * 2

    # Non-BMP emojis before invoice number
    doc_content = "📦 Logistics Dispatch ✨\n🚀 TAX INVOICE\nInvoice No. : SIS/26-27/0042\nTotal : 23,458.00"
    res = api_client.post("/api/invoices", files={"file": ("emoji.txt", doc_content.encode("utf-8"), "text/plain")})
    assert res.status_code == 200
    inv_id = res.json()["id"]

    detail = api_client.get(f"/api/invoices/{inv_id}").json()
    assert "evidence" in detail
    ev = detail["evidence"]["invoice_number"]

    # Python code point slicing in backend:
    assert detail["raw_text"][ev["start"]:ev["end"]] == "SIS/26-27/0042"

    # JS UTF-16 code unit conversion simulation:
    def py_to_js_offset(text: str, offset: int) -> int:
        return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text[:offset])

    js_start = py_to_js_offset(detail["raw_text"], ev["start"])
    js_end = py_to_js_offset(detail["raw_text"], ev["end"])

    utf16_bytes = detail["raw_text"].encode("utf-16-le")
    utf16_slice = utf16_bytes[js_start * 2 : js_end * 2].decode("utf-16-le")
    assert utf16_slice == "SIS/26-27/0042"


def test_internal_db_and_validation_preserves_unmasked_bank_account(tmp_db, fake_llm):
    conn, _ = tmp_db
    ref = load_reference()
    from ledgerline.llm.fake import CANNED_EXTRACTION
    changed_extraction = dict(CANNED_EXTRACTION)
    changed_extraction["bank_account"] = "9999888877776666"
    fake_llm.responses = [json.dumps(changed_extraction)] * 2

    raw_text = """SITARA INDUSTRIAL SUPPLIES PVT. LTD.
GSTIN: 27AAKCS4821M1ZW
Invoice No: SIS/26-27/9999
Dated: 2026-04-25
Total: 1,000.00
Bank Details:
A/c No: 9999888877776666
IFS Code: ZEPH0001234"""

    # Process document internally
    inv = process_document(conn, ref, raw_text, "doc_internal_1")

    # In internal invoice object:
    assert inv["bank_account"] == "9999888877776666"  # Preserved unmasked for internal validation!
    assert inv["evidence"]["bank_account"]["text"] == "9999888877776666"
    assert inv["evidence"]["bank_account"]["masked_text"] == "************6666"

    # Exact-slice invariant holds 100% internally
    s = inv["evidence"]["bank_account"]["start"]
    e = inv["evidence"]["bank_account"]["end"]
    assert raw_text[s:e] == inv["evidence"]["bank_account"]["text"]

    # In raw SQLite table:
    row = conn.execute("SELECT bank_account, evidence_json FROM invoices WHERE id = ?", (inv["id"],)).fetchone()
    assert row["bank_account"] == "9999888877776666"
    ev_json = json.loads(row["evidence_json"])
    assert ev_json["bank_account"]["text"] == "9999888877776666"
    assert ev_json["bank_account"]["masked_text"] == "************6666"


# ==========================================
# Phase 5: Invoice Queue Filter Tests
# ==========================================

def test_list_invoices_no_filter(api_client):
    res = api_client.get("/api/invoices")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_list_invoices_filter_supported_statuses(api_client):
    # Add ready_for_approval and needs_review invoices
    doc_ok = """SITARA INDUSTRIAL SUPPLIES PVT. LTD.
GSTIN/UIN: 27AAKCS4821M1ZW
Invoice No. : SIS/26-27/0042
Dated : 25/04/2026
Sub Total: 19,880.00
CGST @ 9%: 1,789.20
SGST @ 9%: 1,789.20
Round Off: -0.40
TOTAL: 23,458.00
A/c No: 50200012345678
IFS Code: ZEPH0001234"""
    api_client.post("/api/invoices", files={"file": ("ok.txt", doc_ok.encode("utf-8"), "text/plain")})

    doc_rev = "Random text"
    api_client.post("/api/invoices", files={"file": ("rev.txt", doc_rev.encode("utf-8"), "text/plain")})

    # Test filter needs_review
    res_rev = api_client.get("/api/invoices?status=needs_review")
    assert res_rev.status_code == 200
    for inv in res_rev.json():
        assert inv["status"] == "needs_review"

    # Test filter ready_for_approval
    res_ok = api_client.get("/api/invoices?status=ready_for_approval")
    assert res_ok.status_code == 200
    for inv in res_ok.json():
        assert inv["status"] == "ready_for_approval"

    # Test filter approved
    res_app = api_client.get("/api/invoices?status=approved")
    assert res_app.status_code == 200
    for inv in res_app.json():
        assert inv["status"] == "approved"

    # Test filter rejected
    res_rej = api_client.get("/api/invoices?status=rejected")
    assert res_rej.status_code == 200
    for inv in res_rej.json():
        assert inv["status"] == "rejected"


def test_list_invoices_invalid_status_returns_400(api_client):
    res = api_client.get("/api/invoices?status=invalid_status_name")
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "Invalid status" in detail
    assert "ready_for_approval" in detail
