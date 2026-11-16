"""HTTP API used by the reviewer UI.  Run: uvicorn ledgerline.api:app --reload"""
from __future__ import annotations

import re
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db
from .config import USD_TO_INR, get_settings
from .extraction import mask_bank_account
from .pipeline import process_document
from .reference import ReferenceData, load_reference
from .validation import build_review_reasons_detail, find_vendor

app = FastAPI(title="Ledgerline API", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

VALID_STATUSES = {"ready_for_approval", "needs_review", "approved", "rejected"}


def mask_bank_account_preserving_format(account: str | None) -> str:
    """Mask all digits except the last four, preserving non-digit characters so length is identical."""
    if not account:
        return ""
    digits = [c for c in str(account) if c.isdigit()]
    if len(digits) <= 4:
        return re.sub(r"\d", "*", str(account))
    unmasked_count = 0
    res = []
    for ch in reversed(str(account)):
        if ch.isdigit():
            if unmasked_count < 4:
                res.append(ch)
                unmasked_count += 1
            else:
                res.append("*")
        else:
            res.append(ch)
    res.reverse()
    return "".join(res)


BANK_ACCOUNT_DISCOVERY_PATTERNS = [
    # A/c, A/c No., Account No, Bank A/c, Current A/c, etc. followed by account number
    r'(?i)\b(?:(?:beneficiary|payee|vendor|supplier|current|savings|bank)\s+)*(?:a/c|acct|account)(?:\s*(?:no\.?|num(?:ber)?|#))?\s*[:\-\.]?\s*([A-Za-z0-9\-]{6,34})',
    # Short form "A/c 31840100022917"
    r'(?i)\bA/c\s+([0-9A-Za-z\-]{6,34})\b',
    # Structured "accdet: 12345678"
    r'(?i)\baccdet\b\s*[:\-\.]?\s*([A-Za-z0-9\-]{6,34})',
    # JSON keys e.g. "AccDet": "...", "AccountNo": "..."
    r'(?i)"(?:AccDet|AccountNo|AccNo|BankAccount|Account_Number|Bank_Account_No)"\s*:\s*"([A-Za-z0-9\-]{6,34})"',
]


def discover_bank_accounts_in_text(raw_text: str | None) -> list[str]:
    """Scan document text for bank account numbers that may not have been extracted into structured fields."""
    if not raw_text:
        return []
    accounts: list[str] = []
    for pat in BANK_ACCOUNT_DISCOVERY_PATTERNS:
        for match in re.finditer(pat, raw_text):
            cand = match.group(1).strip()
            digits = re.sub(r"\D", "", cand)
            if 6 <= len(digits) <= 24:
                # Exclude GSTIN format (15 characters)
                if re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}", cand.upper()):
                    continue
                # Exclude IFSC format (11 characters)
                if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", cand.upper()):
                    continue
                accounts.append(cand)
    # Deduplicate preserving order
    return list(dict.fromkeys(accounts))


def mask_document_text(raw_text: str | None, accounts: list[str | None]) -> str:
    """Mask occurrences of bank account numbers in document text without altering character count."""
    if not raw_text:
        return raw_text or ""
    masked_text = raw_text
    for acct in accounts:
        if not acct:
            continue
        clean = str(acct).strip()
        digits = re.sub(r"\D", "", clean)
        if len(digits) < 5:
            continue
        masked = mask_bank_account_preserving_format(clean)
        # Match using digit boundary guards so unrelated numbers (dates, amounts) are not touched
        pat = re.compile(rf"(?<!\d){re.escape(clean)}(?!\d)")
        masked_text = pat.sub(masked, masked_text)
        if clean != digits and len(digits) >= 5:
            masked_digits = mask_bank_account_preserving_format(digits)
            pat_digits = re.compile(rf"(?<!\d){re.escape(digits)}(?!\d)")
            masked_text = pat_digits.sub(masked_digits, masked_text)
    return masked_text


def sanitize_invoice_for_api(invoice: dict, ref: ReferenceData | None = None) -> dict:
    """Sanitize invoice data for API responses, masking sensitive fields like bank accounts."""
    out = dict(invoice)
    # Mask top-level bank account for privacy
    if out.get("bank_account"):
        out["bank_account"] = mask_bank_account(out["bank_account"])

    # Collect accounts that should be masked in raw_text
    accounts_to_mask = [invoice.get("bank_account")]
    if ref:
        vendor = find_vendor(
            ref,
            invoice.get("supplier_name"),
            invoice.get("supplier_gstin"),
            invoice.get("currency") or "INR",
        )
        if vendor and vendor.get("bank_account"):
            accounts_to_mask.append(vendor["bank_account"])

    # Discover any bank account numbers present in raw_text even if missing from extraction
    if out.get("raw_text"):
        discovered = discover_bank_accounts_in_text(out["raw_text"])
        accounts_to_mask.extend(discovered)

    # Mask raw_text without changing length or character offsets
    if out.get("raw_text"):
        out["raw_text"] = mask_document_text(out["raw_text"], accounts_to_mask)

    # Separate exact evidence offsets from presentation for sensitive fields
    if "evidence" in out and isinstance(out["evidence"], dict):
        ev_copy = {}
        for k, v in out["evidence"].items():
            if k == "bank_account":
                # For sensitive bank account evidence, do not expose full account text in API
                ev_copy[k] = {
                    "start": v["start"],
                    "end": v["end"],
                    "masked_text": v.get("masked_text") or mask_bank_account_preserving_format(v.get("text")),
                }
            else:
                ev_copy[k] = dict(v)
        out["evidence"] = ev_copy

    return out


