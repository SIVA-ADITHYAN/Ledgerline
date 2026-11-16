# How I worked with AI

## Tools and models

- **Primary Assistant:** Antigravity AI Agent (powered by Google DeepMind Gemini models).
- **Subagents & Tooling:**
  - Automated Browser Subagent (Chrome DevTools Protocol via Chrome DevTools MCP) for live end-to-end frontend review verification and visual DOM auditing.
  - Native filesystem, file editing, and test execution tools.

## How I set up context

- **Grounding in Reference Docs:** Initialized each phase by explicitly referencing `docs/AP_POLICY.md`, `docs/PRODUCT_BRIEF.md`, `docs/OUTPUT_CONTRACT.md`, and `docs/STAKEHOLDER_THREAD.md`.
- **Preserving Business Invariants:** Provided explicit constraints:
  - Exact offset integrity: `raw_text[start:end] == evidence["text"]` must never be violated.
  - Privacy compliance: Vendor bank account numbers must never leave Meridian's systems unmasked.
  - Test-driven verification: Every fix must be verified by automated unit tests before proceeding.

## My workflow

1. **Investigate & Plan:** Use read-only inspection tools to locate root causes and understand existing architecture before proposing changes.
2. **Deterministic Code Fixes:** Restrict LLM to unstructured extraction while placing all statutory business rules, duplicate detection, and checksums in deterministic Python code.
3. **Automated Verification:** Run backend unit tests (`pytest`), frontend tests (`node --test`), production builds (`vite build`), and headless browser tests (`Chrome MCP`) after every architectural increment.
4. **Independent Review:** Actively cross-check AI proposals against policy and contract rules rather than accepting generated code blindly.

## Where the AI was wrong

1. **Bank Account Privacy vs Offset Integrity (Step 2):**
   - *What happened:* The AI initially attempted to implement bank account masking by replacing `evidence["text"]` with masked asterisks while keeping original offsets. This violated the critical invariant `raw_text[start:end] == evidence["text"]`. Furthermore, when bank accounts appeared in the raw invoice text but were missed during extraction, the initial code leaked the full account numbers through `GET /api/invoices/{id}`.
   - *How noticed:* During API test auditing, observing that substring slicing failed and unextracted account digits remained visible in the raw document viewer.
   - *What I did:* Enforced a strict separation between internal exact evidence (`evidence.py`) and API presentation (`masked_text`), and introduced autonomous regex discovery (`discover_bank_accounts_in_text`) in `backend/ledgerline/api.py` that masks raw text occurrences while preserving 1-to-1 character lengths.
   - *Transcript reference:* Documented in Part 4A Step 2 review and `submission/ai-logs/04_part4a_evidence_review.md`.

2. **Terminal Invoice State Controls in the Frontend:**
   - *What happened:* The AI initially left the "Approve" and "Reject" action buttons and the audit-note textarea fully enabled in the UI for invoices with status `approved` or `rejected`, assuming backend HTTP 400 rejection was sufficient.
   - *How noticed:* Live browser testing via Chrome DevTools MCP showed active, clickable buttons on already approved invoices.
   - *What I did:* Added explicit frontend terminal status guards (`isTerminal`) in `frontend/src/App.tsx`, replacing action buttons with a read-only terminal status badge and disabling user input.
   - *Transcript reference:* Logged in `submission/ai-logs/04_part4a_evidence_review.md` and verified in `frontend/test/app_logic.test.mjs`.

3. **Unicode Offset Divergence (I went along with it at first):**
   - *What happened:* The AI initially suggested that Python character offsets could be directly applied in the browser with `rawText.slice(start, end)`. I initially accepted this assumption because ASCII and basic multilingual characters aligned identically.
   - *How noticed:* Deep dive into Unicode specifications revealed that Python's `str` indexes by Unicode code points, whereas JavaScript's string methods index by 16-bit UTF-16 code units. Non-BMP characters (such as emojis or complex astral symbols) count as 1 code point in Python but 2 code units in JavaScript, causing text highlights to drift.
   - *What I did:* Implemented `pyCodePointToJsUtf16Offset` in `frontend/src/utils/unicode.ts` and wrote dedicated regression tests covering astral plane characters in both TypeScript and Python.
   - *Transcript reference:* Documented in `submission/ai-logs/04_part4a_evidence_review.md` and `frontend/test/unicode.test.mjs`.

## What I deliberately didn't delegate, and why

- **Policy Rulings & Final Risk Assessment:** Analyzing disputed invoices (`disp_01` to `disp_06`) and determining corporate risk tradeoffs requires human engineering and business judgement. AI tools can summarize text, but cannot own financial liability or statutory risk evaluations.
- **Architectural Boundary Enforcement:** The strategic decision to strictly decouple probabilistic LLM extraction from deterministic Python business rules was made deliberately to eliminate AI hallucination from accounting decisions.
- **Privacy & Security Boundaries:** Verification that vendor bank accounts are completely masked in API responses, prompts, and DOM trees was checked with manual curl and DOM inspections.

## Time

- **Total Time Spent:** Approximately 7.5 hours.
- **Breakdown:**
  - Part 0 (First Hour): ~1 hour (codebase exploration, risk identification, `FIRST_HOUR.md`).
  - Part 4A (Step 1–3 Evidence Reviewer & Hardening): ~3.5 hours (offset mapping, schema migration, frontend UI, browser DevTools MCP testing, bank privacy sanitization).
  - Bug Fixes & Deterministic Rules: ~1.5 hours (duplicate detection, approval idempotency, GSTIN checksum, bank validation).
  - Evaluation & Deliverables: ~1.5 hours (unmasking 94% claim, disputed rulings, findings, stakeholder replies, reflection).

## Transcripts

- `submission/ai-logs/00_first_hour.md`: Part 0 investigation and structured interview log.
- `submission/ai-logs/04_part4a_evidence_review.md`: Part 4A Step 3 reviewer interface implementation, browser verification, and targeted bank privacy / terminal action fixes.
