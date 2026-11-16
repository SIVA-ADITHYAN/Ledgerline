import { useEffect, useRef, useState, type ChangeEvent } from "react";
import {
  approveInvoice,
  getInvoice,
  listInvoices,
  rejectInvoice,
  uploadInvoice,
  type EvidenceSpan,
  type Invoice,
  type InvoiceSummary,
} from "./api";
import { sliceDocumentForHighlight } from "./utils/unicode";

type FilterStatus = "all" | "needs_review" | "ready_for_approval" | "approved" | "rejected";

const FILTER_TABS: { id: FilterStatus; label: string }[] = [
  { id: "all", label: "All" },
  { id: "needs_review", label: "Needs Review" },
  { id: "ready_for_approval", label: "Ready for Approval" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
];

function money(value: number | null | undefined, currency: string | null) {
  if (value === null || value === undefined) return "–";
  const symbol = currency === "USD" ? "$" : "₹";
  return symbol + value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const formatStatusLabel = (status: string) => status.replace(/_/g, " ");

export default function App() {
  const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
  const [allInvoices, setAllInvoices] = useState<InvoiceSummary[]>([]);
  const [filter, setFilter] = useState<FilterStatus>("all");
  const [selected, setSelected] = useState<Invoice | null>(null);
  const [activeField, setActiveField] = useState<string | null>(null);
  const [activeEvidence, setActiveEvidence] = useState<EvidenceSpan | null>(null);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const [isError, setIsError] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const highlightRef = useRef<HTMLElement | null>(null);

  // Load and refresh queue data
  async function refreshQueue() {
    try {
      const all = await listInvoices();
      setAllInvoices(all);
      const targetList = filter === "all" ? all : await listInvoices(filter);
      setInvoices(targetList);
      if (!selected && targetList.length > 0) {
        open(targetList[0].id);
      }
    } catch (err) {
      console.error("Queue refresh error:", err);
    }
  }

  // Initial load and gentle 15-second refresh (replaces 500ms spamming)
  useEffect(() => {
    refreshQueue();
    const interval = setInterval(refreshQueue, 15000);
    const onFocus = () => refreshQueue();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [filter]);

  // Scroll highlight into view whenever active evidence changes
  useEffect(() => {
    if (activeEvidence && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [activeEvidence]);

  // Keyboard navigation: J / K / Down / Up to navigate queue
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.isComposing
      ) {
        return;
      }

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        navigateQueue(1);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        navigateQueue(-1);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [invoices, selected]);

  function navigateQueue(delta: number) {
    if (invoices.length === 0) return;
    if (!selected) {
      open(invoices[0].id);
      return;
    }
    const currentIndex = invoices.findIndex((inv) => inv.id === selected.id);
    const nextIndex = currentIndex + delta;
    if (nextIndex >= 0 && nextIndex < invoices.length) {
      open(invoices[nextIndex].id);
    }
  }

  async function open(id: number) {
    try {
      const invoice = await getInvoice(id);
      setSelected(invoice);
      setActiveField(null);
      setActiveEvidence(null);
      setNote("");
      setMessage("");
      setIsError(false);
    } catch (error) {
      setIsError(true);
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function onUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setIsError(false);
    setMessage(`Processing ${file.name}…`);
    try {
      const invoice = await uploadInvoice(file);
      setSelected(invoice);
      setActiveField(null);
      setActiveEvidence(null);
      setMessage(`Successfully processed ${file.name}`);
      refreshQueue();
    } catch (error) {
      setIsError(true);
      setMessage(error instanceof Error ? error.message : String(error));
    }
    event.target.value = "";
  }

  async function handleApprove() {
    if (!selected || isSubmitting || selected.status === "approved" || selected.status === "rejected") return;
    setIsSubmitting(true);
    setIsError(false);
    try {
      const result = await approveInvoice(selected.id, note);
      setMessage(`Approval recorded (${result.approvals ?? 1} of ${result.required_approvals ?? 1})`);
      const updated = await getInvoice(selected.id);
      setSelected(updated);
      refreshQueue();
    } catch (error) {
      setIsError(true);
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleReject() {
    if (!selected || isSubmitting || selected.status === "approved" || selected.status === "rejected") return;
    setIsSubmitting(true);
    setIsError(false);
    try {
      await rejectInvoice(selected.id, note);
      setMessage("Document rejected");
      const updated = await getInvoice(selected.id);
      setSelected(updated);
      refreshQueue();
    } catch (error) {
      setIsError(true);
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  function activateEvidence(fieldName: string, ev?: EvidenceSpan | null) {
    if (!ev || typeof ev.start !== "number" || typeof ev.end !== "number") {
      return;
    }
    if (activeField === fieldName) {
      // Toggle off if clicked again
      setActiveField(null);
      setActiveEvidence(null);
    } else {
      setActiveField(fieldName);
      setActiveEvidence(ev);
    }
  }

  // Queue status counts computed from all loaded invoices
  const counts: Record<FilterStatus, number> = {
    all: allInvoices.length,
    needs_review: allInvoices.filter((i) => i.status === "needs_review").length,
    ready_for_approval: allInvoices.filter((i) => i.status === "ready_for_approval").length,
    approved: allInvoices.filter((i) => i.status === "approved").length,
    rejected: allInvoices.filter((i) => i.status === "rejected").length,
  };

  const currentIndex = selected ? invoices.findIndex((i) => i.id === selected.id) : -1;
  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex >= 0 && currentIndex < invoices.length - 1;

  // Render document text with evidence highlighting using Unicode offset conversion
  function renderDocumentContent() {
    if (!selected) return null;
    const rawText = selected.raw_text;
    const { before, highlighted, after, isHighlighted } = sliceDocumentForHighlight(
      rawText,
      activeEvidence
    );

    if (!isHighlighted) {
      return <pre className="document">{rawText}</pre>;
    }

    return (
      <pre className="document">
        {before}
        <mark ref={highlightRef} className="doc-highlight">
          {highlighted}
        </mark>
        {after}
      </pre>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>Ledgerline</h1>
        <span className="org">Meridian Precision Components</span>
        <label className="upload">
          Upload document
          <input type="file" accept=".txt,.eml,.json" onChange={onUpload} />
        </label>
      </header>

      {message && (
        <p className={`message ${isError ? "error" : ""}`} role="status">
          {message}
        </p>
      )}

      <main className="layout">
        <section className="queue" aria-label="Documents">
          <div className="queue-header">
            <nav className="queue-filters" aria-label="Filter queue by status">
              {FILTER_TABS.map((tab) => (
                <button
                  key={tab.id}
                  className={`filter-tab ${filter === tab.id ? "active" : ""}`}
                  onClick={() => setFilter(tab.id)}
                >
                  {tab.label}
                  <span className="filter-badge">{counts[tab.id]}</span>
                </button>
              ))}
            </nav>
          </div>

          <table>
            <thead>
              <tr>
                <th>Supplier</th>
                <th>Invoice</th>
                <th className="num">Total</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => (
                <tr
                  key={inv.id}
                  onClick={() => open(inv.id)}
                  className={selected?.id === inv.id ? "active" : ""}
                >
                  <td>{inv.supplier_name ?? "Unknown supplier"}</td>
                  <td>
                    {inv.invoice_number ?? "–"}
                    <small>{inv.invoice_date ?? "No date"}</small>
                  </td>
                  <td className="num">{money(inv.total, inv.currency)}</td>
                  <td>
                    <span className={`badge ${inv.status}`}>{formatStatusLabel(inv.status)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {invoices.length === 0 && (
            <p className="empty">No documents found matching this filter.</p>
          )}
        </section>

        <section className="detail" aria-label="Selected document">
          {selected ? (
            <>
              <div className="detail-top">
                <div>
                  <h2>{selected.supplier_name ?? "Unknown supplier"}</h2>
                  <span className={`badge ${selected.status}`} style={{ marginTop: "0.4rem" }}>
                    {formatStatusLabel(selected.status)}
                  </span>
                </div>
                <div className="nav-group">
                  <button
                    className="nav-btn"
                    onClick={() => navigateQueue(-1)}
                    disabled={!hasPrev}
                    title="Previous document (K or Up)"
                  >
                    ← Prev
                  </button>
                  <button
                    className="nav-btn"
                    onClick={() => navigateQueue(1)}
                    disabled={!hasNext}
                    title="Next document (J or Down)"
                  >
                    Next →
                  </button>
                  <span className="keyboard-hint" title="Use J/K or Up/Down keys">
                    [J / K keys]
                  </span>
                </div>
              </div>

              {/* Review Reasons Structured Cards */}
              {selected.review_reasons_detail && selected.review_reasons_detail.length > 0 ? (
                <div className="callout review">
                  <h3 style={{ marginTop: 0 }}>Review Reasons ({selected.review_reasons_detail.length})</h3>
                  <div className="reasons-container">
                    {selected.review_reasons_detail.map((detail) => {
                      const hasEv = Boolean(
                        detail.evidence &&
                          typeof detail.evidence.start === "number" &&
                          typeof detail.evidence.end === "number"
                      );
                      const isTargetActive = activeField === (detail.target_field || detail.code);

                      return (
                        <div key={detail.code} className="reason-card">
                          <div className="reason-card-header">
                            <span className="reason-title">
                              ⚠️ {detail.label}
                            </span>
                            {hasEv && (
                              <button
                                className={`jump-btn ${isTargetActive ? "active" : ""}`}
                                onClick={() => activateEvidence(detail.target_field || detail.code, detail.evidence)}
                              >
                                {isTargetActive ? "Highlighted" : "Jump to evidence →"}
                              </button>
                            )}
                          </div>
                          <p className="reason-desc">{detail.description}</p>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : selected.review_reasons.length > 0 ? (
                <div className="callout review">
                  <h3>Needs review</h3>
                  <ul>
                    {selected.review_reasons.map((r) => (
                      <li key={r}>{formatStatusLabel(r)}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {/* Extracted Fields with Evidence Highlighting */}
              <h3 style={{ marginTop: "1.25rem" }}>Extracted Fields</h3>
              <dl className="fields">
                <dt>GSTIN</dt>
                <dd>
                  <span
                    className={`field-clickable ${activeField === "supplier_gstin" ? "active" : ""}`}
                    onClick={() => activateEvidence("supplier_gstin", selected.evidence?.["supplier_gstin"])}
                  >
                    {selected.supplier_gstin ?? "–"}
                    {selected.evidence?.["supplier_gstin"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>

                <dt>Invoice number</dt>
                <dd>
                  <span
                    className={`field-clickable ${activeField === "invoice_number" ? "active" : ""}`}
                    onClick={() => activateEvidence("invoice_number", selected.evidence?.["invoice_number"])}
                  >
                    {selected.invoice_number ?? "–"}
                    {selected.evidence?.["invoice_number"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>

                <dt>Date</dt>
                <dd>
                  <span
                    className={`field-clickable ${activeField === "invoice_date" ? "active" : ""}`}
                    onClick={() => activateEvidence("invoice_date", selected.evidence?.["invoice_date"])}
                  >
                    {selected.invoice_date ?? "–"}
                    {selected.evidence?.["invoice_date"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>

                <dt>PO Number</dt>
                <dd>
                  <span
                    className={`field-clickable ${activeField === "po_number" ? "active" : ""}`}
                    onClick={() => activateEvidence("po_number", selected.evidence?.["po_number"])}
                  >
                    {selected.po_number ?? "–"}
                    {selected.evidence?.["po_number"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>

                <dt>Subtotal</dt>
                <dd>
                  <span
                    className={`field-clickable ${activeField === "subtotal" ? "active" : ""}`}
                    onClick={() => activateEvidence("subtotal", selected.evidence?.["subtotal"])}
                  >
                    {money(selected.subtotal, selected.currency)}
                    {selected.evidence?.["subtotal"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>

                <dt>CGST</dt>
                <dd>
                  <span
                    className={`field-clickable ${activeField === "cgst" ? "active" : ""}`}
                    onClick={() => activateEvidence("cgst", selected.evidence?.["cgst"])}
                  >
                    {money(selected.cgst, selected.currency)}
                    {selected.evidence?.["cgst"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>

                <dt>SGST</dt>
                <dd>
                  <span
                    className={`field-clickable ${activeField === "sgst" ? "active" : ""}`}
                    onClick={() => activateEvidence("sgst", selected.evidence?.["sgst"])}
                  >
                    {money(selected.sgst, selected.currency)}
                    {selected.evidence?.["sgst"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>

                <dt>IGST</dt>
                <dd>
                  <span
                    className={`field-clickable ${activeField === "igst" ? "active" : ""}`}
                    onClick={() => activateEvidence("igst", selected.evidence?.["igst"])}
                  >
                    {money(selected.igst, selected.currency)}
                    {selected.evidence?.["igst"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>

                <dt>Total</dt>
                <dd className="total">
                  <span
                    className={`field-clickable ${activeField === "total" ? "active" : ""}`}
                    onClick={() => activateEvidence("total", selected.evidence?.["total"])}
                  >
                    {money(selected.total, selected.currency)}
                    {selected.evidence?.["total"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>

                <dt>Bank Details</dt>
                <dd>
                  <span
                    className={`field-clickable ${activeField === "bank_account" ? "active" : ""}`}
                    onClick={() => activateEvidence("bank_account", selected.evidence?.["bank_account"])}
                  >
                    {selected.evidence?.["bank_account"]?.masked_text || selected.bank_account || "–"}{" "}
                    {selected.ifsc ?? ""}
                    {selected.evidence?.["bank_account"] && (
                      <span className="evidence-indicator" title="Click to view evidence in document">
                        🔍 Evidence
                      </span>
                    )}
                  </span>
                </dd>
              </dl>

              {/* Secure Notes Rendering (no dangerouslySetInnerHTML) */}
              {selected.notes && (
                <div className="callout" style={{ marginTop: "1rem" }}>
                  <h3 style={{ margin: "0 0 0.35rem" }}>Notes on the document</h3>
                  <div className="notes-text">{selected.notes}</div>
                </div>
              )}

              {/* Line items */}
              {selected.line_items.length > 0 && (
                <>
                  <h3>Line items</h3>
                  <table className="lines">
                    <thead>
                      <tr>
                        <th>Description</th>
                        <th className="num">Qty</th>
                        <th className="num">Rate</th>
                        <th className="num">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.line_items.map((item, i) => (
                        <tr key={i}>
                          <td>{item.description}</td>
                          <td className="num">{item.quantity ?? "–"}</td>
                          <td className="num">{money(item.unit_price, selected.currency)}</td>
                          <td className="num">{money(item.amount, selected.currency)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              {/* Evidence-Linked Document Viewer */}
              <div className="doc-section-header">
                <h3>Original document</h3>
                {activeField && (
                  <div className="highlight-banner">
                    <span>
                      Highlighting <strong>{formatStatusLabel(activeField)}</strong>
                    </span>
                    <button
                      className="clear-highlight"
                      onClick={() => {
                        setActiveField(null);
                        setActiveEvidence(null);
                      }}
                    >
                      Clear highlight
                    </button>
                  </div>
                )}
              </div>
              {renderDocumentContent()}

              {/* Decision Section */}
              {(() => {
                const isTerminal = selected.status === "approved" || selected.status === "rejected";
                return (
                  <div className="decision">
                    {isTerminal ? (
                      <div className="terminal-notice" style={{ padding: "0.5rem 0", color: "var(--muted)" }}>
                        <p style={{ margin: 0 }}>
                          This invoice is <strong>{selected.status.toUpperCase()}</strong>. Review decisions are final.
                        </p>
                      </div>
                    ) : (
                      <textarea
                        placeholder="Note for the audit log (required when approving an invoice needing review)"
                        value={note}
                        disabled={isSubmitting}
                        onChange={(e) => setNote(e.target.value)}
                      />
                    )}
                    <div className="actions">
                      <button
                        onClick={handleApprove}
                        disabled={isSubmitting || isTerminal}
                        title={isTerminal ? `Invoice is already ${selected.status}` : undefined}
                      >
                        {isSubmitting ? "Approving…" : "Approve"}
                      </button>
                      <button
                        className="secondary"
                        onClick={handleReject}
                        disabled={isSubmitting || isTerminal}
                        title={isTerminal ? `Invoice is already ${selected.status}` : undefined}
                      >
                        {isSubmitting ? "Rejecting…" : "Reject"}
                      </button>
                    </div>
                    <p className="hint">
                      Status: <strong>{formatStatusLabel(selected.status)}</strong>. Approvals required:{" "}
                      <strong>{selected.required_approvals}</strong>.
                    </p>
                  </div>
                );
              })()}
            </>
          ) : (
            <p className="empty">Choose a document from the queue to review it.</p>
          )}
        </section>
      </main>
    </div>
  );
}
