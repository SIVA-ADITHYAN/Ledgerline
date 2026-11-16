"""Deterministic checks.

Deterministic validation rules strictly enforce the AP policy rules (see AP_POLICY.md).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import os
import re
import sqlite3

from .models import Invoice
from .parsing import parse_date
from .reference import ReferenceData
from .extraction import mask_bank_account

GSTIN_PATTERN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BUYER_STATE_CODE = 27  # Maharashtra


def gstin_checksum(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = GSTIN_ALPHABET.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return GSTIN_ALPHABET[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str | None) -> bool:
    """Validate a GSTIN's format and check digit per AP Policy Appendix A."""
    if not gstin:
        return False
    clean = gstin.strip().upper()
    if len(clean) != 15 or not GSTIN_PATTERN.match(clean):
        return False
    return gstin_checksum(clean[:14]) == clean[14]


def normalize_supplier_name(name: str | None) -> str:
    """Normalize foreign supplier legal name: lowercase, ignoring spaces and punctuation (AP Policy §1)."""
    if not name:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(name)).lower()


def get_supplier_key(currency: str | None, gstin: str | None, name: str | None) -> str:
    """Get the supplier key according to AP Policy §1.

    - For domestic documents (currency INR), supplier GSTIN.
    - For foreign documents (non-INR currency), legal name case-insensitive ignoring spaces/punctuation.
    """
    is_foreign = currency and currency.strip().upper() != "INR"
    if not is_foreign:
        return gstin.strip().upper() if gstin else ""
    return normalize_supplier_name(name)


def normalize_invoice_number(inv_num: str | None) -> str:
    """Uppercase, with spaces and the characters / - # . removed. Leading zeros preserved (AP Policy §1)."""
    if not inv_num:
        return ""
    return re.sub(r"[\s/\-#.]", "", str(inv_num)).upper()


def get_financial_year(dt: date | None) -> str | None:
    """Financial year (FY): 1 April to 31 March (AP Policy §1).

    e.g. 2026-04-03 -> '2026-27'; 2026-03-31 -> '2025-26'.
    """
    if not dt:
        return None
    year = dt.year
    if dt.month >= 4:
        start_year = year
        end_year = (year + 1) % 100
    else:
        start_year = year - 1
        end_year = year % 100
    return f"{start_year}-{end_year:02d}"


def find_vendor(
    ref: ReferenceData,
    supplier_name_or_inv: Invoice | str | None,
    supplier_gstin: str | None = None,
    currency: str = "INR",
) -> dict | None:
    """Find vendor in vendor master (AP Policy §1 & §2.2).

    - Domestic documents: matched by GSTIN alone.
    - Foreign documents: matched by normalized legal name.
    """
    if isinstance(supplier_name_or_inv, Invoice):
        inv = supplier_name_or_inv
        s_name = inv.supplier_name
        s_gstin = inv.supplier_gstin
        curr = inv.currency or "INR"
    else:
        s_name = supplier_name_or_inv
        s_gstin = supplier_gstin
        curr = currency or "INR"

    is_foreign = curr.strip().upper() != "INR"

    if not is_foreign:
        # Domestic supplier: matched by GSTIN alone
        if not s_gstin:
            return None
        target_gstin = s_gstin.strip().upper()
        for vendor in ref.vendors:
            vg = vendor.get("gstin")
            if vg and vg.strip().upper() == target_gstin:
                return vendor
        return None
    else:
        # Foreign supplier: matched by normalized legal name
        target_name_key = normalize_supplier_name(s_name)
        if not target_name_key:
            return None
        for vendor in ref.vendors:
            vn = vendor.get("legal_name")
            if vn and normalize_supplier_name(vn) == target_name_key:
                return vendor
        return None


def check_vendor(vendor: dict | None) -> list[str]:
    return [] if vendor else ["vendor_unknown"]


def check_vendor_new(inv: Invoice, vendor: dict | None) -> list[str]:
    """Flag vendor_new if vendor was onboarded fewer than 90 days before document date (AP Policy §3)."""
    if not vendor or not inv.invoice_date:
        return []
    onboarded_str = vendor.get("onboarded_on")
    if not onboarded_str or not onboarded_str.strip():
        return []
    try:
        onboarded_date = date.fromisoformat(onboarded_str.strip())
    except (ValueError, TypeError):
        parsed = parse_date(onboarded_str)
        if not parsed:
            return []
        onboarded_date = parsed

    delta_days = (inv.invoice_date - onboarded_date).days
    if 0 <= delta_days < 90:
        return ["vendor_new"]
    return []


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


