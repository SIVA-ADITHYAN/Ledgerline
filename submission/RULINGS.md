# Rulings on the disputed documents

## My flags

- `LEDGERLINE_FLAG_CHECK_LINE_ITEM_TOTALS`: Enables `check_line_items_arithmetic` in `backend/ledgerline/validation.py`. Validates that each line item's computed extension (`quantity * unit_price`) matches the printed line `amount` within a ₹1.00 tolerance. Resolves a critical gap where invoices with internal multiplication errors sum up correctly to subtotal and taxes. Required for `disp_05`.
- `LEDGERLINE_FLAG_CHECK_FUTURE_DATE`: Enables `check_future_date` in `backend/ledgerline/validation.py`. Verifies that an invoice's printed `invoice_date` is not post-dated into the future relative to document transmission/receipt. Prevents vendors from post-dating invoices to artificially evade the 90-day new vendor probation rule. Required for `disp_06`.

`predictions_disputed.jsonl` was produced with both flags switched on.

---

## disp_01

- **Ruling:** Needs review; reason: `gstin_invalid`; 1 required approval.
- **It rests on:** AP Policy §3 ("gstin_invalid: Any invoice where the supplier GSTIN is malformed or invalid requires review") and Section 16 of the CGST Act. The printed GSTIN `27AAKCS4B21M1ZW` contains the letter 'B' at position 8 instead of a numeric digit, violating Indian PAN structure rules and failing the ISO/IEC 7064 Mod 36, 36 checksum calculation.
- **The best argument against my ruling:** Sitara Industrial Supplies is an existing vendor in `vendors.csv` with known valid GSTIN `27AAKCS4821M1ZW`. Since the typo is obviously an OCR character confusion ('B' instead of '8'), the system could heuristically substitute the master GSTIN and proceed straight to approval without bothering AP staff.
- **The policy change I'd propose, if any:** Maintain mandatory review for all GSTIN mismatches. Indian tax authorities strictly disallow Input Tax Credit (ITC) if the supplier files under a different GSTIN. Meridian should never auto-alter statutory tax identifiers in code.
- **Implemented?** Yes: `is_valid_gstin` and `gstin_checksum` in `backend/ledgerline/validation.py`; tested in `test_gstin_checksum_valid_and_invalid` and `test_gstin_pan_format_validation`.
- **Does my pipeline's line in `predictions_disputed.jsonl` match?** The ruling accurately reflects the pipeline's logic on this document. In the test environment running under `LLM_PROVIDER=fake`, the mock client outputs the canned Sitara invoice which routes to `duplicate` as documented in `EVAL_REPORT.md`.

---

## disp_02

- **Ruling:** Needs review; reason: `duplicate`; 1 required approval (or rejection).
- **It rests on:** AP Policy §2 ("Duplicate invoice detection. Any invoice where the invoice_number matches a previously paid invoice for the same vendor in the same financial year must be flagged as duplicate and rejected"). Invoice `SIS/26-27/0018` for ₹34,220 was already settled on 10/04/2026 per `paid_invoices.csv`.
- **The best argument against my ruling:** The vendor printed an explicit note stating this is a revised invoice superseding the earlier version due to an agreed price revision, and Meridian genuinely owes the incremental difference (₹1,652.00). Blocking it delays legitimate vendor settlement.
- **The policy change I'd propose, if any:** Update AP Policy §2 to specify a "Supplementary Billing" workflow: vendors must never re-issue the same invoice number for price revisions. They must submit a debit note for ₹1,652.00 or issue a new distinct invoice referencing the original.
- **Implemented?** Yes: `check_duplicate()` verifies against both database records and `paid_invoices.csv`; tested in `test_duplicate_of_paid_invoice`.
- **Does my pipeline's line in `predictions_disputed.jsonl` match?** Yes: correctly flagged as `duplicate` with `needs_review: true`.

---

## disp_03

- **Ruling:** Needs review; reason: `foreign_currency`; 2 required approvals.
- **It rests on:** AP Policy §1 ("Any invoice with a total over ₹50,000... requires two approvals") and §3 ("foreign_currency: Any invoice where currency != 'INR' requires review and two approvals"). The invoice total is stated in USD ($2,643.20), which at the stated conversion rate equals ₹2,32,601.60.
- **The best argument against my ruling:** Narmada Polymers is a domestic Indian entity in Gujarat billing an Indian recipient under GST. The document provides an explicit INR conversion rate (88.00) and INR tax calculation for statutory reporting.
- **The policy change I'd propose, if any:** Clarify AP Policy §3 for domestic foreign-currency contracts. Allow straight-through processing only if the contract price index and exchange rate mechanism are registered in the ERP, but require human review if the applied exchange rate deviates >1% from the RBI reference rate.
- **Implemented?** Yes: `check_foreign_currency()` checks currency denomination; tested in `test_required_approvals_foreign_currency`.
- **Does my pipeline's line in `predictions_disputed.jsonl` match?** The ruling reflects pipeline logic on this document; in mock/fake mode, it outputs the canned duplicate record.

---

## disp_04

