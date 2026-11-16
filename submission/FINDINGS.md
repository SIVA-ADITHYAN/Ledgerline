# Findings

## The three fixes I would ship first, and why

1. **Fix 1: Deterministic Duplicate Invoice Detection with Financial Year & Reference Data Matching (`04ba0f0`, `c5c9f3d`)**
   - **Why:** Paying an invoice twice creates immediate, unrecoverable cash loss from Meridian's bank account. Accidental resubmissions and revised invoices are common in high-volume accounts payable. Eliminating duplicate disbursements protects corporate capital immediately without requiring any change to vendor habits.
2. **Fix 2: Approval Idempotency, Database Transaction Isolation & Dual-Approver Segregation (`c5c9f3d`)**
   - **Why:** Kavya Iyer confirmed that double-clicking "Approve" during system latency created duplicate payment entries in Treasury. Furthermore, high-value invoices (>₹50,000) could be approved twice by the same user. Enforcing atomic database constraints and distinct approvers prevents dual cash outflow and restores statutory segregation of duties.
3. **Fix 3: Vendor Bank Detail Substitution Validation & Prompt Privacy Masking (`a9793ed`, `04ba0f0`)**
   - **Why:** Meridian already suffered a ₹3.8 lakh fraud loss last quarter from a spoofed bank details email. Validating extracted account and IFSC numbers against the verified vendor master in deterministic code—while masking account numbers before LLM egress—prevents payment diversion fraud and ensures legal compliance with data privacy policies.

---

## My top eight, ranked by business risk

### F1: Duplicate Invoice Detection Failure Across Financial Years and Paid History

- **Severity:** Critical
- **Why it ranks here:** Duplicate payments cause immediate, direct balance-sheet losses. Once funds are wired to a vendor's bank account, recovery requires manual clawback negotiations or legal notices, severely damaging cash flow.
- **Where:** `backend/ledgerline/validation.py` (`check_duplicate`), `backend/ledgerline/db.py`, `backend/ledgerline/pipeline.py`
- **What's wrong:** The starter pipeline relied on exact string equality in a volatile database and did not check `data/reference/paid_invoices.csv`. Furthermore, it lacked financial year (FY) awareness (e.g., Indian fiscal year running April 1 to March 31), allowing invoices from new financial years to be marked as duplicates of older ones, or identical invoice numbers from distinct vendors to collide.
- **Impact:** In the dev set, `dev_002` (Sitara Industrial Supplies `SIS/26-27/0018` for ₹35,872) is an invoice that was already settled on 10/04/2026. The starter system marked it as `ready_for_approval` with `review_reasons: []`, allowing an immediate duplicate payout of ₹35,872.
- **How I found it:** Code inspection of `check_duplicate` in `validation.py` followed by running the evaluation against `paid_invoices.csv`.
- **Evidence from my run (F1 to F3):** On `dev_002`, the unpatched starter pipeline output:
  `{"document_id": "dev_002", "invoice_number": "SIS/26-27/0018", "needs_review": false, "review_reasons": [], "status": "ready_for_approval"}`
- **What it could cost Meridian (F1 to F3):** Assuming 3,000 invoices per month with an average invoice value of ₹45,000, if even 0.5% of vendor submissions are duplicate or revised billings, an unmitigated duplicate leak represents ₹6,75,000 per month (over ₹80 lakh annually) in erroneous cash outflows.
- **Fix:** Commits `04ba0f0` and `c5c9f3d` implemented cross-checking against `paid_invoices.csv`, compound matching on `(vendor_id, invoice_number, financial_year)`, and database duplicate tracking.
- **Regression test:** `test_duplicate_of_paid_invoice`, `test_routing_overrides_llm_when_duplicate_detected`
- **Status:** Fixed.

---

### F2: Silent Vendor Bank Detail Substitution and Account Takeover Vulnerability

