import { useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api/client";
import { useFetch } from "../api/useFetch";
import { PayDuesPanel, usePayConfig } from "../components/PayNow";
import { Empty, ErrorNote, fmtDate, inr, Loading, Pager, StatusPill } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import "../styles/PortalPay.css";

export function PortalDashboard() {
  const { data, loading, error, refetch } = useFetch("/portal/dashboard");
  const gateway = usePayConfig();
  if (loading) return <Loading label="Loading your account" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  const plan = data?.active_plan;
  const outstanding = Number(data?.outstanding || 0);
  return <section className="page"><div className="page-heading"><div><h1>Welcome back, {data?.customer?.first_name || "customer"}</h1><p>Manage your internet connection, invoices and support from one place.</p></div></div>
    <div className="metric-grid"><article className="metric-card"><span>Current plan</span><strong>{plan?.plan_name || "No active plan"}</strong><small>{plan?.speed_mbps ? `${plan.speed_mbps} Mbps` : "Contact support to activate"}</small></article><article className="metric-card"><span>Plan expiry</span><strong>{plan?.end_date ? fmtDate(plan.end_date) : "—"}</strong><small>{plan?.days_left === undefined ? "" : `${plan.days_left} days remaining`}</small></article><Link className={`metric-card${outstanding > 0 ? " is-due" : ""}`} to="/customer/invoices"><span>Outstanding</span><strong>{inr(outstanding)}</strong><small>{outstanding > 0 ? "Pay below, or see it bill by bill" : "Nothing to pay right now"}</small></Link></div>
    {/* The dashboard is where the customer meets the number, so it is where
        they should be able to act on it - rather than being sent to the
        invoice list to work out which bills that one figure is made of. */}
    <PayDuesPanel outstanding={outstanding} invoiceCount={data?.due_invoice_count}
                  gateway={gateway} onPaid={refetch} />
    <div className="grid-two"><Rows title="Recent invoices" rows={data?.recent_invoices} empty="You have no recent invoices." /><Rows title="Recent payments" rows={data?.recent_payments} empty="Your approved payments will appear here." /></div>
  </section>;
}

// Invoices moved to pages/PortalInvoices.jsx: that screen carries the
// pay-now flow, which needs far more than a generic read-only table.
export function PortalPayments() { return <PortalList endpoint="/portal/payments" title="Payments" columns={["receipt_no", "payment_date", "payment_mode", "amount", "status"]} />; }

function PortalList({ endpoint, title, columns }) {
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } = useFetch(endpoint, { page });
  return <section className="page"><div className="page-heading"><div><h1>{title}</h1><p>Your account history is always available here.</p></div></div><ErrorNote error={error} onRetry={refetch} /><section className="panel table-wrap">{loading ? <Loading label={`Loading ${title.toLowerCase()}`} /> : !data?.length ? <Empty title={`No ${title.toLowerCase()} yet`} /> : <table className="data"><thead><tr>{columns.map((key) => <th key={key}>{key.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{data.map((row) => <tr key={row.id}>{columns.map((key) => <td key={key}>{renderValue(key, row[key])}</td>)}</tr>)}</tbody></table>}</section><Pager meta={meta} onPage={setPage} /></section>;
}

export function PortalPlans() {
  const { data, loading, error, refetch } = useFetch("/portal/plans");
  const { data: account } = useFetch("/portal/dashboard");
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(null);
  const [message, setMessage] = useState(null);
  const [failure, setFailure] = useState(null);

  const current = account?.active_plan;

  async function act(endpoint, payload, key, describe) {
    setBusy(key);
    setMessage(null);
    setFailure(null);
    try {
      const response = await post(endpoint, payload);
      const invoice = (response?.data ?? response)?.invoice;
      setMessage(`${describe} Invoice ${invoice?.invoice_no || "created"} for `
        + `${inr(invoice?.balance ?? invoice?.total_amount)} is ready — pay it from `
        + "your invoices and the change takes effect once it clears.");
    } catch (err) {
      // The old version put the raw error message in the success slot, so a
      // failed request looked like a confirmation.
      setFailure(err.detail || err.message || "That could not be done just now.");
    } finally {
      setBusy(null);
    }
  }

  const plans = Array.isArray(data) ? data : [];
  const selected = plans.find((plan) => String(plan.id) === String(choice)) || null;
  const isCurrentChoice = Boolean(selected && current && selected.id === current.plan_id);

  // Grouped for the <optgroup>s. Plans with no type still have to appear, so
  // they fall into "Other plans" rather than being silently dropped.
  const families = Object.entries(plans.reduce((groups, plan) => {
    const family = (plan.plan_type || "").trim();
    const label = family ? family.toUpperCase() : "Other plans";
    (groups[label] ||= []).push(plan);
    return groups;
  }, {}));

  // Monthly difference against what they pay today, or null when there is
  // nothing to compare against.
  const currentPrice = Number(current?.price ?? current?.price_monthly ?? NaN);
  const difference = selected && Number.isFinite(currentPrice)
    ? Number(selected.price_monthly) - currentPrice
    : null;

  return <section className="page">
    <div className="page-heading">
      <div>
        <h1>Renew or change your plan</h1>
        <p>Renewing raises an invoice. Your service continues once it is paid.</p>
      </div>
    </div>

    <ErrorNote error={error} onRetry={refetch} />
    {message && <div className="alert success">{message}</div>}
    {failure && <div className="alert error" role="alert">{failure}</div>}

    {/* Renewing what they already have is the common case, and it was the one
        thing this screen could not do - the endpoint existed with no caller,
        so a customer wanting the same plan again had to "change" to it. */}
    {current && (
      <section className="panel current-plan">
        <div>
          <h2>Your plan: {current.plan_name}</h2>
          <p>
            {current.speed_mbps ? `${current.speed_mbps} Mbps · ` : ""}
            {current.end_date ? `Runs to ${fmtDate(current.end_date)}` : "Active"}
            {current.days_left !== undefined && ` · ${current.days_left} days left`}
          </p>
        </div>
        <button className="btn primary" disabled={busy === "renew"}
                onClick={() => act("/portal/renew", {}, "renew",
                                   "Your renewal is booked.")}>
          {busy === "renew" ? "Working…" : "Renew this plan"}
        </button>
      </section>
    )}

    <h2 className="section-title">Or move to a different plan</h2>

    {/* A dropdown rather than a wall of cards, to match how staff pick a
        package in the admin portal - one control, one decision. Grouped by
        plan family for the same reason the admin picker has an
        Unlimited / FUP toggle: the two are not comparable like for like. */}
    <section className="panel plan-choose-panel">
      {loading ? <Loading label="Loading plans" rows={2} cols={2} />
        : !plans.length ? <Empty title="No plans available"
                                 hint="Please contact the office for plan options." />
          : <>
            <div className="plan-choose-row">
              <label htmlFor="portal-plan">Select an internet plan</label>
              <select id="portal-plan" className="plan-select" value={choice}
                      onChange={(event) => setChoice(event.target.value)}>
                <option value="">Choose a plan…</option>
                {families.map(([family, items]) => (
                  <optgroup key={family} label={family}>
                    {items.map((plan) => (
                      <option key={plan.id} value={plan.id}>
                        {plan.name} — {inr(plan.price_monthly)} / {plan.validity_days} days
                        {plan.speed_mbps ? ` · ${plan.speed_mbps} Mbps` : ""}
                        {current && plan.id === current.plan_id ? "  (your current plan)" : ""}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>

            {selected && (
              <div className="plan-choose-detail">
                <dl>
                  <div><dt>Plan</dt><dd>{selected.name}</dd></div>
                  <div><dt>Speed</dt><dd>{selected.speed_mbps ? `${selected.speed_mbps} Mbps` : "—"}</dd></div>
                  <div><dt>Validity</dt><dd>{selected.validity_days} days</dd></div>
                  <div><dt>Amount</dt><dd>{inr(selected.price_monthly)}</dd></div>
                  {selected.service_provider && (
                    <div><dt>Provider</dt><dd>{selected.service_provider}</dd></div>
                  )}
                </dl>
                {/* Say what it costs relative to today BEFORE they commit.
                    A price change is the thing people are least happy to
                    discover on the invoice afterwards. */}
                {difference !== null && difference !== 0 && (
                  <p className={`plan-diff ${difference > 0 ? "up" : "down"}`}>
                    {difference > 0
                      ? `${inr(difference)} more than your current plan.`
                      : `${inr(Math.abs(difference))} less than your current plan.`}
                  </p>
                )}
              </div>
            )}

            <div className="plan-choose-actions">
              <button className="btn primary" disabled={!selected || isCurrentChoice || busy === "change"}
                      onClick={() => act("/portal/change-plan", { plan_id: selected.id },
                                         "change", `Switching to ${selected.name}.`)}>
                {busy === "change" ? "Creating invoice…" : "Change to this plan"}
              </button>
              {isCurrentChoice && (
                <span className="muted">
                  That is the plan you are already on — use Renew this plan above.
                </span>
              )}
            </div>
          </>}
    </section>
  </section>;
}

export function PortalNotifications() {
  const { data, loading, error, refetch } = useFetch("/portal/notifications");
  const [markError, setMarkError] = useState(null);
  // Unguarded, a failed POST threw out of the handler: refetch() never ran,
  // nothing on screen changed, and the customer had no way to tell the
  // difference between "marked read" and "the request died".
  async function readAll() {
    setMarkError(null);
    try {
      await post("/portal/notifications/read-all");
      refetch();
    } catch (err) {
      setMarkError(err);
    }
  }
  return <section className="page"><div className="page-heading"><div><h1>Notifications</h1><p>Important updates from your internet provider.</p></div><button className="btn" onClick={readAll}>Mark all read</button></div>{markError && <ErrorNote error={markError} onRetry={readAll} />}<section className="panel">{loading ? <Loading /> : error ? <ErrorNote error={error} onRetry={refetch} /> : !data?.length ? <Empty title="No notifications" /> : <div className="list-cards">{data.map((notice) => <article key={notice.id}><div><strong>{notice.title || "Account update"}</strong><p>{notice.body || notice.message}</p></div><small>{fmtDate(notice.created_at)}</small></article>)}</div>}</section></section>;
}

export function PortalProfile() {
  const { user, refreshProfile } = useAuth();
  const [form, setForm] = useState({ old_password: "", new_password: "", confirm_password: "" });
  const [touched, setTouched] = useState({});
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  // A curated list, so internal columns (ids, hashes, flags) never reach the
  // screen. The previous version rendered every key on the user object.
  const DETAILS = [
    ["Name", user?.full_name],
    ["Customer ID", user?.reference_id],
    ["Username", user?.username],
    ["Mobile", user?.mobile],
    ["Email", user?.email],
    ["Connection", user?.connection_type],
    ["Zone", user?.zone],
    ["Address", user?.primary_address || user?.billing_address],
  ].filter(([, value]) => value !== null && value !== undefined && value !== "");

  const errors = {};
  if (!form.old_password) errors.old_password = "Enter your current password.";
  if (form.new_password.length < 6) errors.new_password = "Use at least 6 characters.";
  else if (form.new_password === form.old_password) errors.new_password = "Choose a different password.";
  if (form.confirm_password !== form.new_password) errors.confirm_password = "The two passwords do not match.";
  const isValid = Object.keys(errors).length === 0;

  const set = (key) => (event) => {
    setForm((f) => ({ ...f, [key]: event.target.value }));
    setMessage(null);
  };
  const blur = (key) => () => setTouched((t) => ({ ...t, [key]: true }));
  const errorFor = (key) => (touched[key] ? errors[key] : undefined);

  async function submit(event) {
    event.preventDefault();
    setTouched({ old_password: true, new_password: true, confirm_password: true });
    if (!isValid || busy) return;

    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await post("/auth/customer/change-password", {
        old_password: form.old_password,
        new_password: form.new_password,
      });
      setForm({ old_password: "", new_password: "", confirm_password: "" });
      setTouched({});
      await refreshProfile();
      setMessage("Your password has been updated.");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return <section className="page">
    <div className="page-heading"><div><h1>My profile</h1><p>{user?.full_name} · {user?.mobile}</p></div></div>
    <div className="grid-two">
      <section className="panel">
        <h2>My details</h2>
        {DETAILS.length === 0
          ? <Empty title="No details on file" hint="Please contact the office to update your record." />
          : <div className="detail-grid">
              {DETAILS.map(([label, value]) => (
                <div key={label}><span>{label}</span><strong>{String(value)}</strong></div>
              ))}
            </div>}
        <p className="hint" style={{ marginTop: 12 }}>
          To correct any of these details, please contact the office.
        </p>
      </section>

      <form className="panel stack" onSubmit={submit} noValidate>
        <h2>Change password</h2>
        {message && <div className="alert success">{message}</div>}
        <ErrorNote error={error} />

        <label className={errorFor("old_password") ? "has-error" : undefined}>
          Current password
          <input type="password" autoComplete="current-password" value={form.old_password}
                 onChange={set("old_password")} onBlur={blur("old_password")} />
          {errorFor("old_password") && <small className="field-error">{errorFor("old_password")}</small>}
        </label>

        <label className={errorFor("new_password") ? "has-error" : undefined}>
          New password
          <input type="password" autoComplete="new-password" value={form.new_password}
                 onChange={set("new_password")} onBlur={blur("new_password")} />
          {errorFor("new_password")
            ? <small className="field-error">{errorFor("new_password")}</small>
            : <small>At least 6 characters.</small>}
        </label>

        <label className={errorFor("confirm_password") ? "has-error" : undefined}>
          Confirm new password
          <input type="password" autoComplete="new-password" value={form.confirm_password}
                 onChange={set("confirm_password")} onBlur={blur("confirm_password")} />
          {errorFor("confirm_password") && <small className="field-error">{errorFor("confirm_password")}</small>}
        </label>

        <button className="btn primary" disabled={busy}>
          {busy ? "Saving…" : "Change password"}
        </button>
      </form>
    </div>
  </section>;
}

function Rows({ title, rows, empty }) { return <section className="panel"><h2>{title}</h2>{!rows?.length ? <Empty title="Nothing to show" hint={empty} /> : <div className="list-cards">{rows.map((row) => <article key={row.id}><div><strong>{row.invoice_no || row.receipt_no || row.payment_mode}</strong><p>{fmtDate(row.issue_date || row.payment_date)}</p></div><div><strong>{inr(row.total_amount ?? row.amount)}</strong><StatusPill value={row.status} /></div></article>)}</div>}</section>; }
function renderValue(key, value) { if (key.includes("amount") || key === "balance") return inr(value); if (key.includes("date")) return fmtDate(value); if (key === "status") return <StatusPill value={value} />; return value || "—"; }
