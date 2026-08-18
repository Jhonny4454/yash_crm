import { useMemo, useState } from "react";
import { del, post, put } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useLookup } from "../api/useLookup";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { Empty, ErrorNote, Loading, inr, readableError } from "../components/ui";
import MoneyInput from "../components/MoneyInput";

const BLANK = {
  name: "", plan_code: "", plan_type: "", speed_mbps: 0,
  price_monthly: "", isp_amount: "", validity_days: 30, is_active: true,
};

export default function Plans() {
  const { isAdmin } = useAuth();
  const { toast, confirm } = useToast();
  const { data, loading, error, refetch } = useFetch("/plans");
  const [editing, setEditing] = useState(null);
  // { id, what } - which row is working, and which of its buttons.
  const [busy, setBusy] = useState(null);

  /* Plan type was a free text box, so "Prepaid", "prepaid" and "Prepaid "
   * were three different plan types anywhere the column is grouped or
   * filtered - and nothing on the screen told you which spelling the existing
   * plans used. The column is a plain string rather than an enum, so the list
   * is built from what is already in use plus the two conventional values,
   * and an existing plan keeps whatever it has. */
  const planTypes = useMemo(() => {
    const used = (data || []).map((p) => p.plan_type).filter(Boolean);
    return [...new Set(["Prepaid", "Postpaid", ...used])];
  }, [data]);

  /* Two different endings for a plan, and they are not the same decision.
   *
   * Retire stops a plan being sold. Everyone already on it keeps it, every
   * invoice ever raised from it still reads correctly, and it can be brought
   * back. It is the answer for a package the business has stopped offering,
   * which is nearly always what somebody means.
   *
   * Delete removes the row. Only possible while nothing points at it - a plan
   * with subscribers, past or present, cannot go without taking the meaning
   * of their billing history with it, and the server refuses.
   *
   * They used to be one button whose label changed with the subscriber count,
   * so retiring a live plan meant pressing something that said Delete on a
   * different row a moment earlier. Two buttons, each doing only what it
   * says. */
  async function retirePlan(plan) {
    const count = Number(plan.customer_count || 0);
    const bringingBack = !plan.is_active;

    if (!bringingBack) {
      const confirmed = await confirm({
        title: `Retire ${plan.name}?`,
        message: "It stops being offered on the Assign Plan and Add Customer "
          + (count > 0
            ? `screens. The ${count} customer${count === 1 ? "" : "s"} already `
              + "on it keep it, and every bill raised from it is untouched. "
            : "screens. ")
          + "You can bring it back at any time.",
        confirmLabel: "Retire",
        tone: "danger",
      });
      if (!confirmed) return;
    }

    setBusy({ id: plan.id, what: "retire" });
    try {
      await put(`/plans/${plan.id}`, { is_active: bringingBack });
      toast.success(bringingBack
        ? `${plan.name} is available again.`
        : `${plan.name} retired. It is no longer offered to new customers.`);
      await refetch();
    } catch (err) {
      toast.error(err.detail || readableError(err));
    } finally {
      setBusy(null);
    }
  }

  async function deletePlan(plan) {
    const count = Number(plan.customer_count || 0);
    if (count > 0) {
      // The button is disabled in this case; this is the keyboard path.
      toast.error(`${plan.name} cannot be deleted - ${count} customer plan`
        + `${count === 1 ? "" : "s"} reference it. Retire it instead.`);
      return;
    }

    const confirmed = await confirm({
      title: `Delete ${plan.name} permanently?`,
      message: "Nobody is on this plan and nothing has been billed from it, "
        + "so the row is removed completely. This cannot be undone. To stop "
        + "offering a plan while keeping it on old bills, retire it instead.",
      confirmLabel: "Delete permanently",
      tone: "danger",
    });
    if (!confirmed) return;

    setBusy({ id: plan.id, what: "delete" });
    try {
      await del(`/plans/${plan.id}`);
      toast.success(`${plan.name} deleted.`);
      await refetch();
    } catch (err) {
      // 409 from the server: the count on screen was stale - somebody was
      // assigned this plan between the page loading and the click.
      toast.error(err.detail || readableError(err));
      await refetch();
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      {isAdmin && (
        <div className="page-heading">
          <div>
            <h1>Plans</h1>
            <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: 2 }}>Manage service plans offered to customers.</p>
          </div>
          <button className="btn primary" onClick={() => setEditing({ ...BLANK })} style={{ borderRadius: 8, padding: "9px 18px" }}>
            <i className="fas fa-plus" aria-hidden="true" /> Add plan
          </button>
        </div>
      )}

      <ErrorNote error={error} onRetry={refetch} />

      <div className="card" style={{ borderRadius: 12, overflow: "hidden" }}>
        <div className="table-wrap">
          {loading ? <Loading label="Loading plans" />
            : !data?.length ? <Empty title="No plans yet" hint="Add your first plan to start billing." />
            : (
              <table className="data">
                <thead>
                  <tr><th>Plan</th><th>Code</th><th className="right">Speed</th>
                    <th className="right">Price</th><th className="right">ISP cost</th>
                    <th className="right">Validity</th><th>Provider</th>
                    <th className="right">Customers</th><th>Status</th>
                    {isAdmin && <th className="right" style={{ width: 180 }}>Actions</th>}</tr>
                </thead>
                <tbody>
                  {data.map((p) => (
                    <tr key={p.id} className={p.is_active ? "rail-ok" : "rail-idle"}>
                      <td><strong>{p.name}</strong></td>
                      <td className="num">{p.plan_code || "—"}</td>
                      <td className="right num">{p.speed_mbps} Mbps</td>
                      <td className="right num">{inr(p.price_monthly)}</td>
                      <td className="right num">{inr(p.isp_amount)}</td>
                      <td className="right num">{p.validity_days} days</td>
                      <td>{p.service_provider || "—"}</td>
                      <td className="right num">{p.customer_count ?? 0}</td>
                      <td><span className={`pill ${p.is_active ? "ok" : "idle"}`}>
                        {p.is_active ? "active" : "inactive"}</span></td>
                      {isAdmin && (
                        <td className="right">
                          <div className="row-actions">
                            <button className="btn sm" onClick={() => setEditing({ ...p })}
                                    style={{ borderRadius: 6 }}>Edit</button>

                            <button className="btn sm" disabled={busy?.id === p.id}
                                    onClick={() => retirePlan(p)}
                                    title={p.is_active
                                      ? "Stop offering this plan. Existing customers keep it."
                                      : "Offer this plan again."}
                                    style={{ borderRadius: 6 }}>
                              {busy?.id === p.id && busy.what === "retire" ? "…"
                                : p.is_active ? "Retire" : "Restore"}
                            </button>

                            <button className="btn sm danger"
                                    disabled={busy?.id === p.id
                                              || Number(p.customer_count || 0) > 0}
                                    onClick={() => deletePlan(p)}
                                    title={Number(p.customer_count || 0) > 0
                                      ? `${p.customer_count} customer plan(s) reference `
                                        + "this plan, so it cannot be deleted. Retire it instead."
                                      : "Remove this plan completely."}
                                    style={{ borderRadius: 6 }}>
                              {busy?.id === p.id && busy.what === "delete" ? "…" : "Delete"}
                            </button>
                          </div>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      </div>

      {editing && (
        <PlanDialog value={editing} knownTypes={planTypes} onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); refetch(); }} />
      )}
    </>
  );
}

function PlanDialog({ value, knownTypes = [], onClose, onSaved }) {
  const [form, setForm] = useState(value);
  // The Provider column on this page and in the customer's Plan tab was
  // permanently empty, and no amount of looking at the customer record
  // explained why: the form had no field for it, so service_provider_id was
  // never set on any plan.
  const { options: providers, loading: providersLoading } =
    useLookup("/service-providers", { valueKey: "id", labelKey: "name" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const isNew = !form.id;

  const set = (k) => (e) =>
    setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));

  async function save(e) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      if (isNew) await post("/plans", form);
      else await put(`/plans/${form.id}`, form);
      onSaved();
    } catch (err) { setError(err); setBusy(false); }
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <form className="card modal-card" style={{ width: "100%", maxWidth: 540, borderRadius: 16 }}
            onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <div className="card-head" style={{ padding: "16px 24px", borderBottom: "1px solid #f1f5f9" }}>
          <h2 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 700 }}>{isNew ? "Add plan" : `Edit ${form.name}`}</h2>
          <button type="button" className="icon-btn" onClick={onClose}
                  style={{ fontSize: 18, opacity: 0.5 }}>✕</button>
        </div>
        <div className="card-body" style={{ padding: "20px 24px" }}>
          {error && <div className="alert error" style={{ borderRadius: 8, marginBottom: 16 }}>{readableError(error)}</div>}
          <div className="field">
            <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Plan name</label>
            <input className="input" value={form.name} onChange={set("name")} required
                   style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} />
          </div>
          <div className="grid grid-2" style={{ gap: "16px 24px" }}>
            <div className="field"><label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Plan code</label>
              <input className="input" value={form.plan_code || ""} onChange={set("plan_code")}
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} /></div>
            <div className="field"><label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Speed (Mbps)</label>
              <MoneyInput className="input" value={form.speed_mbps} onChange={set("speed_mbps")}
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} /></div>
            <div className="field"><label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Price per cycle</label>
              <MoneyInput className="input" value={form.price_monthly} onChange={set("price_monthly")} required
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} /></div>
            <div className="field"><label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>ISP cost</label>
              <MoneyInput className="input" value={form.isp_amount || ""} onChange={set("isp_amount")}
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} /></div>
            <div className="field"><label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Validity (days)</label>
              <MoneyInput className="input" value={form.validity_days} onChange={set("validity_days")}
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} /></div>
            <div className="field"><label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Plan type</label>
              <select className="input" value={form.plan_type || ""} onChange={set("plan_type")}
                      style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }}>
                <option value="">Not set</option>
                {knownTypes.map((type) => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select></div>
            <div className="field"><label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Service provider</label>
              <select className="input" value={form.service_provider_id || ""}
                      disabled={providersLoading}
                      onChange={(e) => setForm((f) => ({
                        ...f,
                        service_provider_id: e.target.value ? Number(e.target.value) : null,
                      }))}
                      style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }}>
                <option value="">{providersLoading ? "Loading…" : "Not set"}</option>
                {providers.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select></div>
          </div>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: "0.88rem", marginTop: 16, padding: "10px 0", borderTop: "1px solid #f1f5f9" }}>
            <input type="checkbox" checked={!!form.is_active} onChange={set("is_active")} />
            Offer this plan to customers
          </label>
        </div>
        <div className="modal-foot" style={{ padding: "14px 24px", borderTop: "1px solid #f1f5f9" }}>
          <span />
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button type="button" className="btn" onClick={onClose} style={{ borderRadius: 8 }}>Cancel</button>
            <button className="btn primary" disabled={busy} style={{ borderRadius: 8 }}>
              {busy ? <span className="spinner" /> : isNew ? "Add plan" : "Save changes"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