- **Severity:** Critical
- **Why it ranks here:** Payment diversion fraud (Business Email Compromise or rogue invoice PDF injection) routes company disbursements directly to fraudulent third-party bank accounts.
- **Where:** `backend/ledgerline/validation.py` (`check_bank_details`), `backend/ledgerline/extraction.py`
- **What's wrong:** The starter pipeline did not compare extracted bank account numbers or IFSC codes against `vendors.csv` in deterministic Python code. It relied entirely on the LLM to notice bank discrepancies, which LLMs routinely miss or ignore when prompts are lengthy.
- **Impact:** If an attacker modifies the bank account or IFSC printed on a vendor's invoice, the invoice would be approved and paid to the fraudster's account without any human intervention or review trigger.
- **How I found it:** Review of `docs/STAKEHOLDER_THREAD.md` where Farhan Sheikh noted a ₹3.8 lakh loss from a fake bank account email, followed by inspecting `backend/ledgerline/validation.py` which lacked deterministic bank comparison logic.
- **Evidence from my run (F1 to F3):** When processing an invoice with vendor Sitara Industrial Supplies but an altered IFSC (`ZEPH9999999`) or account (`9999999999`), the starter returned:
  `{"needs_review": false, "review_reasons": []}`
- **What it could cost Meridian (F1 to F3):** Farhan confirmed a historical loss of ₹3,80,000 in a single quarter. At Meridian's scale, a single diverted high-value payment can easily exceed ₹10,00,000, plus legal and banking tracing overhead.
- **Fix:** Commit `a9793ed` implemented `check_bank_details()` to compare normalized account numbers and IFSC against the approved vendor master, raising `bank_details_changed` whenever they differ.
- **Regression test:** `test_routing_overrides_llm_when_bank_details_changed`, `test_bank_details_changed_masks_accounts_in_reasons_detail`
- **Status:** Fixed.

---

### F3: Approval Race Condition, Missing User Separation & Duplicate Payment Row Creation

- **Severity:** Critical
- **Why it ranks here:** Directly creates duplicate payment orders in Meridian's treasury banking integration and violates core internal financial controls (Maker-Checker dual approval separation).
- **Where:** `backend/ledgerline/api.py` (`approve_invoice`), `backend/ledgerline/db.py`
- **What's wrong:** The `/api/invoices/{id}/approve` endpoint lacked idempotency controls and transaction locking. When a reviewer clicked "Approve" multiple times during UI lag, concurrent requests each inserted a new row into the `payments` table. Additionally, on invoices requiring two approvals (>₹50,000), the system allowed the same user to approve twice consecutively.
- **Impact:** Treasury payment batches picked up duplicate payout instructions, resulting in actual dual payments for single invoices. In addition, single individuals could unilaterally authorize payments exceeding ₹50,000 by approving twice.
- **How I found it:** Noted in stakeholder thread by Kavya Iyer ("clicking Approve sometimes creates two payment entries"), verified by sending concurrent curl POST requests to the approval endpoint.
- **Evidence from my run (F1 to F3):** Executing two sequential POST calls with payload `{"user": "ap_reviewer_1"}` on an invoice requiring 2 approvals moved the invoice to `approved` and created two rows in the `payments` table for the same invoice.
- **What it could cost Meridian (F1 to F3):** Duplicate disbursements on high-value invoices (>₹50,000) risk ₹1,00,000 to ₹5,00,000 per incident. Processing reversals with corporate banking portals incurs bank fees and takes 2–4 weeks of administrative effort.
- **Fix:** Commit `c5c9f3d` added a SQLite unique constraint on `payments(invoice_id)`, enforced distinct approver validation (`user != prior_approver`), and introduced atomic transaction state transitions.
- **Regression test:** `test_approve_already_approved_invoice_fails_and_does_not_duplicate_payment`, `test_same_user_cannot_approve_twice`
- **Status:** Fixed.

---

### F4: LLM-Driven Review Routing Instead of Deterministic Code Validation

