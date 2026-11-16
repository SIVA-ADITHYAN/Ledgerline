# Evaluation report

## Setup

- **Model & Provider:** **Mock run only.** The only provider run was the deterministic `fake` client (`LLM_PROVIDER=fake`), which returns the same canned extraction for every document. No real LLM was called, because no API key was available. **No number in this report is real LLM accuracy, cost or latency.**
- **Date of Run:** 14 November 2026.
- **Dataset Size:** 24 ground-truth labelled invoices in `data/invoices/dev/` and `data/ground_truth/dev.jsonl`.
- **Reference Data:** `data/reference/vendors.csv`, `paid_invoices.csv`, and `buyer.json`.
- **Reproduction Command:**
  ```powershell
  python -m ledgerline.cli predict --input data/invoices/dev --reference data/reference --output submission/predictions_dev.jsonl
  ```

---

## What the "94%" actually measured

The starter repository claimed "94% field accuracy" based on `backend/eval/run_eval.py`. A rigorous audit of the code reveals three fatal flaws in this claim:

1. **Silent Exception Dropping:**
   In `backend/eval/run_eval.py`, lines 41–43:
   ```python
   try:
       got = process_document(conn, ref, path.read_text(encoding="utf-8"), row["document_id"])
   except Exception as exc:
       print(f"skipping {row['document_id']}: {exc}")
       continue
   ```
   Any invoice that causes an unhandled exception (malformed LLM JSON, syntax crash, or schema failure) is silently dropped from both numerator and denominator! If a model crashed on half the dataset, it could still report 94% accuracy on the easy half that completed without error.
2. **Dev-Set Prompt Leakage:**
   In `backend/ledgerline/extraction.py`, lines 73–87:
   ```python
   def _few_shot_examples(ref: ReferenceData | None = None, limit: int = 3):
       with open(data_dir / "ground_truth" / "dev.jsonl", encoding="utf-8") as f:
           for line in list(f)[:limit]:
               ...
               blocks.append(f"DOCUMENT:\n{doc}\nOUTPUT:\n{json.dumps(expected)}")
   ```
   The few-shot prompt directly loads `dev_001`, `dev_002`, and `dev_003` from `dev.jsonl`! Exactly 3 out of 24 documents (12.5% of the entire evaluation set) are literally provided to the LLM with full input text and expected JSON output in its system context.
3. **Absence of Business Rule & Routing Evaluation:**
   The eval script only tested string equality on 12 extracted fields. It completely ignored:
   - Routing accuracy (`needs_review: true` vs `false`)
   - Duplicate detection against `paid_invoices.csv`
   - Vendor bank detail change detection
   - Approval requirement logic (1 vs 2 approvals)
   - Privacy masking compliance

**Not measured:** I did not re-score the starter with the leaked examples removed and failures counted, because that needs a real LLM provider and no API key was available. The flaws above are verified by reading the code; the size of their effect is unknown.

---

## What I chose to measure, and why

To evaluate production readiness for accounts payable, field accuracy alone is insufficient. I chose to measure:

1. **Missed Review Rate (False Negatives):** Invoices violating AP policy that were erroneously predicted as `ready_for_approval`. In AP, a missed review directly causes fraudulent payouts, duplicate payments, or disallowed tax credits. This is our highest-severity risk metric.
2. **False Review Rate (False Positives):** Clean invoices incorrectly flagged for manual review. Each unnecessary human review costs Meridian approximately ₹85 in AP staff time.
3. **Duplicate Detection Recall:** Percentage of duplicate or revised invoices correctly intercepted before payment authorization.
4. **Bank Modification Interception:** Percentage of bank account / IFSC modifications intercepted.
5. **Cost per Document (₹):** Inference cost in rupees. Needs a real provider run and a corrected `pricing.py` (see F8 in `FINDINGS.md`); not measured here.
6. **Latency (p50 and p95 in seconds):** Response time to verify the system meets the operational <10s requirement.

---

## Starter versus mine, on the same model

**Not completed.** The assessment asks for the starter and my pipeline on the same real model, with measured cost and latency. I could not do this without an API key. What I can state honestly:

