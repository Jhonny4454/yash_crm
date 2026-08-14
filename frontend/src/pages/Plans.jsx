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
  const [removing, setRemoving] = useState(null);

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

  /* Remove a plan - or retire it, depending on whether anyone is on it.
   *
   * The API deliberately refuses to actually delete a plan that customers
   * are subscribed to: doing so would orphan their billing history. It
   * deactivates it instead. That is the right behaviour and the wrong thing
   * to hide, so the confirmation says which of the two is about to happen,
   * using the subscriber count the list now carries. */
  async function removePlan(plan) {
    const count = Number(plan.customer_count || 0);
    const inUse = count > 0;

    const confirmed = await confirm({
      title: inUse ? `Retire ${plan.name}?` : `Delete ${plan.name}?`,
      message: inUse
        ? `${count} customer${count === 1 ? " is" : "s are"} on this plan, so it `
          + "cannot be deleted without losing their billing history. It will be "
          + "switched off instead: existing customers keep it, and it stops "
          + "being offered to new ones."
        : "Nobody is on this plan, so it will be removed completely. "
          + "This cannot be undone.",
      confirmLabel: inUse ? "Switch it off" : "Delete",
      tone: "danger",
    });
    if (!confirmed) return;

    setRemoving(plan.id);
    try {
      const response = await del(`/plans/${plan.id}`);
      const status = (response?.data ?? response)?.status;
      toast.success(status === "deleted"
        ? `${plan.name} deleted.`
        : `${plan.name} switched off. The ${count} customer`
          + `${count === 1 ? "" : "s"} already on it are unaffected.`);
      await refetch();
    } catch (err) {
      toast.error(err.detail || readableError(err));
    } finally {
      setRemoving(null);
    }
  }

  return (
    <>
      {isAdmin && (
        <div className="toolbar">
          <button className="btn primary" onClick={() => setEditing({ ...BLANK })}>Add plan</button>
        </div>
      )}

      <ErrorNote error={error} onRetry={refetch} />

      <div className="card">
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
                    {isAdmin && <th />}</tr>
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
                        <td className="right row-actions">
                          <button className="btn sm" onClick={() => setEditing({ ...p })}>Edit</button>
                          {/* Labelled for what it will actually do. A button
                              that says Delete and quietly deactivates teaches
                              the operator not to trust the screen. */}
                          <button className="btn sm danger" disabled={removing === p.id}
                                  onClick={() => removePlan(p)}>
                            {removing === p.id ? "…"
                              : Number(p.customer_count || 0) > 0 ? "Retire" : "Delete"}
                          </button>
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
    <div style={{ position: "fixed", inset: 0, background: "rgba(15,27,45,.5)", display: "grid", placeItems: "center", zIndex: 100, padding: 16 }} onClick={onClose}>
      <form className="card" style={{ width: "100%", maxWidth: 520 }} onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <div className="card-head">
          <h2>{isNew ? "Add plan" : `Edit ${form.name}`}</h2>
          <button type="button" className="icon-btn" onClick={onClose}>✕</button>
        </div>
        <div className="card-body">
          {error && <div className="alert error">{readableError(error)}</div>}
          <div className="field">
            <label>Plan name</label>
            <input className="input" value={form.name} onChange={set("name")} required />
          </div>
          <div className="grid grid-2">
            <div className="field"><label>Plan code</label>
              <input className="input" value={form.plan_code || ""} onChange={set("plan_code")} /></div>
            <div className="field"><label>Speed (Mbps)</label>
              <MoneyInput className="input" value={form.speed_mbps} onChange={set("speed_mbps")} /></div>
            <div className="field"><label>Price per cycle</label>
              <MoneyInput className="input" value={form.price_monthly} onChange={set("price_monthly")} required /></div>
            <div className="field"><label>ISP cost</label>
              <MoneyInput className="input" value={form.isp_amount || ""} onChange={set("isp_amount")} /></div>
            <div className="field"><label>Validity (days)</label>
              <MoneyInput className="input" value={form.validity_days} onChange={set("validity_days")} /></div>
            <div className="field"><label>Plan type</label>
              <select className="input" value={form.plan_type || ""} onChange={set("plan_type")}>
                <option value="">Not set</option>
                {knownTypes.map((type) => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select></div>
            <div className="field"><label>Service provider</label>
              <select className="input" value={form.service_provider_id || ""}
                      disabled={providersLoading}
                      onChange={(e) => setForm((f) => ({
                        ...f,
                        service_provider_id: e.target.value ? Number(e.target.value) : null,
                      }))}>
                <option value="">{providersLoading ? "Loading…" : "Not set"}</option>
                {providers.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select></div>
          </div>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13.5 }}>
            <input type="checkbox" checked={!!form.is_active} onChange={set("is_active")} />
            Offer this plan to customers
          </label>
        </div>
        <div className="card-head" style={{ borderTop: "1px solid var(--line)", borderBottom: "none" }}>
          <span />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button className="btn primary" disabled={busy}>
              {busy ? <span className="spinner" /> : isNew ? "Add plan" : "Save changes"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
