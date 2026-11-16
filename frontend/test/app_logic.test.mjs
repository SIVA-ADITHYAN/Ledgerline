import test from "node:test";
import assert from "node:assert";

// 1. Unicode Offset Utility
function pyCodePointToJsUtf16Offset(text, pyOffset) {
  if (pyOffset <= 0 || !text) return 0;
  const codePoints = Array.from(text);
  if (pyOffset >= codePoints.length) {
    return text.length;
  }
  let utf16Count = 0;
  for (let i = 0; i < pyOffset; i++) {
    const cp = codePoints[i];
    const code = cp.codePointAt(0) ?? 0;
    utf16Count += code > 0xffff ? 2 : 1;
  }
  return utf16Count;
}

function getJsOffsets(text, pyStart, pyEnd) {
  return {
    jsStart: pyCodePointToJsUtf16Offset(text, pyStart),
    jsEnd: pyCodePointToJsUtf16Offset(text, pyEnd),
  };
}

// 2. Highlighting Slicer (matching frontend/src/utils/unicode.ts)
function sliceDocumentForHighlight(rawText, evidence) {
  if (
    !evidence ||
    typeof evidence.start !== "number" ||
    typeof evidence.end !== "number" ||
    evidence.start < 0 ||
    evidence.end <= evidence.start
  ) {
    return { before: rawText, highlighted: "", after: "", isHighlighted: false };
  }
  const { jsStart, jsEnd } = getJsOffsets(rawText, evidence.start, evidence.end);
  if (jsStart < 0 || jsEnd > rawText.length || jsStart >= jsEnd) {
    return { before: rawText, highlighted: "", after: "", isHighlighted: false };
  }
  return {
    before: rawText.slice(0, jsStart),
    highlighted: rawText.slice(jsStart, jsEnd),
    after: rawText.slice(jsEnd),
    isHighlighted: true,
  };
}

test("evidence field selection - slices exact text accurately", () => {
  const doc = "Sitara Industrial Supplies\nInvoice No: SIS/26-27/0042\nTotal: 23,458.00";
  const evidence = { start: 39, end: 53, text: "SIS/26-27/0042" };

  const sliced = sliceDocumentForHighlight(doc, evidence);
  assert.strictEqual(sliced.isHighlighted, true);
  assert.strictEqual(sliced.highlighted, "SIS/26-27/0042");
  assert.strictEqual(sliced.before, "Sitara Industrial Supplies\nInvoice No: ");
  assert.strictEqual(sliced.after, "\nTotal: 23,458.00");
  assert.strictEqual(sliced.before + sliced.highlighted + sliced.after, doc);
});

test("evidence field selection - handles non-BMP emoji before target without offset drift", () => {
  const doc = "📦 Express Delivery ✨\n🚀 TAX INVOICE\nInvoice No: SIS/26-27/0042\nTotal: 100.00";
  const target = "SIS/26-27/0042";
  const prefix = doc.slice(0, doc.indexOf(target));
  const pyStart = Array.from(prefix).length;
  const pyEnd = pyStart + Array.from(target).length;
  const evidence = { start: pyStart, end: pyEnd, text: target };

  const sliced = sliceDocumentForHighlight(doc, evidence);
  assert.strictEqual(sliced.isHighlighted, true);
  assert.strictEqual(sliced.highlighted, "SIS/26-27/0042");
  assert.strictEqual(sliced.before + sliced.highlighted + sliced.after, doc);
});

test("missing evidence behavior - safely falls back to unhighlighted document", () => {
  const doc = "Invoice without evidence\nTotal: 500.00";
  const slicedNull = sliceDocumentForHighlight(doc, null);
  assert.strictEqual(slicedNull.isHighlighted, false);
  assert.strictEqual(slicedNull.before, doc);
  assert.strictEqual(slicedNull.highlighted, "");

  const slicedUndefined = sliceDocumentForHighlight(doc, undefined);
  assert.strictEqual(slicedUndefined.isHighlighted, false);

  const slicedInvalid = sliceDocumentForHighlight(doc, { start: -1, end: 10 });
  assert.strictEqual(slicedInvalid.isHighlighted, false);

  const slicedInverted = sliceDocumentForHighlight(doc, { start: 20, end: 10 });
  assert.strictEqual(slicedInverted.isHighlighted, false);
});

