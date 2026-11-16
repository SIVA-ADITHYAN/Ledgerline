# Reflection

## Three things I didn't know when I started, and how I learned each one

1. **The ISO/IEC 7064 Mod 36, 36 Indian GSTIN Checksum Algorithm:**
   Prior to this project, I assumed GSTIN validation simply consisted of checking a 15-character alphanumeric regex structure matching a 2-digit state code and a 10-character PAN. While investigating `disp_01.txt` (where Sitara's GSTIN had a subtle character substitution), I researched Indian tax specifications and learned that character 15 is an official check digit computed using the ISO/IEC 7064 Mod 36, 36 standard with alternating weights. Implementing the mathematical algorithm in Python and writing unit tests against real GSTINs taught me how statutory tax portals deterministically reject typos without needing a network database query.
2. **Unicode Code Point vs UTF-16 Code Unit Offset Drift Between Python and JavaScript:**
   I had never encountered the subtle offset drift that occurs when Python backend offsets meet JavaScript frontend string slicing. Python counts strings by Unicode code points (where every character, including astral plane emojis and special symbols, is a single index). In contrast, JavaScript's `String.prototype.slice()` and DOM APIs index by UTF-16 code units (where characters outside the Basic Multilingual Plane require 2 surrogate code units). Implementing `pyCodePointToJsUtf16Offset` taught me that full-stack text highlighting architectures must explicitly normalize character coordinates across the language boundary.
3. **Internal Line-Item Arithmetic Vulnerability in AP Invoices:**
   I originally assumed that verifying that the sum of line item totals equals the invoice subtotal and tax amounts was sufficient to validate invoice calculations. Analyzing `disp_05.txt` was an eye-opener: an invoice can have a subtotal, CGST, SGST, and grand total that are 100% mathematically consistent with the printed line totals, yet still overcharge the buyer because an individual line item's multiplication (`quantity * unit_price != amount`) is wrong. This taught me that automated accounting systems must validate math at the lowest atomic unit of data rather than trusting aggregate sums.

---

## The decision I'm least sure of

**What it was:**
Applying an autonomous regex-based scanner (`discover_bank_accounts_in_text`) to mask 9-to-18 digit numeric sequences in raw document text before returning it through the reviewer API, rather than only masking the specific coordinates identified during extraction.

**Why I made it:**
I chose a strict defense-in-depth approach. Under Section 5 of Meridian's AP Policy and the DPDPA, complete vendor bank accounts must never leak through API responses. If an invoice contained an unextracted bank account (or if the LLM hallucinated or missed the field), a coordinate-only approach would have leaked the full account in the raw text viewer. Regex masking guaranteed that no unmasked account digits could reach the browser DOM.

**What would show me it was wrong:**
If production telemetry reveals that long non-banking numeric sequences (such as 10-to-14 digit courier tracking numbers, machinery part serial numbers, or internal drawing numbers) are accidentally masked with asterisks in the document viewer. If reviewers report that legitimate non-banking identifiers are being obscured, this decision would need to be replaced with a context-aware entity recognizer that only masks numbers preceded by banking keywords ("A/c", "Account", "IBAN", "NEFT").

---

## With one more week

If I had one more week to work on Ledgerline, here is what I would build, in order of priority:

1. **PDF Canvas Bounding-Box Coordinate Viewer (Frontend & OCR):**
   Replace the plain-text document viewer with a native PDF.js canvas viewer that overlays visual bounding boxes directly on top of the original vendor PDF. While plain-text highlighting works well for emails, tabular manufacturing invoices are best verified against their native visual layouts.
2. **Automated Vendor Master Phone Call-Back Verification Workflow:**
   Collaborate with Sunil Joshi's team to add verified telephone numbers to `vendors.csv` and integrate an automated telephony service (e.g. Twilio / Exotel) that triggers a secure voice prompt or SMS OTP to the vendor's authorized finance contact whenever a bank account change is detected.
3. **Live Reserve Bank of India (RBI) Foreign Exchange Reference Rate Integration:**
   Implement an automated integration with the RBI reference rate feed to dynamically validate exchange rates on multi-currency domestic contracts (such as resin contracts in `disp_03`), eliminating manual rate verification for commodity purchases.
4. **Self-Service Vendor Pre-Validation Submission Portal:**
   Build a vendor-facing upload portal where suppliers can test their invoices against Meridian's validation rules before submitting them to AP. This shifts error detection upstream, catching GSTIN typos, missing PO numbers, and duplicate billings before they ever enter Meridian's queue.

**Why this order:**
Visual bounding boxes provide immediate daily workflow acceleration for AP reviewers; the call-back integration eliminates Meridian's remaining physical fraud vulnerability; the RBI integration automates commodity contracts; and the vendor portal permanently reduces incoming queue defect rates at the source.
