"""Deterministic checks.

Since v0.3 these are informational: they are stored as `warnings` and shown to reviewers.
Routing is decided by the model, which applies the AP policy itself (see pipeline.py).
"""
from __future__ import annotations

import re
import sqlite3

from .models import Invoice
from .reference import ReferenceData

GSTIN_PATTERN = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]")
GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BUYER_STATE_CODE = 27  # Maharashtra


def gstin_checksum(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = GSTIN_ALPHABET.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return GSTIN_ALPHABET[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str | None) -> bool:
    """Validate a GSTIN's format and check digit."""
    if not gstin:
        return False
    return GSTIN_PATTERN.match(gstin.strip().upper()) is not None


def find_vendor(ref: ReferenceData, supplier_name: str | None) -> dict | None:
    if not supplier_name:
        return None
    for vendor in ref.vendors:
        if vendor["legal_name"].strip().lower() == supplier_name.strip().lower():
            return vendor
    return None


def check_vendor(vendor: dict | None) -> list[str]:
    return [] if vendor else ["vendor_unknown"]


def check_gstin(inv: Invoice) -> list[str]:
    if inv.currency == "INR" and not is_valid_gstin(inv.supplier_gstin):
        return ["gstin_invalid"]
    return []


def check_tax_type(inv: Invoice) -> list[str]:
    if not inv.supplier_gstin:
        return []
    intra_state = inv.supplier_gstin[:2] == str(BUYER_STATE_CODE)
    if intra_state and inv.igst > 0:
        return ["tax_type_mismatch"]
    if not intra_state and (inv.cgst > 0 or inv.sgst > 0):
        return ["tax_type_mismatch"]
    return []


def check_totals(inv: Invoice) -> list[str]:
    if inv.total is None:
        return []
    computed = sum(item.amount for item in inv.line_items) + inv.cgst + inv.sgst + inv.igst + (inv.round_off or 0.0)
    diff = abs(computed - inv.total)
    return [] if diff <= 1.00 else ["totals_mismatch"]


def check_duplicate(conn: sqlite3.Connection, ref: ReferenceData, inv: Invoice) -> list[str]:
    row = conn.execute(
        "SELECT id FROM invoices WHERE supplier_name = ? AND invoice_number = ?",
        (inv.supplier_name, inv.invoice_number),
    ).fetchone()
    if row:
        return ["duplicate"]
    for paid in ref.paid_invoices:
        if paid["supplier_name"] == inv.supplier_name and paid["invoice_number"] == inv.invoice_number:
            return ["duplicate"]
    return []


SUSPICIOUS_PROMPT_INJECTION_PATTERN = re.compile(
    r"(?i)\b("
    r"ignore\s+(all\s+|previous\s+|the\s+)?(instructions|checks|rules|prompts)|"
    r"system\s+(override|instruction|prompt)|"
    r"approve\s+(this\s+|immediately|without\s+review)|"
    r"bypass\s+(checks|review|validation|policy)|"
    r"do\s+not\s+(review|flag|check|audit)|"
    r"mark\s+(as\s+)?(ready|approved)"
    r")\b"
)
SUSPICIOUS_HTML_PATTERN = re.compile(
    r"(?i)(<script\b|<iframe\b|<object\b|<embed\b|javascript:|onerror\s*=|onload\s*=)"
)


def check_credit_note(inv: Invoice, raw_text: str = "") -> list[str]:
    if inv.document_type == "credit_note":
        return ["credit_note"]
    if raw_text and (
        re.search(r'(?i)"Typ"\s*:\s*"CRN"', raw_text) or re.search(r'(?i)\bcredit\s+note\b', raw_text)
    ):
        return ["credit_note"]
    return []


def check_foreign_currency(inv: Invoice) -> list[str]:
    if inv.currency and inv.currency.strip().upper() != "INR":
        return ["foreign_currency"]
    return []


def check_suspicious_content(raw_text: str = "") -> list[str]:
    if not raw_text:
        return []
    if SUSPICIOUS_PROMPT_INJECTION_PATTERN.search(raw_text) or SUSPICIOUS_HTML_PATTERN.search(raw_text):
        return ["suspicious_content"]
    return []


def check_bank_details(inv: Invoice, vendor: dict | None) -> list[str]:
    if vendor and inv.ifsc and vendor.get("ifsc") and inv.ifsc != vendor["ifsc"]:
        return ["bank_details_changed"]
    return []


def run_checks(
    conn: sqlite3.Connection,
    ref: ReferenceData,
    inv: Invoice,
    raw_text: str = "",
) -> list[str]:
    vendor = find_vendor(ref, inv.supplier_name)
    reasons: list[str] = []
    reasons.extend(check_credit_note(inv, raw_text))
    reasons.extend(check_gstin(inv))
    reasons.extend(check_vendor(vendor))
    reasons.extend(check_tax_type(inv))
    reasons.extend(check_totals(inv))
    reasons.extend(check_duplicate(conn, ref, inv))
    reasons.extend(check_bank_details(inv, vendor))
    reasons.extend(check_foreign_currency(inv))
    reasons.extend(check_suspicious_content(raw_text))
    return list(dict.fromkeys(reasons))
