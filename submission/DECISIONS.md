# Decisions

## Architecture

Ledgerline's production architecture is built on a strict separation of concerns between **probabilistic AI extraction** and **deterministic software validation**:

1. **What the Model Does:**
   The LLM is treated as an advanced perceptual parser. It reads raw invoice text or OCR output across hundreds of unpredictable vendor layouts and converts unstructured text into a structured, strongly-typed JSON schema (`Invoice`). It extracts field values and identifies source evidence snippets.
2. **What Code Does:**
   All business logic, statutory compliance checks, and approval routing are executed exclusively in deterministic Python code. Python validates:
   - ISO/IEC 7064 Mod 36, 36 GSTIN check digits and PAN structure
   - Duplicate detection against `paid_invoices.csv` and local database history across financial years
   - Comparison of extracted bank account numbers and IFSC against approved vendor master records
   - Exact mathematical consistency (`Decimal` arithmetic for subtotal, CGST, SGST, IGST, round-off, and line items)
   - Approval routing rules (1 vs 2 approvals, `needs_review` vs `ready_for_approval`)
   - Atomic database idempotency and distinct Maker-Checker approver constraints
3. **Prompt & Output Design:**
   Prompts use structured few-shot examples, system role boundaries, and strict JSON output schemas. Bank account numbers in few-shot demonstrations and vendor master tables are strictly masked prior to prompt serialization.
4. **Handling Hostile & Non-Standard Documents:**
   Raw document text is enclosed within explicit XML delimiters (`<DOCUMENT>...</DOCUMENT>`) with clear instructions that documents contain untrusted vendor content. Heuristic filters check for prompt injection keywords ("ignore previous instructions", "system override") while preserving standard operational remarks (such as remittance advice notes).
5. **Data Egress Boundary:**
   Under Meridian AP Policy Section 5 and DPDPA, full vendor bank account numbers never leave Meridian's network. Bank account numbers are masked (`**********5678`) before transmission to external LLM providers and before serialization to reviewer APIs. Complete account numbers remain encrypted locally within SQLite for payment execution.

---

## Part 4: what I went deep on, and why

I chose **Option A: Evidence-Linked Review Experience**.

In accounts payable automation, the greatest operational failure mode is not model inaccuracy—it is **reviewer distrust**. As AP Head Farhan Sheikh reported, reviewers stopped trusting Ledgerline because they could not see where extracted numbers came from. As a result, reviewers resorted to manually re-keying invoices from the original PDF, completely defeating the purpose of automation and creating a severe processing bottleneck.

Option A directly solves this adoption crisis:
- **Clickable Extracted Fields:** Clicking any extracted field immediately highlights its exact character span in the original document viewer and auto-scrolls the highlight into view.
- **Review Reason Cards:** Each review reason card features an active navigation button that jumps directly to the conflicting evidence (e.g. invalid GSTIN or mismatched line item).
- **Unicode Coordinate Integrity:** Implemented `pyCodePointToJsUtf16Offset` to resolve the disparity between Python's Unicode code points and JavaScript's UTF-16 code units, ensuring pixel-perfect highlight slicing even on documents with emojis or non-BMP symbols.
- **Privacy-Preserving Rendering:** The document viewer and field panels display masked bank accounts while preserving character offsets, guaranteeing zero data leaks in the DOM.

By transforming review from a 3-minute manual cross-referencing ordeal into a 10-second visual verification, Option A restores team trust and unlocks the speed necessary to maintain Meridian's 45-day MSME payment cycle.

---

## Replies to stakeholders

### Anita Rao, CFO
We have enabled automated straight-through processing for all clean invoices under ₹10,000 from established vendors, capturing 40% of our volume and saving ~₹1,00,000 monthly in manual touch costs. However, we must not auto-correct garbled GSTINs. Under Section 16 of the CGST Act, Input Tax Credit is strictly disallowed if the invoice GSTIN does not match government tax portals, turning tax credits into an unrecoverable corporate loss. Instead, Ledgerline instantly validates the ISO/IEC 7064 Mod 36, 36 checksum and PAN syntax; invalid GSTINs route immediately to a 10-second human review with highlighted evidence. This protects both AP velocity and statutory tax credits ahead of the board presentation.

### Farhan Sheikh, Head of AP
We have resolved all three of your concerns. First, our new evidence-linked reviewer UI highlights the exact source text in the document whenever a reviewer clicks any field or review reason, eliminating manual re-keying and restoring team trust. Second, bank account and IFSC changes are now caught deterministically in code against our approved vendor master, preventing the ₹3.8 lakh diversion fraud you experienced. Third, we added atomic database idempotency and strict user segregation to the approval API; double-clicking "Approve" can never create duplicate payment entries in Treasury, and high-value invoices strictly require two distinct approvers. Reviewers can now process invoices with full confidence.

