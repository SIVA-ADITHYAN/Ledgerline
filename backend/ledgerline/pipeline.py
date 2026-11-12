"""Process one document end to end: extract -> normalise -> check -> route -> persist."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict
from decimal import Decimal

from . import db
from .extraction import extract
from .llm.pricing import cost_usd
from .models import Invoice, LineItem
from .parsing import parse_amount, parse_date
from .reference import ReferenceData
from .validation import run_checks

HIGH_VALUE_THRESHOLD = Decimal("500000.00")  # AP_POLICY.md §4.1: invoices >= 5,00,000 need 2 approvals


def compute_required_approvals(inv: Invoice) -> int:
    # AP_POLICY.md §4.1:
    # 1. Credit notes: 0, because they cannot be approved for payment.
    # 2. Foreign-currency invoices: 2.
    # 3. Invoices with a total of ₹5,00,000.00 or more: 2.
    # 4. All other invoices: 1.
    if inv.document_type == "credit_note":
        return 0
    if inv.currency and inv.currency.strip().upper() != "INR":
        return 2
    if inv.total is not None and Decimal(str(inv.total)) >= HIGH_VALUE_THRESHOLD:
        return 2
    return 1


def normalize(data: dict) -> Invoice:
    items = [
        LineItem(
            description=str(item.get("description", "")),
            quantity=parse_amount(item.get("quantity")),
            unit_price=parse_amount(item.get("unit_price")),
            amount=parse_amount(item.get("amount")) or 0.0,
        )
        for item in data.get("line_items") or []
    ]
    return Invoice(
        document_type=data.get("document_type") or "invoice",
        supplier_name=data.get("supplier_name"),
        supplier_gstin=data.get("supplier_gstin"),
        invoice_number=data.get("invoice_number"),
        invoice_date=parse_date(data.get("invoice_date")),
        currency=data.get("currency") or "INR",
        subtotal=parse_amount(data.get("subtotal")),
        cgst=parse_amount(data.get("cgst")) or 0.0,
        sgst=parse_amount(data.get("sgst")) or 0.0,
        igst=parse_amount(data.get("igst")) or 0.0,
        round_off=parse_amount(data.get("round_off")) or 0.0,
        total=parse_amount(data.get("total")),
        bank_account=data.get("bank_account"),
        ifsc=data.get("ifsc"),
        po_number=data.get("po_number"),
        notes=data.get("notes"),
        line_items=items,
    )


def process_document(
    conn: sqlite3.Connection,
    ref: ReferenceData,
    raw_text: str,
    document_id: str,
    org_id: str = "meridian",
    filename: str | None = None,
) -> dict:
    responses = []
    processing_error = False
    try:
        data, responses = extract(raw_text, ref)
        inv = normalize(data)
        # Check raw document clues for credit note if LLM misclassified
        if inv.document_type != "credit_note" and (
            re.search(r'(?i)"Typ"\s*:\s*"CRN"', raw_text) or re.search(r'(?i)\bcredit\s+note\b', raw_text)
        ):
            inv.document_type = "credit_note"
    except Exception as exc:
        processing_error = True
        safe_msg = re.sub(r"\b\d{5,18}\b", lambda m: ("*" * (len(m.group(0)) - 4)) + m.group(0)[-4:], str(exc))
        inv = Invoice(
            document_type="invoice",
            supplier_name=None,
            supplier_gstin=None,
            invoice_number=None,
            invoice_date=None,
            currency="INR",
            subtotal=None,
            cgst=0.0,
            sgst=0.0,
            igst=0.0,
            round_off=0.0,
            total=None,
            bank_account=None,
            ifsc=None,
            po_number=None,
            notes=f"Processing error: {safe_msg}",
            line_items=[],
        )
        data = {"requires_review": True, "review_reasons": ["processing_error"]}

    # Deterministic checks strictly enforce AP policy rules
    deterministic_reasons = run_checks(conn, ref, inv, raw_text, document_id=document_id)
    if processing_error and "processing_error" not in deterministic_reasons:
        deterministic_reasons.insert(0, "processing_error")

    # Preserve legitimate extraction uncertainty and model-flagged reasons
    llm_reasons = [r for r in data.get("review_reasons") or [] if isinstance(r, str)]
    review_reasons = list(dict.fromkeys(deterministic_reasons + llm_reasons))

    needs_review = bool(review_reasons) or bool(data.get("requires_review", False))
    required_approvals = compute_required_approvals(inv)
    status = "needs_review" if needs_review else "ready_for_approval"
    warnings = deterministic_reasons

    cur = conn.execute(
        """INSERT INTO invoices (org_id, document_id, filename, raw_text, document_type, supplier_name,
               supplier_gstin, invoice_number, invoice_date, currency, subtotal, cgst, sgst, igst, round_off,
               total, bank_account, ifsc, po_number, notes, line_items_json, warnings_json, needs_review,
               review_reasons_json, required_approvals, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            org_id, document_id, filename, raw_text, inv.document_type, inv.supplier_name, inv.supplier_gstin,
            inv.invoice_number, inv.invoice_date.isoformat() if inv.invoice_date else None, inv.currency,
            inv.subtotal, inv.cgst, inv.sgst, inv.igst, inv.round_off, inv.total, inv.bank_account, inv.ifsc,
            inv.po_number, inv.notes, json.dumps([asdict(i) for i in inv.line_items]), json.dumps(warnings),
            int(needs_review), json.dumps(review_reasons), required_approvals, status,
        ),
    )
    invoice_id = cur.lastrowid
    for r in responses:
        conn.execute(
            "INSERT INTO llm_calls (invoice_id, document_id, model, input_tokens, output_tokens, cost_usd, "
            "latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (invoice_id, document_id, r.model, r.input_tokens, r.output_tokens,
             cost_usd(r.model, r.input_tokens, r.output_tokens), r.latency_ms),
        )
    conn.commit()
    return db.get_invoice(conn, invoice_id)