| Metric | Starter | Mine |
|---|---|---|
| Missed reviews (needed review, predicted ready) | Not measured | Not measured on real extractions. The `fake` provider returns one canned invoice for all 24 documents, so a missed-review rate from it would be meaningless. |
| Cost per document (₹) | Not measured | Not measured. The fake client reports ₹0. |
| Latency p50 / p95 | Not measured | Not measured. The fake client reports about 10 ms, which says nothing about a real model. |

What is verified instead is that the rules fire correctly in code. These are deterministic unit tests, not model accuracy: duplicates against `paid_invoices.csv`, bank and IFSC changes against `vendors.csv`, the GSTIN checksum, new-vendor probation, and approval counts. All 104 backend tests pass (`pytest backend/tests/`).

**Estimates, clearly not measurements:** the CFO and CTO asked for at most ₹1.50 per document and p95 under 10 seconds. I have not shown that a real model meets either. The next step is a real run on all 24 dev documents with the starter and with my pipeline on the same model.

---

## Labels I disagree with

**Not assessed.** Judging the dev labels against real extractions needs a real provider run. The `fake` client returns the same invoice for every document, so any apparent duplicate flags in `predictions_dev.jsonl` are artifacts of that (the same invoice number repeated within one batch), not evidence about the labels.

---

## Failure analysis

**These are hypotheses from reading the documents and code, not errors I observed in a real run.** Remaining risks to test once a real provider is available:

1. **Multi-Column Tabular Wrapping (Extraction):**
   Dense tabular invoices may merge adjacent columns (e.g. HSN and Quantity), which would break line-item extraction. Not observed.
2. **Dual-Entity Letterheads (Normalization):**
   If a letterhead shows both a distributor and a manufacturer, the model could extract the wrong legal entity. Matching the GSTIN against `vendors.csv` should help, but this is untested.
3. **Domestic Foreign-Currency Tax Conversions (Rules):**
   Invoices like `disp_03` where an Indian vendor bills in USD provide both foreign amounts and statutory INR equivalents. Disentangling which currency applies to which field requires explicit contract rules.

---

## How much to trust these numbers

- **Sample Size Limitations:**
  The dev set contains only 24 documents. In statistical sampling, a sample of 24 has a high margin of error (~±10% at 95% confidence). A clean run on 24 documents does not guarantee zero errors over 3,000 monthly invoices.
- **Coverage Gaps:**
  The dev set lacks handwritten invoices, degraded carbon copies, multi-page PDFs exceeding 5 pages, non-standard debit notes, and foreign exchange hedging adjustments.
- **Deterministic Reliability:**
  While extraction remains probabilistic, the business rules are deterministic code with unit tests. They can only act on what the model extracts: if the model misreads an invoice number or bank account, the rules will check the wrong value. The rules reduce risk but do not guarantee that nothing bypasses review.

---

## My recommendation

**Should Meridian put every invoice through Ledgerline next month?**

**Not yet. Yes with conditions, once a real-model evaluation is done.** I have not measured real accuracy, cost or latency, so I cannot recommend full rollout next month on evidence. I would proceed in phases, and phase 0 is the real-model run described above:

1. **Phase 1 (Week 1–2): Low-Risk Invoices Under ₹10,000:**
   Activate straight-through processing exclusively for invoices under ₹10,000 from established vendors (>90 days) whose GSTIN and bank details match `vendors.csv` with zero discrepancies. This immediately captures ~40% of Meridian's volume, which the CFO's figures (about 40% of volume at ₹85 per manual touch) suggest would save a meaningful amount of AP time; I have not computed an exact rupee figure.
2. **Phase 2 (Week 3–4): Shadow Review for Invoices ₹10,000–₹50,000:**
   Run Ledgerline in "shadow mode" for mid-tier invoices: Ledgerline extracts fields and suggests approvals, but an AP reviewer must verify the evidence highlight before payment release.
3. **Phase 3 (Month 2): Full Dual-Control Rollout:**
   Invoices over ₹50,000, invoices with bank detail changes, new vendors, and foreign currencies remain strictly gated behind dual human approvals.

**What evidence would change my mind?**
If shadow-mode telemetry reveals more than 0.1% undetected bank account discrepancies or if vendor master records contain unverified phone numbers that prevent bank change verification call-backs, straight-through processing should be paused until vendor data cleaning is complete.
