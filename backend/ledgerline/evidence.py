"""Extract exact character offsets and text evidence for extracted fields (Part 4A).

Invariant for every returned entry:
    raw_text[entry["start"]:entry["end"]] == entry["text"]

Unicode Offset Convention:
    Offsets (start and end) count Unicode code points (Python string indexing),
    as defined by docs/OUTPUT_CONTRACT.md.
    In JavaScript, which indexes strings by UTF-16 code units (where non-BMP
    characters such as emoji use 2 code units as surrogate pairs), consumers
    can map a Python code-point offset to a JavaScript UTF-16 offset using:

        function pyCodePointToJsUtf16Offset(str, pyOffset) {
            return Array.from(str).slice(0, pyOffset).join("").length;
        }
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import re

from .extraction import mask_bank_account
from .parsing import parse_date


def format_indian_number(val: float | int | Decimal) -> str:
    """Format a number with Indian comma grouping (e.g. 141600.0 -> '1,41,600.00')."""
    s = f"{float(val):.2f}"
    parts = s.split(".")
    integer_part = parts[0]
    decimal_part = "." + parts[1] if len(parts) > 1 else ""
    if len(integer_part) <= 3:
        return integer_part + decimal_part
    last_three = integer_part[-3:]
    remaining = integer_part[:-3]
    groups = []
    while len(remaining) > 2:
        groups.append(remaining[-2:])
        remaining = remaining[:-2]
    if remaining:
        groups.append(remaining)
    groups.reverse()
    return ",".join(groups) + "," + last_three + decimal_part


def _get_amount_candidates(val: float | int | str | None) -> list[str]:
    """Generate string representations of an amount (plain, Indian commas, standard commas, integer)."""
    if val is None or val == "":
        return []
    try:
        num = float(str(val).replace(",", "").replace("₹", "").replace("INR", "").strip())
    except (ValueError, TypeError):
        return [str(val).strip()]

    candidates = [
        format_indian_number(num),
        f"{num:,.2f}",
        f"{num:.2f}",
    ]
    if num.is_integer():
        int_val = int(num)
        candidates.append(str(int_val))
        indian_int = format_indian_number(num).split(".")[0]
        if indian_int not in candidates:
            candidates.append(indian_int)
        std_int = f"{int_val:,}"
        if std_int not in candidates:
            candidates.append(std_int)

    # Deduplicate preserving order
    return list(dict.fromkeys(candidates))


def _get_date_candidates(val: date | str | None) -> list[str]:
    """Generate potential formatted date representations found in vendor invoices."""
    if not val:
        return []
    parsed_date = None
    if isinstance(val, date):
        parsed_date = val
    elif isinstance(val, str):
        try:
            parsed_date = date.fromisoformat(val.strip())
        except (ValueError, TypeError):
            parsed_date = parse_date(val)

    candidates = []
    if isinstance(val, str) and val.strip():
        candidates.append(val.strip())

    if parsed_date:
        candidates.extend([
            parsed_date.strftime("%d/%m/%Y"),
            parsed_date.strftime("%d-%m-%Y"),
            parsed_date.strftime("%d.%m.%Y"),
            parsed_date.strftime("%Y-%m-%d"),
            parsed_date.strftime("%m/%d/%Y"),
            f"{parsed_date.day}/{parsed_date.month}/{parsed_date.year}",
            f"{parsed_date.day}-{parsed_date.month}-{parsed_date.year}",
            parsed_date.strftime("%d %b %Y"),
            parsed_date.strftime("%d %B %Y"),
            parsed_date.strftime("%B %d, %Y"),
        ])

    return list(dict.fromkeys(candidates))


def _build_candidate_pattern(cand: str, allow_flexible_whitespace: bool = False) -> str:
    """Build a regex pattern for a candidate string with boundaries and optional flexible whitespace."""
    if allow_flexible_whitespace and " " in cand:
        words = cand.split()
        if len(words) > 1:
            cand_pat = r"\s+".join(re.escape(w) for w in words)
        else:
            cand_pat = re.escape(cand)
    else:
        cand_pat = re.escape(cand)

    # Boundary guards
    if cand[0].isdigit():
        # Do not match if preceded by another digit, or decimal dot following a digit (e.g. 0.100)
        cand_pat = r"(?<!\d)(?<!\d\.)" + cand_pat
    elif cand[0].isalnum():
        cand_pat = r"\b" + cand_pat

    if cand[-1].isdigit():
        # If candidate has no decimal dot, do not match if followed by digit or .digit (e.g. 100 in 100.50)
        if "." not in cand:
            cand_pat = cand_pat + r"(?![\d]|\.\d)"
        else:
            cand_pat = cand_pat + r"(?!\d)"
    elif cand[-1].isalnum():
        cand_pat = cand_pat + r"\b"

    return cand_pat


def _find_field_match(
    raw_text: str,
    candidates: list[str],
    anchor_patterns: list[str] | None = None,
    allow_flexible_whitespace: bool = False,
    is_amount: bool = False,
    field_name: str | None = None,
) -> dict | None:
    """Find the best match for a field using contextual anchors first, then uniqueness."""
    if not candidates:
        return None

    # 1. Contextual Anchor Search
    if anchor_patterns:
        for anchor in anchor_patterns:
            for cand in candidates:
                if not cand:
                    continue
                cand_pat = _build_candidate_pattern(cand, allow_flexible_whitespace)
                sep = r"[^\n\r\d]{0,120}" if is_amount else r"[^\n\r\w]{0,80}"
                pattern = re.compile(
                    rf"(?:{anchor}){sep}({cand_pat})",
                    re.IGNORECASE,
                )
                for match in pattern.finditer(raw_text):
                    # Guard for total vs subtotal: ensure anchor for 'total' is not preceded by 'sub'
                    if field_name == "total" or "total" in anchor.lower():
                        match_start = match.start()
                        prefix = raw_text[max(0, match_start - 15):match_start].rstrip().lower()
                        if prefix.endswith("sub") or prefix.endswith("sub-"):
                            continue
                    start = match.start(1)
                    end = match.end(1)
                    text = raw_text[start:end]
                    return {"start": start, "end": end, "text": text}

    # 2. Unique Exact Match Search (only if unambiguous)
    for cand in candidates:
        if not cand or len(cand) < 2:
            continue
        cand_pat = _build_candidate_pattern(cand, allow_flexible_whitespace)
        matches = list(re.finditer(cand_pat, raw_text, re.IGNORECASE))
        if len(matches) == 1:
            m = matches[0]
            start = m.start()
            end = m.end()
            text = raw_text[start:end]
            return {"start": start, "end": end, "text": text}

    return None


def extract_evidence(raw_text: str, data: dict) -> dict[str, dict]:
    """Extract exact source evidence (start, end, text) for extracted fields.

    Enforces the invariant:
        raw_text[entry['start']:entry['end']] == entry['text']
    Omit a field rather than guess.
    """
    evidence: dict[str, dict] = {}

    # 1. Invoice Number
    inv_num = data.get("invoice_number")
    if inv_num:
        cand = str(inv_num).strip()
        anchors = [
            r'invoice\s*(?:no\.?|num(?:ber)?|#)',
            r'inv\s*no\.?',
            r'bill\s*no\.?',
            r'doc\s*no\.?',
            r'"No"\s*:\s*"',
            r'\binvoice\b(?!\s+(?:total|date|due|amount|value|tax|sub))',
            r'\binv\b(?!\s+(?:total|date|due|amount|value|tax|sub))',
        ]
        match = _find_field_match(raw_text, [cand], anchors, field_name="invoice_number")
        if match:
            evidence["invoice_number"] = match

    # 2. Invoice Date
    inv_date = data.get("invoice_date")
    if inv_date:
        candidates = _get_date_candidates(inv_date)
        anchors = [
            r'dated\s*[:\.]?',
            r'invoice\s*date',
            r'inv\s*date',
            r'date\s*[:\.]?',
            r'"Dt"\s*:\s*"',
        ]
        match = _find_field_match(raw_text, candidates, anchors, field_name="invoice_date")
        if match:
            evidence["invoice_date"] = match

    # 3. Supplier GSTIN
    gstin = data.get("supplier_gstin")
    if gstin and str(gstin).strip():
        cand = str(gstin).strip().upper()
        anchors = [
            r'SellerDtls.*?Gstin"\s*:\s*"',
            r'supplier\s*gstin',
            r'gstin/uin',
            r'gstin',
        ]
        match = _find_field_match(raw_text, [cand], anchors, field_name="supplier_gstin")
        if match:
            evidence["supplier_gstin"] = match

    # 4. Supplier Name
    name = data.get("supplier_name")
    if name and str(name).strip():
        cand = str(name).strip()
        anchors = [
            r'SellerDtls.*?LglNm"\s*:\s*"',
            r'supplier\s*name',
            r'from\s*:\s*"',
            r'for\s+',
        ]
        match = _find_field_match(
            raw_text,
            [cand],
            anchors,
            allow_flexible_whitespace=True,
            field_name="supplier_name",
        )
        if match:
            evidence["supplier_name"] = match

    # 5. PO Number
    po = data.get("po_number")
    if po and str(po).strip():
        cand = str(po).strip()
        anchors = [
            r'buyer\'?s\s*order\s*no\.?',
            r'purchase\s*order\s*(?:no\.?)?',
            r'po\s*(?:no\.?|num(?:ber)?|#)',
            r'po\s*[:\.]?',
        ]
        match = _find_field_match(raw_text, [cand], anchors, field_name="po_number")
        if match:
            evidence["po_number"] = match

    # 6. Total
    total = data.get("total")
    if total is not None and str(total).strip() != "":
        candidates = _get_amount_candidates(total)
        anchors = [
            r'grand\s*total',
            r'invoice\s*total',
            r'total\s*due(?:\s*\([a-zA-Z\s]+\))?',
            r'total\s*amount',
            r'amount\s*payable',
            r'totinvval',
            r'net\s*amount',
            r'total\s*₹',
            r'(?<!sub)(?<!sub\s)(?<!sub\s\s)(?<!sub\-)total\b',
        ]
        match = _find_field_match(raw_text, candidates, anchors, is_amount=True, field_name="total")
        if match:
            evidence["total"] = match

    # 7. Subtotal
    subtotal = data.get("subtotal")
    if subtotal is not None and str(subtotal).strip() != "":
        candidates = _get_amount_candidates(subtotal)
        anchors = [
            r'sub\s*total',
            r'taxable\s*value',
            r'taxable\s*amount',
            r'assamt',
            r'subtotal',
        ]
        match = _find_field_match(raw_text, candidates, anchors, is_amount=True, field_name="subtotal")
        if match:
            evidence["subtotal"] = match

    # 8. CGST
    cgst = data.get("cgst")
    if cgst is not None and float(cgst or 0) > 0:
        candidates = _get_amount_candidates(cgst)
        anchors = [
            r'cgst\s*@\s*[\d\.]+%',
            r'cgst\s*\([\d\.]+%\)',
            r'cgst\s*amt',
            r'cgstamt',
            r'cgst',
        ]
        match = _find_field_match(raw_text, candidates, anchors, is_amount=True, field_name="cgst")
        if match:
            evidence["cgst"] = match

    # 9. SGST
    sgst = data.get("sgst")
    if sgst is not None and float(sgst or 0) > 0:
        candidates = _get_amount_candidates(sgst)
        anchors = [
            r'sgst\s*@\s*[\d\.]+%',
            r'sgst\s*\([\d\.]+%\)',
            r'sgst\s*amt',
            r'sgstamt',
            r'sgst',
        ]
        match = _find_field_match(raw_text, candidates, anchors, is_amount=True, field_name="sgst")
        if match:
            evidence["sgst"] = match

    # 10. IGST
    igst = data.get("igst")
    if igst is not None and float(igst or 0) > 0:
        candidates = _get_amount_candidates(igst)
        anchors = [
            r'igst\s*@\s*[\d\.]+%',
            r'igst\s*\([\d\.]+%\)',
            r'igst\s*amt',
            r'igstamt',
            r'igst',
        ]
        match = _find_field_match(raw_text, candidates, anchors, is_amount=True, field_name="igst")
        if match:
            evidence["igst"] = match

    # 11. Bank Account
    bank_acct = data.get("bank_account")
    if bank_acct and str(bank_acct).strip():
        cand = str(bank_acct).strip()
        anchors = [
            r'a/c\s*(?:no\.?)?',
            r'account\s*(?:no\.?|number)?',
            r'accdet',
            r'bank\s*a/c',
        ]
        match = _find_field_match(raw_text, [cand], anchors, field_name="bank_account")
        if match:
            match["masked_text"] = mask_bank_account(match["text"])
            evidence["bank_account"] = match

    # 12. IFSC
    ifsc = data.get("ifsc")
    if ifsc and str(ifsc).strip():
        cand = str(ifsc).strip().upper()
        anchors = [
            r'ifs\s*code',
            r'ifsc(?:\s*code)?',
            r'ifsc',
        ]
        match = _find_field_match(raw_text, [cand], anchors, field_name="ifsc")
        if match:
            evidence["ifsc"] = match

    # Strict invariant validation: raw_text[start:end] must match text exactly
    verified: dict[str, dict] = {}
    for k, ev in evidence.items():
        start, end, text = ev["start"], ev["end"], ev["text"]
        if raw_text[start:end] == text:
            verified[k] = ev

    return verified
