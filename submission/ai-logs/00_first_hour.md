# Session Log: 00_first_hour

**Tool:** Antigravity (Assistant)
**Candidate / Engineer:** Siva Adithyan
**Task:** Part 0 — Initial Investigation & FIRST_HOUR.md Preparation

---

## Overview

The engineer and assistant conducted a structured, question-by-question interview to establish the candidate's authentic understanding of the Ledgerline repository, business risks, inspection strategy, and assessment time management without modifying application code.

---

## Structured Interview Transcript

### Q1: What do you understand about the main problem Ledgerline is trying to solve?

**Candidate Response:**
> "My understanding is that Ledgerline helps a company's accounts payable team process invoices received from multiple vendors. One important risk is duplicate invoices: the same vendor may submit an invoice more than once, either accidentally or intentionally. If the system fails to identify these duplicates, the company could make duplicate payments and suffer financial losses. Therefore, invoice verification, duplicate detection, vendor validation, and proper approval checks are important to make the payment process reliable and secure."

---

### Q2: What do you think is the most dangerous problem in an accounts payable system?

**Candidate Response:**
> "I think the most dangerous problem in an accounts payable system is a fraudulent payment caused by unauthorized changes to vendor bank details. For example, if someone changes a legitimate vendor's bank account information and the system fails to detect or properly verify that change, the company could transfer a large amount of money to a fraudulent account. Recovering that money may be very difficult. Duplicate invoices are another major risk because they can result in paying the same bill twice. Therefore, validating vendor bank details, detecting duplicate invoices, and enforcing proper human approvals are critical to protecting the company's money."

---

### Q3: Why could incorrect invoice extraction or duplicate detection cause financial damage?

**Candidate Response:**
> "Incorrect invoice extraction and duplicate detection can directly cause financial losses because the accounts payable team depends on this information to verify invoices and make payment decisions. For example, if an LLM adds an extra zero to an invoice amount, the company may overpay a vendor. If it extracts the wrong tax rate, the tax calculation and compliance checks may become incorrect. Similarly, if duplicate detection misses an invoice that has already been paid, the company could pay it again. OCR errors can also cause important details such as invoice numbers, dates and amounts to be misread. Even a small extraction error can become a serious financial problem if it is not detected during validation and human review."

---

### Q4: What would you inspect first in this repository?

**Candidate Response:**
> "I would first read the README, AP_POLICY.md and project structure to understand how the system is expected to work. Then I would trace the complete invoice-processing flow, starting from OCR and LLM-based data extraction, followed by validation, duplicate detection and the final payment approval process.
> 
> I would pay particular attention to the validation logic because incorrect extracted values or missed duplicates could lead to financial losses. I would also inspect the database schema and payment-related endpoints to understand how invoices, vendor bank details and approvals are stored and updated. Next, I would review the LLM prompts and error-handling mechanisms to identify possible extraction failures or unreliable outputs.
> 
> Finally, I would run the existing tests and examine whether they cover important scenarios such as duplicate invoices, bank account changes, invalid GSTINs, incorrect totals and repeated approval attempts. I would prioritize the highest-risk issues and add regression tests before making fixes."

---

### Q5: What questions would you ask Meridian before trusting its AI decisions?

**Candidate Response:**
> "Before trusting Ledgerline's AI decisions, I would ask Meridian a few important questions:
> 
> **To Anita Rao (CFO):**
> 1. What is the company's tolerance for incorrect payments compared with unnecessary manual reviews?
> 2. Have there been any incidents of duplicate payments, invoice fraud or unauthorized bank transfers?
> 3. What is the maximum financial risk the company is willing to accept from an automated invoice-processing system?
> 
> **To Farhan Sheikh (AP Lead):**
> 1. How does the AP team currently verify new vendors and changes to existing bank details?
> 2. What happens when the system identifies a duplicate invoice or an invoice with suspicious information?
> 3. Who is authorized to approve payments, and how do you prevent the same person from approving twice?
> 4. Which types of invoices create the most manual work or errors for your team?
> 
> **To Priya Natarajan (Data Protection Officer):**
> 1. What invoice and vendor information is shared with external LLM or OCR providers?
> 2. Do the AI providers have agreements that prevent our financial data from being stored or used for model training?
> 3. Where is the data processed and stored, and how long is it retained?
> 4. What security measures protect sensitive information such as GSTINs and bank account details?
> 
> I would also ask how Meridian currently measures invoice-processing accuracy and what evidence is required before an invoice can be marked ready for payment. I believe understanding these operational, financial and privacy requirements is essential before trusting AI-generated decisions."

---

### Q6: How would you prioritize the limited assessment time?

**Candidate Response:**
> "I would divide the six focused hours based on business risk and the assessment requirements:
> 
> - **First 30 minutes – Understand the system:** Read the README, AP_POLICY.md, project structure and existing handover notes. Identify the main risks and understand the invoice-processing workflow.
> - **Next 2 hours – Fix critical issues:** Prioritize duplicate detection, vendor bank-detail validation and approval workflow safety because these issues can lead to incorrect or fraudulent payments. Add focused regression tests for each fix.
> - **Next 1 hour – Evidence-based review:** Implement the most important parts of the review experience, including showing the extracted information, validation failures and supporting invoice evidence so that reviewers can make informed decisions.
> - **Next 1 hour – Evaluation:** Run the existing evaluation, investigate extraction and validation failures, compare results and document accuracy, missed reviews, cost and latency.
> - **Next 1 hour – Assessment documentation:** Complete the required findings, disputed invoice rulings, technical decisions and stakeholder responses. Explain what was fixed, what remains unresolved and why.
> - **Final 30 minutes – Verification and submission:** Run the tests and required commands, check the generated predictions, verify the Git repository and ensure all required files are committed.
> 
> I would intentionally defer vendor risk scoring, complex multi-model systems and major redesigns because they are not the immediate priorities. My priority would be to make the existing system safer, demonstrate improvements with evidence and clearly document any remaining risks. I would respect the six-hour target and never exceed the eight-hour hard limit."

---

## Resulting Artifact

The synthesized, candidate-authored document was formatted to strictly meet the `<300 words` requirement and saved directly to:
- `submission/FIRST_HOUR.md`
- `Siva Docs/FIRST_HOUR.md`

