import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { del } from "../api/client";
import { useDebounced, useFetch } from "../api/useFetch";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { Empty, ErrorNote, Pager, TableSkeleton, readableError } from "../components/ui";

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
        <div className="bulk-bar" role="status">
          <span>
            Showing <strong>{label || "customers"}</strong>
            {from && to ? ` registered ${from} to ${to}` : ""}
          </span>
          <div className="bulk-actions">
            <button className="btn sm" onClick={() => setParams({}, { replace: true })}>Clear filter</button>
          </div>
        </div>
      )}
      <div className="list-head">
        <h4><i className="fas fa-users" /> All Customers</h4>
        <div className="list-actions">
          <div className="search-wrapper">
            <i className="fas fa-search" />
            <input
              type="text"
              placeholder="Search customers..."
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            />
          </div>
          <select
            className="select" style={{ width: 150 }}
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1); }}
          >
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
          <Link to="/customers/add" className="btn primary rounded-pill">
            <i className="fas fa-plus" /> Add New
          </Link>
        </div>
      </div>

      <ErrorNote error={error} onRetry={refetch} />
      {actionError && <div className="alert error">{readableError(actionError)}</div>}

      <div className={`table-wrap${refreshing ? " is-refreshing" : ""}`}>
        {loading ? (
          <TableSkeleton rows={8} cols={8} label="Loading customers" />
        ) : !data?.length ? (
          <Empty
            title={search ? "No customers match" : "No customers yet"}
            hint={search ? "Try a different search." : "Add your first customer to get started."}
          />
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>#</th>
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
                  <td className="num">
                    {((meta?.page || 1) - 1) * (meta?.per_page || 25) + i + 1}
                  </td>
                  <td>
                    <Link to={`/customers/${c.id}`}>
                      <strong>{c.full_name}</strong>
                    </Link>
                    {c.company_name && (
                      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                        {c.company_name}
                      </div>
                    )}
                  </td>
                  <td className="num">{c.mobile || "-"}</td>
                  <td>{c.email || "-"}</td>
                  <td>{c.zone || "-"}</td>
                  <td>
                    {c.active_plan_name || (
                      <span style={{ color: "var(--text-muted)" }}>None</span>
                    )}
                  </td>
                  <td>
                    <span className={`pill ${c.is_active ? "ok" : "danger"}`}>
                      {c.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="actions-cell">
                    <Link to={`/customers/${c.id}`} className="btn-icon-sm" title="View">
                      <i className="fas fa-eye" />
                    </Link>
                    <button
                      className="btn-icon-sm"
                      title="Edit"
                      onClick={() => navigate(`/customers/${c.id}/edit`)}
                    >
                      <i className="fas fa-edit" />
                    </button>
                    {isAdmin && (
                      <button
                        className="btn-icon-sm danger"
                        title="Delete"
                        disabled={busyId === c.id}
                        onClick={() => remove(c)}
                      >
                        <i className="fas fa-trash" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Pager meta={meta} onPage={setPage} />
    </div>
  );
}