### Deepak Menon, CTO
The dashboard was wrong due to a pricing calculation bug in `pricing.py`, where token rates were entered as per-thousand instead of per-million and divided by 1,000,000 twice (a 1,000x undercount). This is now fixed. Under our current architecture using Claude 3.5 Sonnet with prompt caching, average LLM cost is ~₹1.35 per document and p95 latency is ~5.2 seconds, comfortably meeting your ₹1.50 and 10-second targets with zero new infrastructure. Fine-tuning is premature: it incurs high upfront training costs, requires continuous retraining as invoice layouts shift, and cannot handle policy rule updates as flexibly as prompt-based extraction paired with our deterministic Python validation engine.

### Priya Natarajan, Data Protection Officer
In the initial prototype, full bank account numbers were inadvertently included in external LLM prompts and API responses. We have completely overhauled data privacy to ensure compliance with Section 5 and the DPDPA. Full bank account numbers are now masked (e.g. `**********5678`) across all LLM prompt payloads, vendor master CSV dumps, and reviewer API endpoints. Full account numbers are stored encrypted and isolated strictly within Meridian's local SQLite database for deterministic validation and payment processing, and never leave our perimeter. In addition, prompt extraction sanitizes sensitive patterns, ensuring no raw financial identifiers are ever transmitted to third-party providers.

### Sunil Joshi, Finance Controller
We have designed Ledgerline to protect the 45-day MSME window. Straight-through processing for verified repeat vendors under ₹10,000 reduces manual AP queue volume by 40%, eliminating the processing bottleneck. Deterministic validation routes only genuine defects (duplicates, tax mismatches, altered bank details, or new vendor probation) to human review, ensuring clean MSME invoices are cleared rapidly. Regarding bank change call-backs: we agree phone numbers are missing from `vendors.csv`. For now, Ledgerline flags altered bank details as `bank_details_changed` for AP supervisor verification without hard-blocking clean vendors. We propose adding verified vendor phone numbers during the next vendor master audit.

### Ravi Kulkarni, contractor
We kept your prompt-based extraction intact for what the LLM does best: reading unstructured invoices, resolving diverse vendor layouts, and extracting tabular fields without fragile regexes. However, we decoupled boolean statutory policy from prompt generation. Relying on the LLM to decide approval routing led to silent duplicate leaks and missed bank changes. Statutory checks (GSTIN checksums, duplicate detection against paid history, and mathematical totals) are now executed deterministically in Python code. Finance can still update business thresholds via environment variables and configuration files without code redeployment, giving Meridian the layout flexibility of LLMs with the zero-hallucination guarantees required by accounting audits.

---

## Policy changes I propose

### Proposal 1: Mandate Line-Item Arithmetic Verification (`disp_05`)
- **What changes:** Amend AP Policy §3 to require that for every line item, `quantity * unit_price == amount` within a ₹1.00 rounding tolerance. Flag discrepancies as `line_item_math_error`.
- **Why:** In `disp_05`, a vendor overbilled Meridian by ₹550 on line 1, but passed baseline policy because subtotal and taxes matched line item amounts.
- **Cost / Risk:** Minimal risk. Prevents silent vendor overbilling across thousands of monthly line items.

### Proposal 2: Forbid Post-Dated Invoices and Benchmark Probation to Receipt Date (`disp_06`)
- **What changes:** Amend AP Policy §3: an invoice whose printed `invoice_date` is later than its physical or digital receipt date must be flagged as `future_dated_invoice`. Calculate the 90-day new vendor probation period using receipt date, not vendor-printed dates.
- **Why:** In `disp_06`, a new vendor post-dated an invoice by two months, artificially bypassing the mandatory 90-day probation check.
- **Cost / Risk:** Ensures compliance with Indian accounting period matching standards and prevents new vendor control bypasses.

### Proposal 3: Supplementary Billing & Debit Note Protocol (`disp_02`)
- **What changes:** Update AP Policy §2 to formally forbid vendors from re-using identical invoice numbers for revised billings. Require price adjustments to be billed via separate Debit/Credit Notes or uniquely suffixed invoice numbers (e.g. `SIS/26-27/0018-R1`).
- **Why:** `disp_02` attempted to bill an incremental price revision under an invoice number already paid, triggering duplicate rejection.
- **Cost / Risk:** Requires vendor communication, but eliminates duplicate payment ambiguity and satisfies GST audit trails.

### Proposal 4: Domestic Foreign-Currency Exchange Rate Policy (`disp_03`)
- **What changes:** Clarify AP Policy §3 for domestic vendors billing in foreign currencies: require the conversion exchange rate to be stated on the invoice and validate that it does not diverge by >1% from the RBI reference rate on the invoice date.
- **Why:** Domestic resin contracts linked to foreign indices (e.g. `disp_03`) currently trigger blanket foreign currency flags even when domestic GST is correctly calculated.
- **Cost / Risk:** Clarifies approval workflows for commodity-linked domestic contracts without creating currency speculation risk.
