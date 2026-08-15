import { useState } from "react";
import api from "../api/client";
import { useFetch } from "../api/useFetch";
import { useToast } from "../context/ToastContext";
import { PayButton, PayDuesPanel, usePayConfig } from "../components/PayNow";
import {
  Empty, ErrorNote, fmtDate, inr, Loading, Pager, StatusPill,
} from "../components/ui";
import "../styles/PortalPay.css";

/**
 * The customer's bills, bill by bill - and what is still to pay on each.
 *
 * The pay endpoints existed on the API from the start but nothing ever called
 * them, so "pay online" was a feature the business had paid for and no
 * customer could reach. This screen is that missing half.
 *
 * Settling the account total lives here too, above the table: somebody who
 * owes four bills wants to clear the number, not tap four buttons. The
 * total comes from the server rather than from the rows on screen, because
 * the rows are one page of a list.
 */
export default function PortalInvoices() {
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } = useFetch("/portal/invoices", { page });
  const gateway = usePayConfig();

  const rows = Array.isArray(data) ? data : [];
  const outstanding = Number(meta?.outstanding || 0);

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>Invoices</h1>
          <p>Your bills, and what is still to pay on each.</p>
        </div>
      </div>

      <ErrorNote error={error} onRetry={refetch} />

      <PayDuesPanel outstanding={outstanding} invoiceCount={meta?.due_invoice_count}
                    gateway={gateway} onPaid={refetch} />

      <section className="panel table-wrap">
        {loading ? <Loading label="Loading your invoices" />
          : !rows.length ? <Empty title="No invoices yet"
                                  hint="Bills raised on your account will appear here." />
            : (
              /* `cards-sm` plus a data-label on every cell: below 720px each
                 row stacks into its own labelled card. Seven columns of bill
                 on a 360px screen is a sideways scroll nobody performs. */
              <table className="data portal-invoices cards-sm">
                <thead>
                  <tr>
                    <th>Invoice</th><th>Date</th><th>Due</th>
                    <th className="num">Amount</th><th className="num">To pay</th>
                    <th>Status</th><th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((invoice) => (
                    <tr key={invoice.id}>
                      <td className="mono" data-label="Invoice">{invoice.invoice_no}</td>
                      <td data-label="Raised">{fmtDate(invoice.issue_date)}</td>
                      <td data-label="Due by">{fmtDate(invoice.due_date)}</td>
                      <td className="num" data-label="Amount">{inr(invoice.total_amount)}</td>
                      <td className="num" data-label="To pay">
                        {Number(invoice.balance) > 0
                          ? <strong className="due">{inr(invoice.balance)}</strong>
                          : "—"}
                      </td>
                      <td data-label="Status"><StatusPill value={invoice.status} /></td>
                      <td className="row-actions" data-label="">
                        <DownloadBill invoice={invoice} />
                        {Number(invoice.balance) > 0 && gateway?.enabled && (
                          <PayButton invoice={invoice} gateway={gateway}
                                     onPaid={refetch} />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
      </section>

      <Pager meta={meta} onPage={setPage} />
    </section>
  );
}

function DownloadBill({ invoice }) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);

  async function open() {
    setBusy(true);
    // Open the tab NOW, inside the click gesture. Opening it after the
    // `await` below trips the popup blocker - by then the user-gesture has
    // expired - and on Android the bill is silently dropped with no error to
    // show. We get the handle first, then point it at the downloaded PDF.
    const win = window.open("", "_blank");
    try {
      // Token-protected, so fetch it with the auth header and hand the browser
      // a local blob rather than navigating to a URL that arrives signed out.
      const response = await api.get(`/portal/invoices/${invoice.id}/pdf`,
                                     { responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      if (win) {
        win.location.href = url;
      } else {
        window.open(url, "_blank", "noopener");
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      try { win?.close(); } catch { /* a window we cannot reach is not ours */ }
      toast.error("That bill could not be opened. Please try again shortly.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" className="btn sm" onClick={open} disabled={busy}>
      {busy ? "…" : "Bill"}
    </button>
  );
}