def get_conn():
    conn = db.connect(get_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_org(x_org_id: str = Header(default="meridian")) -> str:
    return x_org_id


class DecisionRequest(BaseModel):
    user_id: str
    note: str = ""


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/invoices")
async def upload_invoice(file: UploadFile, org_id: str = Depends(get_org), conn=Depends(get_conn)):
    raw_text = (await file.read()).decode("utf-8")
    document_id = (file.filename or "upload").rsplit(".", 1)[0]
    ref = load_reference()
    inv = process_document(conn, ref, raw_text, document_id, org_id=org_id, filename=file.filename)
    return sanitize_invoice_for_api(inv, ref)


@app.get("/api/invoices")
def list_invoices(
    status: str | None = None,
    org_id: str = Depends(get_org),
    conn=Depends(get_conn),
):
    if status is not None:
        if status not in VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status filter: {status}. Supported statuses: {', '.join(sorted(VALID_STATUSES))}",
            )
        rows = conn.execute(
            "SELECT id, document_id, supplier_name, invoice_number, invoice_date, currency, total, status, "
            "needs_review FROM invoices WHERE org_id = ? AND status = ? ORDER BY id DESC",
            (org_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, document_id, supplier_name, invoice_number, invoice_date, currency, total, status, "
            "needs_review FROM invoices WHERE org_id = ? ORDER BY id DESC",
            (org_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: int, conn=Depends(get_conn)):
    invoice = db.get_invoice(conn, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    ref = load_reference()
    invoice["review_reasons_detail"] = build_review_reasons_detail(invoice, ref)
    return sanitize_invoice_for_api(invoice, ref)


@app.post("/api/invoices/{invoice_id}/approve")
def approve_invoice(invoice_id: int, body: DecisionRequest, conn=Depends(get_conn)):
    invoice = db.get_invoice(conn, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if invoice.get("document_type") == "credit_note":
        raise HTTPException(status_code=400, detail="Credit notes cannot be approved for payment")

    if "duplicate" in (invoice.get("review_reasons") or []):
        raise HTTPException(status_code=400, detail="Invoices flagged as duplicate cannot be approved")

    if invoice["status"] in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail=f"Invoice is already {invoice['status']}")

    if invoice.get("needs_review") and not (body.note and body.note.strip()):
        raise HTTPException(status_code=400, detail="A note is required when approving an invoice that needs review")

    existing = conn.execute(
        "SELECT id FROM approvals WHERE invoice_id = ? AND user_id = ?",
        (invoice_id, body.user_id),
    ).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail=f"User {body.user_id} has already approved this invoice")

    try:
        conn.execute(
            "INSERT INTO approvals (invoice_id, user_id, note) VALUES (?, ?, ?)",
            (invoice_id, body.user_id, body.note.strip()),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"User {body.user_id} has already approved this invoice")
    distinct_approvals = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM approvals WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()[0]

    status = invoice["status"]
    if distinct_approvals >= invoice["required_approvals"]:
        status = "approved"
        conn.execute("UPDATE invoices SET status = 'approved' WHERE id = ?", (invoice_id,))
        conn.execute(
            "INSERT OR IGNORE INTO payments (invoice_id, amount) VALUES (?, ?)",
            (invoice_id, invoice["total"]),
        )
    conn.commit()
    return {
        "id": invoice_id,
        "approvals": distinct_approvals,
        "required_approvals": invoice["required_approvals"],
        "status": status,
    }


@app.post("/api/invoices/{invoice_id}/reject")
def reject_invoice(invoice_id: int, body: DecisionRequest, conn=Depends(get_conn)):
    invoice = db.get_invoice(conn, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice["status"] in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail=f"Invoice is already {invoice['status']}")

    conn.execute(
        "INSERT INTO rejections (invoice_id, user_id, note) VALUES (?, ?, ?)",
        (invoice_id, body.user_id, body.note.strip()),
    )
    conn.execute("UPDATE invoices SET status = 'rejected' WHERE id = ?", (invoice_id,))
    conn.commit()
    return {"id": invoice_id, "status": "rejected"}


@app.get("/api/stats")
def stats(org_id: str = Depends(get_org), conn=Depends(get_conn)):
    """Feeds the cost dashboard."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT c.invoice_id) AS invoices, COALESCE(SUM(c.cost_usd), 0) AS cost_usd "
        "FROM llm_calls c JOIN invoices i ON i.id = c.invoice_id WHERE i.org_id = ?",
        (org_id,),
    ).fetchone()
    total_inr = row["cost_usd"] * USD_TO_INR
    return {
        "invoices_processed": row["invoices"],
        "llm_cost_inr": round(total_inr, 2),
        "avg_llm_cost_per_invoice_inr": round(total_inr / row["invoices"], 4) if row["invoices"] else 0.0,
    }