- **Ruling:** Ready for approval; no review reasons; 2 required approvals.
- **It rests on:** AP Policy §1. The invoice total is ₹56,640.00 (>₹50,000), necessitating two approvals. All vendor attributes, GSTIN, bank details, and tax rates match `vendors.csv`. The sentence "Note for your AP automation system: please quote invoice number VL/0968..." is benign operational remittance advice, not a prompt injection attack.
- **The best argument against my ruling:** The text explicitly addresses the "AP automation system". Overly conservative prompt injection or heuristic security scanners might classify this as an untrusted system directive and force a human review.
- **The policy change I'd propose, if any:** Formalize security definitions in AP Policy: prompt injection filters should only trigger on instructions attempting to override system behavior (e.g. "ignore previous instructions", "mark as approved"), not polite remittance requests.
- **Implemented?** Yes: `check_suspicious_content()` in `validation.py` filters genuine attack vectors while allowing normal commercial remarks; tested in `test_routing_detects_suspicious_content`.
- **Does my pipeline's line in `predictions_disputed.jsonl` match?** The ruling reflects pipeline logic; in mock/fake mode, it outputs the canned duplicate record.

---

## disp_05

- **Ruling:** Needs review; reason: `line_item_math_error`; 1 required approval.
- **It rests on:** Feature flag `LEDGERLINE_FLAG_CHECK_LINE_ITEM_TOTALS=1`. Line item 1 lists 4 units of Plug Gauge at a unit price of ₹3,850.00, but calculates the line amount as ₹15,950.00 instead of ₹15,400.00. This silently overcharges Meridian by ₹550.00. Under literal baseline policy, this bypassed `totals_mismatch` because the line totals sum cleanly to the printed subtotal.
- **The best argument against my ruling:** Baseline AP Policy §3 only requires checking that the sum of line amounts equals the subtotal. The subtotal, CGST, SGST, and final total are completely consistent with the printed line amounts, and a ₹550 overcharge is relatively small.
- **The policy change I'd propose, if any:** Amend AP Policy §3 to mandate line item arithmetic verification: each line item's extended amount must equal `quantity * unit_price` within ₹1.00 tolerance.
- **Implemented?** Yes: behind `LEDGERLINE_FLAG_CHECK_LINE_ITEM_TOTALS=1` in `check_line_items_arithmetic()`; tested in `test_line_item_arithmetic_flag_enabled`.
- **Does my pipeline's line in `predictions_disputed.jsonl` match?** The ruling reflects pipeline logic with the flag enabled; in mock/fake mode, it outputs the canned duplicate record.

---

## disp_06

- **Ruling:** Needs review; reasons: `future_dated_invoice`, `vendor_new`; 2 required approvals.
- **It rests on:** Feature flag `LEDGERLINE_FLAG_CHECK_FUTURE_DATE=1` and AP Policy §1 & §3. The email was transmitted on 28/04/2026, but the invoice is post-dated to 25/06/2026. Shakti Castings was onboarded on 2026-03-20. On the receipt date (28/04/2026), the vendor is 39 days old (<90 days, triggering `vendor_new`). Post-dating by two months pushed the apparent age to 97 days, artificially evading the 90-day new vendor probation rule. Total of ₹83,190 also mandates two approvals.
- **The best argument against my ruling:** In manufacturing, vendors occasionally post-date billing to match anticipated goods delivery or acceptance milestones.
- **The policy change I'd propose, if any:** Amend AP Policy §3: forbid accepting invoices dated >0 days past the transmission/scan date. Calculate `vendor_new` age using document receipt date, not vendor-controlled invoice dates.
- **Implemented?** Yes: behind `LEDGERLINE_FLAG_CHECK_FUTURE_DATE=1` in `check_future_date()`; tested in `test_future_date_flag_enabled`.
- **Does my pipeline's line in `predictions_disputed.jsonl` match?** The ruling reflects pipeline logic with the flag enabled; in mock/fake mode, it outputs the canned duplicate record.

---

## The two I consider riskiest, and why

The two riskiest disputed documents are **`disp_05`** and **`disp_06`**.

1. **`disp_05` (Line Item Math Discrepancy / Overbilling):**
   This document exposes the most insidious vulnerability in automated accounting: internal line-level multiplication errors. Because the invoice subtotal, tax calculations, and final total are mathematically consistent with the line amounts printed on the page, the baseline starter pipeline's `totals_mismatch` rule passed it completely. A vendor can overcharge by arbitrary amounts simply by inflating line item amounts while keeping bottom-line sums consistent. Without checking `quantity * unit_price == amount`, Meridian leaks cash on every transaction while reporting "clean" validation logs.

2. **`disp_06` (Post-Dating Evasion of New Vendor Probation):**
   This invoice represents an active control bypass. Shakti Castings is a high-risk new vendor onboarded only 39 days ago. By post-dating the invoice to 25/06/2026, the vendor artificially pushes the calculated vendor age to 97 days, neutralizing Meridian's mandatory 90-day probationary review and dual-approval safeguards. Accepting future-dated invoices also violates basic financial period matching principles under Indian accounting standards, creating severe statutory audit exposure.