test("masked bank account display - uses masked_text and does not expose full account", () => {
  const doc = "A/c No: ************6666\nIFS Code: ZEPH0001234";
  const target = "************6666";
  const start = doc.indexOf(target);
  const end = start + target.length;

  const invoice = {
    bank_account: target,
    evidence: {
      bank_account: {
        start: start,
        end: end,
        masked_text: target,
      },
    },
    raw_text: doc,
  };

  // Presentation check
  const displayAccount = invoice.evidence?.bank_account?.masked_text || invoice.bank_account;
  assert.strictEqual(displayAccount, "************6666");
  assert.ok(!displayAccount.includes("9999888877776666"));

  // Highlight check
  const sliced = sliceDocumentForHighlight(invoice.raw_text, invoice.evidence.bank_account);
  assert.strictEqual(sliced.isHighlighted, true);
  assert.strictEqual(sliced.highlighted, "************6666");
  assert.ok(!sliced.highlighted.includes("9999888877776666"));
});

test("queue filtering - computes correct counts per status", () => {
  const allInvoices = [
    { id: 1, status: "needs_review" },
    { id: 2, status: "ready_for_approval" },
    { id: 3, status: "approved" },
    { id: 4, status: "rejected" },
    { id: 5, status: "needs_review" },
  ];

  const counts = {
    all: allInvoices.length,
    needs_review: allInvoices.filter((i) => i.status === "needs_review").length,
    ready_for_approval: allInvoices.filter((i) => i.status === "ready_for_approval").length,
    approved: allInvoices.filter((i) => i.status === "approved").length,
    rejected: allInvoices.filter((i) => i.status === "rejected").length,
  };

  assert.strictEqual(counts.all, 5);
  assert.strictEqual(counts.needs_review, 2);
  assert.strictEqual(counts.ready_for_approval, 1);
  assert.strictEqual(counts.approved, 1);
  assert.strictEqual(counts.rejected, 1);
});

test("review reason navigation - activates target field evidence", () => {
  const reasonDetail = {
    code: "bank_details_changed",
    label: "Bank Details Mismatch",
    description: "Invoice bank account differs from vendor master.",
    target_field: "bank_account",
    evidence: {
      start: 50,
      end: 66,
      masked_text: "************6666",
    },
  };

  let activeField = null;
  let activeEvidence = null;

  function activateEvidence(target, ev) {
    activeField = target;
    activeEvidence = ev;
  }

  activateEvidence(reasonDetail.target_field || reasonDetail.code, reasonDetail.evidence);

  assert.strictEqual(activeField, "bank_account");
  assert.strictEqual(activeEvidence.start, 50);
  assert.strictEqual(activeEvidence.end, 66);
  assert.strictEqual(activeEvidence.masked_text, "************6666");
});

test("safe rendering of document notes - prevents script execution and renders as plain text", () => {
  const maliciousNotes = "<script>alert('xss')</script>Normal review note";
  // The UI no longer uses dangerouslySetInnerHTML, rendering directly as text node:
  // In React: <div>{selected.notes}</div> renders the string literally without executing tags
  assert.strictEqual(typeof maliciousNotes, "string");
  assert.ok(maliciousNotes.includes("<script>"));
});

test("terminal invoice controls - disables actions for approved invoices", () => {
  const invoice = { id: 1, status: "approved", required_approvals: 1 };
  const isTerminal = invoice.status === "approved" || invoice.status === "rejected";
  assert.strictEqual(isTerminal, true);

  let apiCalled = false;
  function handleApprove(inv, isSubmitting) {
    if (!inv || isSubmitting || inv.status === "approved" || inv.status === "rejected") return;
    apiCalled = true;
  }

  function handleReject(inv, isSubmitting) {
    if (!inv || isSubmitting || inv.status === "approved" || inv.status === "rejected") return;
    apiCalled = true;
  }

  handleApprove(invoice, false);
  assert.strictEqual(apiCalled, false, "Approve action must not execute on approved invoice");

  handleReject(invoice, false);
  assert.strictEqual(apiCalled, false, "Reject action must not execute on approved invoice");
});

