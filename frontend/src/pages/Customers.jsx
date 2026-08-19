import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { del } from "../api/client";
import { useDebounced, useFetch } from "../api/useFetch";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { Empty, ErrorNote, Pager, ScrollArrows, TableSkeleton, readableError } from "../components/ui";

/** Mirrors templates/customers/list.html - same columns, same icon actions. */
export default function Customers() {
  const { isAdmin } = useAuth();
  const { toast, confirm } = useToast();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const [search, setSearch] = useState(params.get("q") || "");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState(null);

  const q = useDebounced(search);
  // ✅ Now automatically adds the Bearer token and handles refresh
  const from = params.get("from") || "";
  const to = params.get("to") || "";
  const label = params.get("label") || "";

  const { data, meta, loading, refreshing, error, refetch } =
    useFetch("/customers", { q, status, from: from || undefined, to: to || undefined, page });

  async function remove(c) {
    // This button said "Deactivate" because the endpoint behind it only set a
    // flag. It deletes now, so the wording has to carry that weight: name the
    // customer, say what goes with them, and point at the reversible option
    // for the far more common case of a customer who has simply left.
    const confirmed = await confirm({
      title: `Delete ${c.full_name} permanently?`,
      message: "This removes the customer and everything attached to them - "
        + "plans, invoices, receipts, payment history and message log. Invoices "
        + "are GST records, so deleting them also changes totals that may "
        + "already have been reported. It cannot be undone. To stop the "
        + "connection and keep the history, open the customer and use Disable.",
      confirmLabel: "Delete permanently",
      tone: "danger",
    });
    if (!confirmed) return;

    setBusyId(c.id);
    setActionError(null);
    try {
      await del(`/customers/${c.id}`);
      toast.success(`${c.full_name} has been deleted.`);
      refetch();
    } catch (err) {
      setActionError(err);
      toast.error(readableError(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="list-container">
      {(from || to || label) && (
        <div className="bulk-bar" role="status" style={{ borderRadius: 10 }}>
          <span>
            Showing <strong>{label || "customers"}</strong>
            {from && to ? ` registered ${from} to ${to}` : ""}
          </span>
          <div className="bulk-actions">
            <button className="btn sm" onClick={() => setParams({}, { replace: true })} style={{ borderRadius: 6 }}>Clear filter</button>
          </div>
        </div>
      )}
      <div className="page-heading">
        <div>
          <h1>All Customers</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: 2 }}>
            Manage your customer base.
          </p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div className="search-wrapper" style={{ position: "relative" }}>
            <i className="fas fa-search" style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "#94a3b8", fontSize: "0.82rem" }} />
            <input
              type="text"
              placeholder="Search customers..."
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              style={{ padding: "8px 12px 8px 34px", borderRadius: 8, border: "1px solid #d0d5dd", fontSize: "0.88rem", width: 220 }}
            />
          </div>
          <select
            className="select"
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1); }}
            style={{ width: 140, padding: "8px 12px", borderRadius: 8, border: "1px solid #d0d5dd", fontSize: "0.88rem" }}
          >
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
          <Link to="/customers/add" className="btn primary" style={{ borderRadius: 8, padding: "9px 18px" }}>
            <i className="fas fa-plus" /> Add New
          </Link>
        </div>
      </div>

      <ErrorNote error={error} onRetry={refetch} />
      {actionError && <div className="alert error" style={{ borderRadius: 8 }}>{readableError(actionError)}</div>}

      <ScrollArrows wrapClassName={`table-wrap${refreshing ? " is-refreshing" : ""}`} style={{ borderRadius: 12, overflow: "hidden" }}>
        {loading ? (
          <TableSkeleton rows={8} cols={8} label="Loading customers" />
        ) : !data?.length ? (
          <Empty
            title={search ? "No customers match" : "No customers yet"}
            hint={search ? "Try a different search." : "Add your first customer to get started."}
          />
        ) : (
          <table className="data cards-sm">
            <thead>
              <tr>
                <th>Name</th>
                <th>Mobile</th>
                <th>Email</th>
                <th>Zone</th>
                <th>Plan</th>
                <th>Status</th>
                <th style={{ textAlign: "center" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.map((c, i) => (
                <tr key={c.id}>
                  <td data-label="Name">
                    <Link to={`/customers/${c.id}`}>
                      <strong>{c.full_name}</strong>
                    </Link>
                    {c.company_name && (
                      <div style={{ fontSize: 12, color: "#64748b" }}>
                        {c.company_name}
                      </div>
                    )}
                  </td>
                  <td className="num" data-label="Mobile">{c.mobile || "-"}</td>
                  <td data-label="Email">{c.email || "-"}</td>
                  <td data-label="Zone">{c.zone || "-"}</td>
                  <td data-label="Plan">
                    {c.active_plan_name || (
                      <span style={{ color: "#94a3b8" }}>None</span>
                    )}
                  </td>
                  <td data-label="Status">
                    <span className={`pill ${c.is_active ? "ok" : "danger"}`}>
                      {c.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="actions-cell" data-label="">
                    <div className="row-actions">
                      <Link to={`/customers/${c.id}`} className="btn sm" title="View" style={{ borderRadius: 6 }}>
                        <i className="fas fa-eye" />
                      </Link>
                      <button
                        className="btn sm"
                        title="Edit"
                        onClick={() => navigate(`/customers/${c.id}/edit`)}
                        style={{ borderRadius: 6 }}
                      >
                        <i className="fas fa-edit" />
                      </button>
                      {isAdmin && (
                        <button
                          className="btn sm danger"
                          title="Delete"
                          disabled={busyId === c.id}
                          onClick={() => remove(c)}
                          style={{ borderRadius: 6 }}
                        >
                          <i className="fas fa-trash" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ScrollArrows>

      <Pager meta={meta} onPage={setPage} />
    </div>
  );
}