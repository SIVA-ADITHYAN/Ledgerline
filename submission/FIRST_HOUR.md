# My first hour

_Under about 300 words, in your own words. Commit it on its own, before any commit that touches `backend/` or `frontend/`, and don't edit it afterwards. Save the AI sessions from this stretch in `ai-logs/` with a name starting `00_first_hour`. Being wrong here is fine._

## What I think is going on

Ledgerline is designed to help an accounts payable team process vendor invoices using automated extraction, validation and review decisions. However, errors in extracted information, weak duplicate detection and insufficient approval safeguards could allow incorrect invoices to pass through the process. These weaknesses could expose the company to duplicate payments, fraudulent transfers and incorrect financial decisions.

## The risks I suspect, most serious first

1. **Fraudulent bank-detail changes:** Changes to vendor bank accounts may not be properly detected or independently verified, potentially redirecting payments to fraudulent accounts.
2. **Duplicate invoice payments:** Weak duplicate matching could fail to identify an invoice that has already been paid or submitted.
3. **Extraction and tax errors:** LLM hallucinations or OCR mistakes could misread invoice amounts, dates or tax information and lead to incorrect payment decisions.
4. **Unsafe approval logic:** Repeated approvals by the same person or missing approval-state checks could weaken payment controls.
5. **Data privacy exposure:** Sensitive vendor and financial information may be exposed if external AI providers receive more data than necessary.

## My plan for the time I have

Within approximately six focused hours, with an eight-hour hard limit:

- **Hours 1–2:** Investigate and fix high-risk duplicate detection, bank-detail validation and approval logic, with regression tests.
- **Hour 3:** Address evidence-based invoice review so reviewers can verify extracted information against source evidence.
- **Hour 4:** Evaluate extraction and validation quality, missed reviews, cost and latency.
- **Hour 5:** Complete findings, disputed invoice rulings, technical decisions and stakeholder responses.
- **Final hour:** Complete remaining assessment deliverables, run tests and prediction commands, verify documentation and check the final Git state.

I would defer vendor risk scoring, complex multi-model systems and major redesigns because they are not the immediate priorities.

## What I would ask Meridian

- **CFO:** What is the acceptable trade-off between missed fraud and unnecessary manual reviews? Have unauthorized payments occurred?
- **AP Lead:** How are bank-detail changes independently verified, and how are duplicate invoices handled today?
- **DPO:** What agreements govern external AI providers' use, retention and protection of vendor financial data?