- **Severity:** High
- **Why it ranks here:** Generative models are probabilistic. Delegating boolean business rules and compliance routing to an LLM produces non-deterministic decisions, hallucinations, and prompt injection vulnerabilities.
- **Where:** `backend/ledgerline/pipeline.py`, `backend/ledgerline/extraction.py`, `backend/ledgerline/validation.py`
- **What's wrong:** The starter pipeline asked the LLM to output `requires_review` and `review_reasons`. Even when deterministic Python validation checks discovered policy failures, the final decision was often overridden or diluted by the LLM response.
- **Impact:** Invoices with statutory defects (tax type mismatches, missing POs, arithmetic discrepancies) were marked `ready_for_approval` because the LLM prompt concluded the invoice looked benign.
- **How I found it:** Code inspection of `pipeline.py` and discrepancy between `validation.py` results and `got["needs_review"]`.
- **Fix:** Commit `f31ffce` restructured `pipeline.py` so that deterministic Python code owns all routing decisions, status assignment, and approval thresholds. The LLM is restricted solely to unstructured text extraction.
- **Regression test:** `test_routing_overrides_llm_when_duplicate_detected`, `test_routing_preserves_multiple_validation_reasons`
- **Status:** Fixed.

---

### F5: Invalid GSTIN Checksum and PAN Formatting Bypass (Tax Credit Loss Risk)

- **Severity:** High
- **Why it ranks here:** Under Section 16 of the Indian CGST Act, Input Tax Credit (ITC) is strictly disallowed if the supplier's GSTIN is invalid or misreported on the invoice. Disallowed ITC becomes a direct corporate tax expense.
- **Where:** `backend/ledgerline/validation.py` (`is_valid_gstin`, `gstin_checksum`, `check_gstin`)
- **What's wrong:** The starter only checked whether the GSTIN was a 15-character string. It did not validate the embedded 10-character PAN structure (characters 6–9 must be digits, character 10 an uppercase letter) nor verify the official ISO/IEC 7064 Mod 36, 36 check digit at position 15.
- **Impact:** Invoices with typographical errors in the GSTIN (e.g. `disp_01` with `27AAKCS4B21M1ZW` where 'B' replaces '8') passed without review, causing Meridian to lose 18% tax credit on the full invoice value.
- **How I found it:** Analyzed `disp_01.txt` and inspected `is_valid_gstin` regex in `validation.py`.
- **Fix:** Commit `04ba0f0` implemented complete PAN syntax validation and the full ISO/IEC 7064 Mod 36, 36 checksum verification algorithm.
- **Regression test:** `test_gstin_checksum_valid_and_invalid`, `test_gstin_pan_format_validation`
- **Status:** Fixed.

---

### F6: Unmasked Vendor Bank Accounts in External LLM Prompts and API Responses (Privacy Violation)

- **Severity:** High
- **Why it ranks here:** Violates Section 5 of Meridian's AP Policy and the Digital Personal Data Protection Act (DPDPA). Exposing complete bank account numbers in third-party API payloads creates severe compliance liability.
- **Where:** `backend/ledgerline/extraction.py`, `backend/ledgerline/api.py`, `backend/ledgerline/db.py`
- **What's wrong:** Raw document text and vendor master CSV containing full bank account numbers were passed in plain text to the LLM provider. Furthermore, GET endpoints returned unmasked account digits to the frontend DOM.
- **Impact:** Meridian's confidential banking records were transmitted to external cloud providers and exposed to anyone inspecting browser network traffic.
- **How I found it:** DPO Priya Natarajan's warning in `docs/STAKEHOLDER_THREAD.md` and auditing API responses in `backend/ledgerline/api.py`.
- **Fix:** Commit `a9793ed` and subsequent Step 2/3 privacy hardening added prompt masking, API-level sanitization (`mask_bank_account`), and raw-text regex discovery that masks account digits while preserving exact character lengths and evidence offsets.
- **Regression test:** `test_bank_account_masked_in_prompt`, `test_api_responses_strictly_mask_bank_accounts`, `test_unextracted_bank_account_in_raw_text_masked_in_api`
- **Status:** Fixed.

---

### F7: Missing 90-Day New Vendor Probation Policy Check (`vendor_new`)

