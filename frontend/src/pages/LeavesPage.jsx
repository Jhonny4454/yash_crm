import { useMemo, useState } from "react";
import { del, post, put } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useLookup } from "../api/useLookup";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import {
  Empty, ErrorNote, Loading, Pager, StatusPill, fmtDate, readableError,
} from "../components/ui";
import "../styles/Forms.css";

/**
 * Leave requests - list, raise, and approve or reject.
 *
 * The generic CrudPage could list these, but leave has a real workflow the
 * backend already supports (POST /hr/leaves/<id>/approve and /reject, both
 * admin-only) that nothing in the UI was calling. This screen wires it up.
 */

const STATUSES = ["pending", "approved", "rejected"];

function dayCount(from, to) {
  if (!from || !to) return null;
  const start = new Date(from);
  const end = new Date(to);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(1, Math.round((end - start) / 86400000) + 1);
}

export default function LeavesPage() {
  const { isAdmin } = useAuth();
  const { toast, confirm } = useToast();

  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [editing, setEditing] = useState(null);

  const { data, meta, loading, error, refetch } = useFetch("/hr/leaves", { status, page });
  const { byValue: staffNames } = useLookup("/staff", { labelKey: "full_name" });

  const rows = Array.isArray(data) ? data : [];

  const counts = useMemo(() => {
    const out = { pending: 0, approved: 0, rejected: 0 };
    for (const row of rows) {
      if (out[row.status] !== undefined) out[row.status] += 1;
    }
    return out;
  }, [rows]);

  async function decide(leave, approved) {
    const who = staffNames.get(String(leave.user_id)) || `Staff #${leave.user_id}`;
    const confirmed = await confirm({
      title: approved ? "Approve this leave?" : "Reject this leave?",
      message: `${who} · ${fmtDate(leave.start_date)} to ${fmtDate(leave.end_date)}.`,
      confirmLabel: approved ? "Approve" : "Reject",
      tone: approved ? "primary" : "danger",
    });
    if (!confirmed) return;

    setBusyId(leave.id);
    setActionError(null);
    try {
      await post(`/hr/leaves/${leave.id}/${approved ? "approve" : "reject"}`, {});
      toast.success(approved ? "Leave approved." : "Leave rejected.");
      refetch();
    } catch (err) {
      setActionError(err);
      toast.error(
        err.message === "forbidden"
          ? "Only an administrator can approve or reject leave."
          : readableError(err),
      );
    } finally {
      setBusyId(null);
    }
  }

  async function remove(leave) {
    const confirmed = await confirm({
      title: "Delete leave request?",
      message: "This request will be permanently removed.",
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!confirmed) return;

    setBusyId(leave.id);
    try {
      await del(`/hr/leaves/${leave.id}`);
      toast.success("Leave request deleted.");
      refetch();
    } catch (err) {
      toast.error(readableError(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="toolbar">
        <div className="filter-chips" role="group" aria-label="Filter by status">
          <button type="button" className={status === "" ? "chip is-active" : "chip"}
                  onClick={() => { setStatus(""); setPage(1); }}>
            All
          </button>
          {STATUSES.map((s) => (
            <button key={s} type="button" className={status === s ? "chip is-active" : "chip"}
                    onClick={() => { setStatus(s); setPage(1); }}>
              {s[0].toUpperCase() + s.slice(1)}
              {counts[s] > 0 && status === "" && <em className="chip-count">{counts[s]}</em>}
            </button>
          ))}
        </div>
        <button className="btn primary" onClick={() => setEditing({})}>
          <i className="fas fa-plus" aria-hidden="true" /> Request leave
        </button>
      </div>

      <ErrorNote error={error} onRetry={refetch} />
      {actionError && <div className="alert error">{readableError(actionError)}</div>}

      <div className="card">
        <div className="table-wrap">
          {loading ? (
            <Loading label="Loading leave requests" />
          ) : !rows.length ? (
            <Empty
              title={status ? `No ${status} leave requests` : "No leave requests yet"}
              hint={status ? "Try a different status filter." : "Raise the first request with the button above."}
            />
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>Staff member</th><th>From</th><th>To</th>
                  <th className="right">Days</th><th>Reason</th><th>Status</th>
                  <th className="right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((leave) => {
                  const days = dayCount(leave.start_date, leave.end_date);
                  const pending = leave.status === "pending";
                  return (
                    <tr key={leave.id}>
                      <td>{staffNames.get(String(leave.user_id)) || `#${leave.user_id}`}</td>
                      <td>{fmtDate(leave.start_date)}</td>
                      <td>{fmtDate(leave.end_date)}</td>
                      <td className="right num">{days ?? "—"}</td>
                      <td className="wrap-cell">{leave.reason || "—"}</td>
                      <td><StatusPill value={leave.status} /></td>
                      <td className="right">
                        <div className="row-actions">
                          {pending && isAdmin && (
                            <>
                              <button className="btn sm primary" disabled={busyId === leave.id}
                                      onClick={() => decide(leave, true)}>
                                Approve
                              </button>
                              <button className="btn sm danger" disabled={busyId === leave.id}
                                      onClick={() => decide(leave, false)}>
                                Reject
                              </button>
                            </>
                          )}
                          {pending && !isAdmin && <span className="muted">Awaiting admin</span>}
                          {!pending && <span className="muted">Decided</span>}
                          <button className="btn sm" onClick={() => setEditing({ ...leave })}>
                            Edit
                          </button>
                          {isAdmin && (
                            <button className="btn sm danger" disabled={busyId === leave.id}
                                    onClick={() => remove(leave)}>
                              Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <Pager meta={meta} onPage={setPage} />
      </div>

      {editing && (
        <LeaveDialog
          value={editing}
          onClose={() => setEditing(null)}
          onSaved={(wasNew) => {
            setEditing(null);
            toast.success(wasNew ? "Leave request raised." : "Leave request updated.");
            refetch();
          }}
        />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */

function LeaveDialog({ value, onClose, onSaved }) {
  const [form, setForm] = useState({
    user_id: value.user_id ?? "",
    start_date: value.start_date ?? "",
    end_date: value.end_date ?? "",
    status: value.status ?? "pending",
    reason: value.reason ?? "",
  });
  const [touched, setTouched] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const { options: staff, loading: staffLoading } = useLookup("/staff", { labelKey: "full_name" });
  const isNew = !value.id;

  const errors = {};
  if (!form.user_id) errors.user_id = "Choose a staff member.";
  if (!form.start_date) errors.start_date = "Start date is required.";
  if (!form.end_date) errors.end_date = "End date is required.";
  if (form.start_date && form.end_date && form.end_date < form.start_date) {
    errors.end_date = "The end date cannot be before the start date.";
  }
  const isValid = Object.keys(errors).length === 0;

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));
  const blur = (key) => () => setTouched((t) => ({ ...t, [key]: true }));
  const errorFor = (key) => (touched[key] ? errors[key] : undefined);

  const days = dayCount(form.start_date, form.end_date);

  async function save(event) {
    event.preventDefault();
    setTouched({ user_id: true, start_date: true, end_date: true });
    if (!isValid || busy) return;

    setBusy(true);
    setError(null);
    try {
      if (isNew) await post("/hr/leaves", form);
      else await put(`/hr/leaves/${value.id}`, form);
      onSaved(isNew);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <form className="card modal-card" style={{ maxWidth: 520 }}
            onClick={(e) => e.stopPropagation()} onSubmit={save} noValidate>
        <div className="card-head">
          <h2>{isNew ? "Request leave" : "Edit leave request"}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="card-body">
          {error && <div className="alert error">{readableError(error)}</div>}

          <div className={`field${errorFor("user_id") ? " has-error" : ""}`}>
            <label>Staff member *</label>
            <select className="input" value={form.user_id} onChange={set("user_id")}
                    onBlur={blur("user_id")} disabled={staffLoading}>
              <option value="">{staffLoading ? "Loading…" : "Select…"}</option>
              {staff.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            {errorFor("user_id") && <div className="field-error">{errorFor("user_id")}</div>}
          </div>

          <div className="grid grid-2">
            <div className={`field${errorFor("start_date") ? " has-error" : ""}`}>
              <label>From *</label>
              <input className="input" type="date" value={form.start_date}
                     onChange={set("start_date")} onBlur={blur("start_date")} />
              {errorFor("start_date") && <div className="field-error">{errorFor("start_date")}</div>}
            </div>
            <div className={`field${errorFor("end_date") ? " has-error" : ""}`}>
              <label>To *</label>
              <input className="input" type="date" value={form.end_date}
                     onChange={set("end_date")} onBlur={blur("end_date")}
                     min={form.start_date || undefined} />
              {errorFor("end_date") && <div className="field-error">{errorFor("end_date")}</div>}
            </div>
          </div>

          {days && !errors.end_date && (
            <div className="hint">{days} day{days === 1 ? "" : "s"} of leave.</div>
          )}

          <div className="field">
            <label>Status</label>
            <select className="input" value={form.status} onChange={set("status")}>
              {STATUSES.map((s) => (
                <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>
              ))}
            </select>
          </div>

          <div className="field">
            <label>Reason</label>
            <textarea className="input" rows={3} value={form.reason} onChange={set("reason")} />
          </div>
        </div>

        <div className="modal-foot">
          <span />
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" disabled={busy}>
              {busy ? <span className="spinner" /> : isNew ? "Submit request" : "Save changes"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
