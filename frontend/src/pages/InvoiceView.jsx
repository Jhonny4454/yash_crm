import { useState } from "react";
import { useParams } from "react-router-dom";
import { post } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useToast } from "../context/ToastContext";
import { ErrorNote, Loading, StatusPill, fmtDate, inr, readableError } from "../components/ui";

/**
 * Printable bill. The company block (name, logo, GSTIN, bank details) comes
 * from the invoice payload itself, so whatever logo is uploaded in Companies
 * shows up here and in the mobile app without any extra wiring.
 */
export default function InvoiceView() {
  const { id } = useParams();
  const { toast, confirm } = useToast();
  const [cancelling, setCancelling] = useState(false);
  const { data, loading, error, refetch } = useFetch(`/invoices/${id}`);

  if (loading) return <Loading label="Loading invoice" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return null;

  const inv = data;
  const co = inv.company || {};
  const cust = inv.customer || {};

  async function cancelInvoice() {
    const confirmed = await confirm({
      title: `Cancel ${inv.invoice_no}?`,
      message: `${inv.caption || "This bill"} — ${inr(inv.total_amount)}. The `
        + "bill stays on the record marked cancelled, with any payment "
        + "entries still visible against it. This cannot be undone.",
      confirmLabel: "Cancel invoice",
      tone: "danger",
    });
    if (!confirmed) return;
    setCancelling(true);
    try {
      const response = await post(`/invoices/${inv.id}/cancel`, {});
      toast.success(`${(response?.data ?? response).invoice_no} cancelled.`);
      refetch();
    } catch (err) {
      toast.error(err.detail || readableError(err));
      setCancelling(false);
    }
  }

  const cancelled = inv.status === "cancelled";
  const hasPaid = Number(inv.paid_amount || 0) > 0;

  return (
    <>
      <div className="toolbar no-print">
        <button className="btn primary" onClick={() => window.print()}>Print / Save as PDF</button>
        {!cancelled && (
          <button className="btn danger" disabled={cancelling || hasPaid}
                  title={hasPaid
                    ? "Money has been paid against this bill — reverse those payments first."
                    : "Void this bill. It stays on the record, marked cancelled."}
                  onClick={cancelInvoice}>
            {cancelling ? "Cancelling…" : "Cancel invoice"}
          </button>
        )}
        <StatusPill value={inv.status} />
      </div>

      <div className="card" style={{ maxWidth: 860 }}>
        <div className="card-body">
          <header style={{ display: "flex", justifyContent: "space-between", gap: 20, paddingBottom: 18, borderBottom: "2px solid var(--ink)" }}>
            <div style={{ display: "flex", gap: 14 }}>
              {co.logo_url && (
                <img src={co.logo_url} alt="" style={{ maxHeight: 62, maxWidth: 150, objectFit: "contain" }} />
              )}
              <div>
                <h2 style={{ margin: 0, fontSize: 18 }}>{co.name}</h2>
                <div style={{ fontSize: 12.5, color: "var(--muted)", whiteSpace: "pre-line", marginTop: 3 }}>
                  {co.address}
                </div>
                <div className="num" style={{ fontSize: 12.5, color: "var(--muted)" }}>
                  {[co.mobile, co.email].filter(Boolean).join(" · ")}
                </div>
                {co.gstin && <div className="num" style={{ fontSize: 12.5 }}>GSTIN: {co.gstin}</div>}
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".08em", color: "var(--muted)", fontWeight: 600 }}>
                Tax invoice
              </div>
              <div className="num" style={{ fontSize: 17, fontWeight: 650 }}>{inv.invoice_no}</div>
              <div className="num" style={{ fontSize: 12.5, color: "var(--muted)" }}>
                Issued {fmtDate(inv.issue_date)}<br />Due {fmtDate(inv.due_date)}
              </div>
            </div>
          </header>

          <section style={{ display: "flex", gap: 30, padding: "16px 0", borderBottom: "1px solid var(--line)" }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".06em", color: "var(--muted)", fontWeight: 600, marginBottom: 4 }}>
                Billed to
              </div>
              <strong>{cust.full_name}</strong>
              <div className="num" style={{ fontSize: 13 }}>{cust.mobile}</div>
              <div style={{ fontSize: 13, color: "var(--ink-soft)", whiteSpace: "pre-line" }}>
                {cust.billing_address || [cust.flat_no, cust.building, cust.area, cust.locality].filter((v) => v && v !== '-').join(' -> ') + (cust.locality ? ', Navi Mumbai, Maharashtra' : '')}
              </div>
              {cust.gstin && <div className="num" style={{ fontSize: 12.5 }}>GSTIN: {cust.gstin}</div>}
            </div>
            {inv.plan?.plan && (
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".06em", color: "var(--muted)", fontWeight: 600, marginBottom: 4 }}>
                  Service period
                </div>
                <strong>{inv.plan.plan.name}</strong>
                <div className="num" style={{ fontSize: 13 }}>
                  {fmtDate(inv.plan.start_date)} → {fmtDate(inv.plan.end_date)}
                </div>
              </div>
            )}
          </section>

          <table className="data" style={{ marginTop: 10 }}>
            <thead>
              <tr><th>Description</th><th className="right">Amount</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>{inv.caption || "Internet service"}</td>
                <td className="right num">{inr(inv.total_amount)}</td>
              </tr>
            </tbody>
          </table>

          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
            <div style={{ minWidth: 280 }}>
              <Line label="Subtotal" value={inr(inv.total_amount)} />
              {inv.tax_amount > 0 && <Line label="Tax" value={inr(inv.tax_amount)} />}
              {inv.discount_amount > 0 && <Line label="Discount" value={`− ${inr(inv.discount_amount)}`} />}
              <Line label="Net payable" value={inr(inv.net_amount)} bold />
              <Line label="Paid" value={inr(inv.paid_amount)} />
              <Line label="Balance due" value={inv.status === 'cancelled' ? '₹0 — Voided' : inr(inv.balance)} bold danger={inv.balance > 0 && inv.status !== 'cancelled'} />
            </div>
          </div>

          {inv.payments?.length > 0 && (
            <>
              <h3 style={{ fontSize: 13, marginTop: 22, marginBottom: 6 }}>Payments received</h3>
              <table className="data">
                <thead><tr><th>Date</th><th>Mode</th><th>Reference</th><th className="right">Amount</th></tr></thead>
                <tbody>
                  {inv.payments.map((p) => (
                    <tr key={p.id}>
                      <td className="num">{fmtDate(p.payment_date)}</td>
                      <td>{p.payment_mode}{p.source === "portal" ? " (app)" : ""}</td>
                      <td className="num">{p.gateway_transaction_id || p.book_receipt_no || "—"}</td>
                      <td className="right num">{inr(p.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {(co.bank_account_details || co.invoice_notes) && (
            <footer style={{ marginTop: 24, paddingTop: 14, borderTop: "1px solid var(--line)", fontSize: 12.5, color: "var(--ink-soft)", whiteSpace: "pre-line" }}>
              {co.bank_account_details && <div><strong>Bank details</strong>{"\n"}{co.bank_account_details}</div>}
              {co.invoice_notes && <div style={{ marginTop: 8 }}>{co.invoice_notes}</div>}
            </footer>
          )}
        </div>
      </div>
    </>
  );
}

function Line({ label, value, bold, danger }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid var(--line)" }}>
      <span style={{ color: "var(--muted)" }}>{label}</span>
      <span className="num" style={{ fontWeight: bold ? 650 : 400, color: danger ? "var(--danger)" : "inherit" }}>
        {value}
      </span>
    </div>
  );
}
