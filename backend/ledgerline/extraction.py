"""Turn a raw vendor document into structured fields and a routing decision, using the LLM."""
from __future__ import annotations

import csv
import io
import json
import re

from .config import get_settings
from .llm import LLMResponse, get_client
from .reference import ReferenceData

FIELDS = [
    "document_type", "supplier_name", "supplier_gstin", "invoice_number", "invoice_date", "currency",
    "subtotal", "cgst", "sgst", "igst", "round_off", "total", "bank_account", "ifsc", "po_number",
    "notes", "line_items",
]

SYSTEM_PROMPT = (
    "You are Ledgerline, an expert accounts-payable assistant for Meridian Precision Components "
    "Pvt. Ltd. You read vendor documents, extract their data and apply Meridian's AP policy."
)


def mask_bank_account(account: str | None) -> str | None:
    """Mask all but the last four digits of a bank account number for privacy."""
    if account is None:
        return None
    raw = str(account).strip()
    if not raw:
        return raw
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return "*" * len(raw)
    if len(digits) <= 4:
        return "*" * len(digits)
    return ("*" * (len(digits) - 4)) + digits[-4:]


def build_masked_vendors_csv(ref: ReferenceData) -> str:
    """Build a CSV string of vendor master records with bank accounts masked.

    Does not modify ref.vendors or ref.vendors_csv.
    """
    if not ref.vendors:
        return ref.vendors_csv
    output = io.StringIO()
    fieldnames = list(ref.vendors[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for v in ref.vendors:
        row = dict(v)
        if "bank_account" in row:
            row["bank_account"] = mask_bank_account(row["bank_account"])
        writer.writerow(row)
    return output.getvalue()


def _few_shot_examples(ref: ReferenceData | None = None, limit: int = 3) -> str:
    """A few labelled examples so the model learns our exact output format.

    Masks vendor bank account numbers in both document text and expected outputs.
    """
    data_dir = get_settings().data_dir
    blocks = []
    known_accounts: list[str] = []
    if ref and ref.vendors:
        for v in ref.vendors:
            acct = v.get("bank_account")
            if acct and len(acct.strip()) > 4:
                known_accounts.append(acct.strip())

    with open(data_dir / "ground_truth" / "dev.jsonl", encoding="utf-8") as f:
        for line in list(f)[:limit]:
            row = json.loads(line)
            expected = dict(row["expected"])
            raw_acct = expected.get("bank_account")
            if raw_acct:
                expected["bank_account"] = mask_bank_account(raw_acct)
            doc = (data_dir / "invoices" / "dev" / row["filename"]).read_text(encoding="utf-8")
            for acct in known_accounts:
                if acct in doc:
                    doc = doc.replace(acct, mask_bank_account(acct) or "")
            if raw_acct and len(raw_acct.strip()) > 4 and raw_acct.strip() in doc:
                doc = doc.replace(raw_acct.strip(), mask_bank_account(raw_acct.strip()) or "")
            blocks.append(f"DOCUMENT:\n{doc}\nOUTPUT:\n{json.dumps(expected)}")
    return "\n\n".join(blocks)


def build_extraction_prompt(document_text: str, ref: ReferenceData) -> str:
    masked_vendors_csv = build_masked_vendors_csv(ref)
    return f"""Read the vendor document below, extract its fields, and decide whether it needs human review under our AP policy.

AP POLICY:
{ref.policy_text}

VENDOR MASTER (CSV):
{masked_vendors_csv}

EXAMPLES:
{_few_shot_examples(ref=ref)}

Return a JSON object with these keys: {", ".join(FIELDS)}, requires_review (true/false) and review_reasons (list of policy codes).
Copy amounts and dates exactly as they are printed in the document.

{document_text}
"""


def build_verification_prompt(document_text: str, extraction: dict, ref: ReferenceData) -> str:
    masked_extraction = dict(extraction)
    if "bank_account" in masked_extraction:
        masked_extraction["bank_account"] = mask_bank_account(masked_extraction["bank_account"])
    masked_vendors_csv = build_masked_vendors_csv(ref)
    return f"""You extracted the JSON below from a vendor document. Check every field against the document and our AP policy, fix anything that is wrong, and return the corrected JSON object with the same keys.

EXTRACTION:
{json.dumps(masked_extraction, indent=2)}

AP POLICY:
{ref.policy_text}

VENDOR MASTER (CSV):
{masked_vendors_csv}

{document_text}
"""


def extract(document_text: str, ref: ReferenceData) -> tuple[dict, list[LLMResponse]]:
    """Two passes: extract, then let the model double-check its own work (+2 points on the eval)."""
    client = get_client()
    first = client.complete(SYSTEM_PROMPT, build_extraction_prompt(document_text, ref))
    first_data = json.loads(first.text)
    second = client.complete(SYSTEM_PROMPT, build_verification_prompt(document_text, first_data, ref))
    second_data = json.loads(second.text)

    # If verification returned a masked bank account, restore the original unmasked bank account
    # from pass 1 for local deterministic validation against vendor master.
    first_acct = first_data.get("bank_account")
    second_acct = second_data.get("bank_account")
    if first_acct and second_acct and ("*" in str(second_acct) or "X" in str(second_acct)):
        second_data["bank_account"] = first_acct
    elif not second_acct and first_acct:
        second_data["bank_account"] = first_acct

    return second_data, [first, second]