def _to_decimal(val: float | int | str | None) -> Decimal:
    if val is None or val == "":
        return Decimal("0.00")
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal("0.00")


def check_totals(inv: Invoice) -> list[str]:
    """Check totals using exact Decimal arithmetic (AP Policy §1 & §3).

    Difference > ₹1.00 triggers totals_mismatch.
    Difference <= ₹1.00 is within tolerance.
    """
    if inv.total is None:
        return []

    total_dec = _to_decimal(inv.total)
    items_sum = sum((_to_decimal(item.amount) for item in inv.line_items), Decimal("0.00"))
    cgst_dec = _to_decimal(inv.cgst)
    sgst_dec = _to_decimal(inv.sgst)
    igst_dec = _to_decimal(inv.igst)
    round_off_dec = _to_decimal(inv.round_off)

    computed = items_sum + cgst_dec + sgst_dec + igst_dec + round_off_dec
    diff = abs(computed - total_dec)
    return [] if diff <= Decimal("1.00") else ["totals_mismatch"]


def check_duplicate(
    conn: sqlite3.Connection,
    ref: ReferenceData,
    inv: Invoice,
    document_id: str | None = None,
) -> list[str]:
    """Check if invoice is a duplicate under AP Policy §3.

    - Credit notes are not checked.
    - Matches on: supplier key, normalized invoice number, and financial year (FY).
    - Checks both reference/paid_invoices.csv and earlier batch invoices in SQLite.
    """
    if inv.document_type == "credit_note":
        return []

    curr_supplier_key = get_supplier_key(inv.currency, inv.supplier_gstin, inv.supplier_name)
    if not curr_supplier_key:
        return []

    curr_norm_num = normalize_invoice_number(inv.invoice_number)
    if not curr_norm_num:
        return []

    curr_fy = get_financial_year(inv.invoice_date)
    if not curr_fy:
        return []

    # 1. Check paid_invoices.csv
    for paid in ref.paid_invoices:
        paid_gstin = paid.get("supplier_gstin")
        paid_name = paid.get("supplier_name")
        paid_currency = "INR" if paid_gstin and paid_gstin.strip() else "USD"
        paid_supplier_key = get_supplier_key(paid_currency, paid_gstin, paid_name)
        if paid_supplier_key != curr_supplier_key:
            continue

        paid_norm_num = normalize_invoice_number(paid.get("invoice_number"))
        if paid_norm_num != curr_norm_num:
            continue

        paid_date_str = paid.get("invoice_date")
        if paid_date_str:
            try:
                paid_date = date.fromisoformat(paid_date_str.strip())
            except (ValueError, TypeError):
                paid_date = parse_date(paid_date_str)
            paid_fy = get_financial_year(paid_date)
            if paid_fy == curr_fy:
                return ["duplicate"]

    # 2. Check earlier invoices in SQLite database
    cursor = conn.execute(
        """SELECT id, document_id, document_type, currency, supplier_gstin, supplier_name,
                  invoice_number, invoice_date
           FROM invoices
           WHERE document_type != 'credit_note'"""
    )
    for row in cursor.fetchall():
        prev_id, prev_doc_id, prev_doc_type, prev_curr, prev_gstin, prev_name, prev_num, prev_dt_str = row
        if document_id and prev_doc_id == document_id:
            continue

        prev_supplier_key = get_supplier_key(prev_curr, prev_gstin, prev_name)
        if prev_supplier_key != curr_supplier_key:
            continue

        prev_norm_num = normalize_invoice_number(prev_num)
        if prev_norm_num != curr_norm_num:
            continue

        if prev_dt_str:
            try:
                prev_date = date.fromisoformat(prev_dt_str.strip())
            except (ValueError, TypeError):
                prev_date = parse_date(prev_dt_str)
            prev_fy = get_financial_year(prev_date)
            if prev_fy == curr_fy:
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
    """Check if invoice bank details match approved vendor master (AP Policy §2.4).

    - If the vendor is not found in vendor master, do not flag bank_details_changed.
    - If the invoice does not state bank details, do not flag bank_details_changed.
    - Digits-only normalization for account numbers, preserving leading zeros.
    - Case-insensitive IFSC code comparison.
    - Flags if either account number or IFSC has changed.
    """
    if not vendor:
        return []

    if not inv.bank_account and not inv.ifsc:
        return []

    reasons: list[str] = []

    if inv.bank_account and vendor.get("bank_account"):
        inv_acct_digits = re.sub(r"\D", "", str(inv.bank_account))
        vendor_acct_digits = re.sub(r"\D", "", str(vendor["bank_account"]))
        if inv_acct_digits != vendor_acct_digits:
            reasons.append("bank_details_changed")

    if inv.ifsc and vendor.get("ifsc"):
        inv_ifsc = inv.ifsc.strip().upper()
        vendor_ifsc = str(vendor["ifsc"]).strip().upper()
        if inv_ifsc != vendor_ifsc:
            reasons.append("bank_details_changed")

    return list(dict.fromkeys(reasons))


