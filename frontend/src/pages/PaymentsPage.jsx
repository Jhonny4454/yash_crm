import { useState } from "react";
import { post } from "../api/client";
import { useFetch } from "../api/useFetch";
import { Empty, ErrorNote, Pager, fmtDate, inr, Loading, StatusPill } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";

export default function PaymentsPage({ review = false }) {
  const { isAdmin } = useAuth();
  const { toast, confirm } = useToast();
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } = useFetch("/payments", { ...(review ? { pending_auth: 1 } : {}), page });

  async function resolve(payment, approved) {
    if (!approved) {
      const confirmed = await confirm({
        title: "Reject this payment?",
        message: `${inr(payment.amount)} from ${payment.customer_name || `customer #${payment.customer_id}`} will be marked rejected, and its invoice reopened if it was settled by this payment.`,
        confirmLabel: "Reject payment",
        tone: "danger",
      });
      if (!confirmed) return;
    }

    setBusy(payment.id);
    setActionError(null);
    try {
      await post(
        `/payments/${payment.id}/${approved ? "authorize" : "reject"}`,
        approved ? {} : { reason: "Rejected in admin review" },
      );
      toast.success(approved ? "Payment authorised." : "Payment rejected.");
      refetch();
    } catch (err) {
      setActionError(err);
      toast.error(err.message || "That payment could not be updated.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>{review ? "Payment authorisations" : "Payments"}</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: 2 }}>
            {review ? "Review payment entries before they are finalised." : "All payments recorded in the system."}
          </p>
        </div>
      </div>

      <ErrorNote error={error || actionError} onRetry={refetch} />

      <div className="card" style={{ borderRadius: 12, overflow: "hidden" }}>
        <div className="table-wrap">
          {loading ? (
            <Loading label="Loading payments" />
          ) : !data?.length ? (
            <Empty
              title="No payments found"
              hint="Payments will appear here when they are recorded."
            />
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Customer</th>
                  <th>Invoice</th>
                  <th>Mode</th>
                  <th className="right">Amount</th>
                  <th>Status</th>
                  {review && <th className="right">Actions</th>}
                </tr>
              </thead>
              <tbody>
                {data.map((row) => (
                  <tr key={row.id}>
                    <td>{fmtDate(row.payment_date)}</td>
                    <td>{row.customer_name || row.customer_id}</td>
                    <td>{row.invoice_no || row.invoice_id || "—"}</td>
                    <td>{row.payment_mode}</td>
                    <td className="right num">{inr(row.amount)}</td>
                    <td><StatusPill value={row.status} kind="payment" /></td>
                    {review && (
                      <td className="right">
                        {isAdmin ? (
                          <div className="row-actions">
                            <button className="btn sm primary" disabled={busy === row.id}
                                    onClick={() => resolve(row, true)}
                                    style={{ borderRadius: 6 }}>
                              Approve
                            </button>
                            <button className="btn sm danger" disabled={busy === row.id}
                                    onClick={() => resolve(row, false)}
                                    style={{ borderRadius: 6 }}>
                              Reject
                            </button>
                          </div>
                        ) : (
                          <span className="muted" style={{ fontSize: "0.78rem" }}>Admin only</span>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <Pager meta={meta} onPage={setPage} />
      </div>
    </section>
  );
}