- **Severity:** High
- **Why it ranks here:** Brand new vendors represent the highest operational and fraud risk. AP Policy §3 explicitly requires human review and two approvals for any vendor onboarded within the last 90 days.
- **Where:** `backend/ledgerline/validation.py` (`check_vendor_new`)
- **What's wrong:** The starter pipeline had no logic to compare vendor `onboarded_on` dates against the invoice date, completely ignoring the 90-day probation requirement.
- **Impact:** Invoices from newly onboarded vendors (e.g. `dev_007` onboarded 30 days prior) passed through as single-approval straight-through invoices without the mandatory secondary review.
- **How I found it:** Cross-referencing `docs/AP_POLICY.md` section 3 against `backend/ledgerline/validation.py`.
- **Fix:** Commit `04ba0f0` added `check_vendor_new()`, computing elapsed calendar days between `vendor.onboarded_on` and `invoice.invoice_date`, triggering `vendor_new` and requiring 2 approvals.
- **Regression test:** `test_vendor_new_within_90_days`, `test_vendor_new_uses_invoice_date_not_processing_date`
- **Status:** Fixed.

---

### F8: 1000x Cost Underreporting in Dashboard Due to Token Pricing Division Bug

- **Severity:** Medium / High
- **Why it ranks here:** Directly misled executive management regarding operational cloud expenditure, creating false confidence about unit economics.
- **Where:** `backend/ledgerline/llm/pricing.py` (`USD_PER_MTOK`, `cost_usd`)
- **What's wrong:** Token pricing in `USD_PER_MTOK` was entered as `$0.003` per million tokens instead of `$3.00` per million tokens. The calculation in `cost_usd()` then divided by 1,000,000 a second time, underreporting costs by a factor of 1,000x.
- **Impact:** Explained why CTO Deepak Menon saw $165 billed by Anthropic while Ledgerline reported only ₹15. Management was blind to true AI operating expenses.
- **How I found it:** Deepak Menon's prompt in `docs/STAKEHOLDER_THREAD.md` cross-referenced with `backend/ledgerline/llm/pricing.py`.
- **Fix:** Updated token pricing definitions to official provider rates ($3.00/$15.00 per MTok for Sonnet) and verified INR conversion rates.
- **Regression test:** `test_pricing_calculation`
- **Status:** Fixed.

---

## Everything else I found (one line each)

- **Line item math discrepancy:** Stated unit price multiplied by quantity does not equal line amount (`disp_05`), which bypassed `totals_mismatch` because line amounts correctly summed to subtotal.
- **Future-dated invoice evasion:** Invoices post-dated into the future (`disp_06`) artificially bypassed the 90-day new vendor probation rule.
- **Silent failure dropping in evaluation:** `backend/eval/run_eval.py` caught and ignored all processing exceptions, artificially inflating reported accuracy.
- **Dev-set prompt leakage:** Ground-truth files `dev_001`, `dev_002`, and `dev_003` (12.5% of dev set) were hardcoded in `extraction.py` few-shot examples.
- **Floating-point rounding inaccuracies:** Invoice balance comparisons used Python floats instead of exact `Decimal` arithmetic, causing false tolerance alarms.
- **Domestic multi-currency vulnerability:** Domestic Indian vendors billing in USD (`disp_03`) lacked automated exchange rate validation against RBI reference rates.
- **Modifiable terminal invoices in UI:** Reviewers could still trigger approve/reject actions on already terminal invoices prior to frontend UI action disabling.
- **Unicode code point vs UTF-16 offset divergence:** Astral Unicode characters (such as emojis or currency symbols) caused text offset drift between backend and browser DOM.

---

## Found but not fixed

- **HSN code statutory tax rate validation:** The system does not cross-reference HSN codes against the statutory GST tariff (e.g. checking whether 7318 should be 18% or 12%). This was deferred because it requires maintaining a 10,000-entry HSN rate master.
- **Automated phone call-back verification portal:** As Sunil Joshi pointed out, vendor master records currently lack phone numbers. Implementing the physical call-back workflow requires vendor master schema updates and telephony integration.
- **Live RBI exchange rate API integration:** For foreign currency invoices, conversion to INR relies on the printed contract rate rather than querying the real-time Reserve Bank of India reference rate.

---

## Things I'm not sure are problems at all

- **Operational notes mistaken for prompt injections:** Documents like `disp_04` containing standard AP notes (e.g. "please quote invoice number in remittance advice") could be flagged by overly aggressive prompt injection filters. These are standard business communications, not attacks.
- **Credit note sign conventions:** Different ERPs output credit notes with positive numbers and a credit header, while others use negative numbers. Enforcing negative numbers everywhere risks rejecting standard vendor formats.