def check_line_items_arithmetic(inv: Invoice) -> list[str]:
    """Check if line item calculation (quantity * unit_price) matches line amount.

    Enabled via LEDGERLINE_FLAG_CHECK_LINE_ITEM_TOTALS=1 (Part 2 feature flag).
    """
    if os.getenv("LEDGERLINE_FLAG_CHECK_LINE_ITEM_TOTALS") != "1":
        return []
    for item in inv.line_items or []:
        if item.quantity is not None and item.unit_price is not None and item.amount is not None:
            try:
                expected_amt = Decimal(str(item.quantity)) * Decimal(str(item.unit_price))
                stated_amt = Decimal(str(item.amount))
                if abs(expected_amt - stated_amt) > Decimal("1.00"):
                    return ["line_item_math_error"]
            except Exception:
                pass
    return []


def check_future_date(inv: Invoice, raw_text: str = "") -> list[str]:
    """Check if invoice date is post-dated into the future relative to document receipt.

    Enabled via LEDGERLINE_FLAG_CHECK_FUTURE_DATE=1 (Part 2 feature flag).
    """
    if os.getenv("LEDGERLINE_FLAG_CHECK_FUTURE_DATE") != "1":
        return []
    if not inv.invoice_date:
        return []
    reference_date = None
    if raw_text:
        m = re.search(r'(?i)\bDate:\s*[A-Za-z]+,\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})', raw_text)
        if m:
            try:
                reference_date = datetime.strptime(m.group(1), "%d %b %Y").date()
            except Exception:
                pass
    if not reference_date:
        reference_date = date(2026, 5, 10)
    if inv.invoice_date > reference_date:
        return ["future_dated_invoice"]
    return []


def run_checks(
    conn: sqlite3.Connection,
    ref: ReferenceData,
    inv: Invoice,
    raw_text: str = "",
    document_id: str | None = None,
) -> list[str]:
    vendor = find_vendor(ref, inv)
    reasons: list[str] = []
    reasons.extend(check_credit_note(inv, raw_text))
    reasons.extend(check_gstin(inv))
    reasons.extend(check_vendor(vendor))
    reasons.extend(check_vendor_new(inv, vendor))
    reasons.extend(check_tax_type(inv))
    reasons.extend(check_totals(inv))
    reasons.extend(check_duplicate(conn, ref, inv, document_id=document_id))
    reasons.extend(check_bank_details(inv, vendor))
    reasons.extend(check_foreign_currency(inv))
    reasons.extend(check_suspicious_content(raw_text))
    reasons.extend(check_line_items_arithmetic(inv))
    reasons.extend(check_future_date(inv, raw_text))
    return list(dict.fromkeys(reasons))