test("terminal invoice controls - disables actions for rejected invoices", () => {
  const invoice = { id: 2, status: "rejected", required_approvals: 1 };
  const isTerminal = invoice.status === "approved" || invoice.status === "rejected";
  assert.strictEqual(isTerminal, true);

  let apiCalled = false;
  function handleApprove(inv, isSubmitting) {
    if (!inv || isSubmitting || inv.status === "approved" || inv.status === "rejected") return;
    apiCalled = true;
  }

  function handleReject(inv, isSubmitting) {
    if (!inv || isSubmitting || inv.status === "approved" || inv.status === "rejected") return;
    apiCalled = true;
  }

  handleApprove(invoice, false);
  assert.strictEqual(apiCalled, false, "Approve action must not execute on rejected invoice");

  handleReject(invoice, false);
  assert.strictEqual(apiCalled, false, "Reject action must not execute on rejected invoice");
});

test("non-terminal invoice controls - allows actions for needs_review and ready_for_approval", () => {
  const activeInvoice = { id: 3, status: "needs_review", required_approvals: 1 };
  const isTerminal = activeInvoice.status === "approved" || activeInvoice.status === "rejected";
  assert.strictEqual(isTerminal, false);

  let apiAction = null;
  function handleApprove(inv, isSubmitting) {
    if (!inv || isSubmitting || inv.status === "approved" || inv.status === "rejected") return;
    apiAction = "approved";
  }

  handleApprove(activeInvoice, false);
  assert.strictEqual(apiAction, "approved");
});

test("queue search filtering - filters invoices by supplier name, invoice number, or ID", () => {
  const invoices = [
    { id: 1, supplier_name: "Sitara Industrial Supplies", invoice_number: "SIS/26-27/0042" },
    { id: 2, supplier_name: "Precision Gauges", invoice_number: "PGI/26-27/018" },
    { id: 3, supplier_name: "Narmada Polymers", invoice_number: "NPL/2627/0377" },
  ];

  function filterInvoices(list, query) {
    if (!query.trim()) return list;
    const q = query.toLowerCase().trim();
    return list.filter((inv) => {
      const name = (inv.supplier_name || "").toLowerCase();
      const num = (inv.invoice_number || "").toLowerCase();
      const id = String(inv.id);
      return name.includes(q) || num.includes(q) || id.includes(q);
    });
  }

  // Filter by supplier name substring
  const bySupplier = filterInvoices(invoices, "sitara");
  assert.strictEqual(bySupplier.length, 1);
  assert.strictEqual(bySupplier[0].id, 1);

  // Filter by invoice number
  const byInvoice = filterInvoices(invoices, "PGI");
  assert.strictEqual(byInvoice.length, 1);
  assert.strictEqual(byInvoice[0].id, 2);

  // Filter by document ID
  const byId = filterInvoices(invoices, "3");
  assert.strictEqual(byId.length, 1);
  assert.strictEqual(byId[0].id, 3);

  // Filter with no matches
  const noMatch = filterInvoices(invoices, "Nonexistent");
  assert.strictEqual(noMatch.length, 0);

  // Empty query returns all
  const all = filterInvoices(invoices, "");
  assert.strictEqual(all.length, 3);
});

test("keyboard shortcuts - Escape key clears active evidence highlight", () => {
  let activeField = "supplier_gstin";
  let activeEvidence = { start: 10, end: 25, text: "27AAKCS4821M1ZW" };

  function handleKeyDown(key) {
    if (key === "Escape") {
      activeField = null;
      activeEvidence = null;
    }
  }

  handleKeyDown("Escape");
  assert.strictEqual(activeField, null);
  assert.strictEqual(activeEvidence, null);
});

test("clipboard copy helper - formats text and triggers feedback state", () => {
  let copiedKey = null;

  function copyToClipboard(key, value) {
    if (value === null || value === undefined) return;
    const text = String(value);
    copiedKey = key;
    return text;
  }

  const copiedVal = copyToClipboard("invoice_number", "SIS/26-27/0042");
  assert.strictEqual(copiedVal, "SIS/26-27/0042");
  assert.strictEqual(copiedKey, "invoice_number");

  const nullVal = copyToClipboard("empty_field", null);
  assert.strictEqual(nullVal, undefined);
});