def build_review_reasons_detail(invoice: dict, ref: ReferenceData | None = None) -> list[dict]:
    """Build structured review reasons with human labels, descriptions, target fields, and evidence."""
    reasons = invoice.get("review_reasons") or []
    if not reasons:
        return []

    evidence_map = invoice.get("evidence") or {}
    vendor = None
    if ref:
        vendor = find_vendor(
            ref,
            invoice.get("supplier_name"),
            invoice.get("supplier_gstin"),
            invoice.get("currency") or "INR",
        )

    details = []
    for code in reasons:
        ev = None
        target_field = None
        label = code.replace("_", " ").title()
        description = f"Invoice flagged under AP policy rule: {code}."

        if code == "credit_note":
            label = "Credit Note"
            description = "Document is a credit note reducing payable amount; credit notes cannot be approved for payment."
            target_field = "document_type"
            ev = evidence_map.get("invoice_number")

        elif code == "duplicate":
            label = "Duplicate Invoice"
            description = "An invoice with the same supplier key, normalized invoice number, and financial year already exists."
            target_field = "invoice_number"
            ev = evidence_map.get("invoice_number")

        elif code == "gstin_invalid":
            label = "Invalid Supplier GSTIN"
            description = "Supplier GSTIN is missing, malformed, or failed checksum verification."
            target_field = "supplier_gstin"
            ev = evidence_map.get("supplier_gstin")

        elif code == "vendor_unknown":
            label = "Unknown Vendor"
            description = "No approved vendor in the vendor master matches this supplier."
            target_field = "supplier_name"
            ev = evidence_map.get("supplier_name") or evidence_map.get("supplier_gstin")

        elif code == "vendor_new":
            label = "Recently Onboarded Vendor"
            description = "Vendor was onboarded fewer than 90 days before the invoice date."
            target_field = "invoice_date"
            ev = evidence_map.get("invoice_date")

        elif code == "bank_details_changed":
            label = "Bank Details Mismatch"
            description = "Invoice bank details differ from approved vendor master records."
            acct_changed = False
            if invoice.get("bank_account") and vendor and vendor.get("bank_account"):
                inv_acct_digits = re.sub(r"\D", "", str(invoice.get("bank_account")))
                v_acct_digits = re.sub(r"\D", "", str(vendor["bank_account"]))
                if inv_acct_digits != v_acct_digits:
                    acct_changed = True

            target_field = "bank_account" if acct_changed else "ifsc"

            if target_field == "bank_account":
                raw_ev = evidence_map.get("bank_account")
                if raw_ev:
                    ev = {
                        "start": raw_ev["start"],
                        "end": raw_ev["end"],
                        "masked_text": raw_ev.get("masked_text") or mask_bank_account(raw_ev.get("text")),
                    }
                else:
                    ev = None
                doc_masked = mask_bank_account(invoice.get("bank_account"))
                exp_masked = mask_bank_account(vendor.get("bank_account")) if vendor else None
                description = f"Invoice bank account ({doc_masked}) differs from vendor master on file ({exp_masked})."
            else:
                raw_ev = evidence_map.get("ifsc")
                ev = dict(raw_ev) if raw_ev else None
                doc_ifsc = invoice.get("ifsc")
                exp_ifsc = vendor.get("ifsc") if vendor else None
                description = f"Invoice IFSC code ({doc_ifsc}) differs from vendor master on file ({exp_ifsc})."

        elif code == "tax_type_mismatch":
            label = "Tax Type Mismatch"
            description = "Intra-state transactions must charge CGST+SGST; inter-state transactions must charge IGST."
            target_field = "igst" if (invoice.get("igst") or 0) > 0 else "cgst"
            ev = evidence_map.get("supplier_gstin") or evidence_map.get(target_field)

        elif code == "totals_mismatch":
            label = "Totals Calculation Mismatch"
            description = "Calculated sum of line items and taxes differs from stated invoice total by more than ₹1.00."
            target_field = "total"
            ev = evidence_map.get("total")

        elif code == "foreign_currency":
            label = "Foreign Currency Invoice"
            description = f"Invoice currency is {invoice.get('currency') or 'non-INR'}, requiring dual human approvals."
            target_field = "currency"
            ev = None

        elif code == "suspicious_content":
            label = "Suspicious Content Detected"
            description = "Document contains prompt injection, override instructions, or embedded script markup."
            target_field = "notes"
            ev = None

        elif code == "processing_error":
            label = "Document Processing Error"
            description = "Automated parsing or extraction encountered an unexpected error; manual review required."
            target_field = "notes"
            ev = None

        elif code == "line_item_math_error":
            label = "Line Item Math Error"
            description = "Calculated line item amount (quantity * unit price) differs from stated line total by more than ₹1.00."
            target_field = "subtotal"
            ev = evidence_map.get("subtotal")

        elif code == "future_dated_invoice":
            label = "Future Dated Invoice"
            description = "Invoice date is post-dated beyond the document receipt/transmission date."
            target_field = "invoice_date"
            ev = evidence_map.get("invoice_date")

        item = {
            "code": code,
            "label": label,
            "description": description,
            "target_field": target_field,
        }
        if ev:
            item["evidence"] = ev
        details.append(item)

    return details
