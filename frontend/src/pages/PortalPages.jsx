import { useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api/client";
import { useFetch } from "../api/useFetch";
import { BillActions, billRowProps, useBillActions } from "../components/BillLink";
import { PayDuesPanel, usePayConfig, usePayNow } from "../components/PayNow";
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
  // Ordered by what a customer opened the app to find out: is my connection
  // about to stop, and do I owe anything. The money card comes first on a
  // phone because it is the only one that needs an action.
  const daysLeft = plan?.days_left;
  const expiring = typeof daysLeft === "number" && daysLeft <= 7;

  return <section className="page">
    <div className="page-heading">
      <div>
        <h1>Welcome back, {data?.customer?.first_name || "customer"}</h1>
        <p>Your connection, your bills and your payments, in one place.</p>
      </div>
    </div>

    <AccountStatus status={data?.account_status} />

    {/* The two things a customer opens this screen for, side by side: what
        keeps the connection running, and what it costs. Two boxes on a desktop
        or a tablet, stacked on a phone with the money first - on a small
        screen the one that needs an action should not be below the fold. */}
    <div className="pt-hero">
      <section className="panel pt-hero-box">
        <h2>Renew your plan</h2>
        <p className="pt-hero-sub">
          {plan?.plan_name || "No active plan"}
          {plan?.speed_mbps ? ` · ${plan.speed_mbps} Mbps` : ""}
        </p>
        <PlanCountdown plan={plan} />
        <Link className="btn primary pt-hero-go" to="/customer/plans">
          Renew plan
        </Link>
      </section>

      <section className={`panel pt-hero-box pt-hero-due${outstanding > 0 ? " is-due" : ""}`}>
        <h2>Total amount due</h2>
        <p className="pt-hero-sub">Total amount due till date</p>
        <strong className="pt-hero-amount">{inr(outstanding)}</strong>
        {outstanding > 0
          ? <Link className="btn primary pt-hero-go" to="/customer/invoices">
              See the bills
            </Link>
          : <p className="pt-hero-meta">Nothing to pay right now.</p>}
      </section>
    </div>

    {/* The dashboard is where the customer meets the number, so it is where
        they should be able to act on it - rather than being sent to the
        invoice list to work out which bills that one figure is made of. */}
    <PayDuesPanel outstanding={outstanding} invoiceCount={data?.due_invoice_count}
                  gateway={gateway} onPaid={refetch} />

    <div className="grid-two">
      <Rows title="Recent invoices" rows={data?.recent_invoices}
            empty="You have no recent invoices." />
      <Rows title="Recent payments" rows={data?.recent_payments}
            empty="Your approved payments will appear here." />
    </div>
  </section>;
}

// Invoices moved to pages/PortalInvoices.jsx: that screen carries the
// pay-now flow, which needs far more than a generic read-only table.
export function PortalPayments() { return <PortalList endpoint="/portal/payments" title="Payments" columns={["receipt_no", "payment_date", "payment_mode", "amount", "status"]} />; }

function PortalList({ endpoint, title, columns }) {
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } = useFetch(endpoint, { page });
  const label = (key) => key.replaceAll("_", " ");

  return <section className="page">
    <div className="page-heading">
      <div><h1>{title}</h1><p>Your account history is always available here.</p></div>
    </div>

    <ErrorNote error={error} onRetry={refetch} />

    <section className="panel table-wrap">
      {loading ? <Loading label={`Loading ${title.toLowerCase()}`} />
        : !data?.length ? <Empty title={`No ${title.toLowerCase()} yet`} />
          : (
            // cards-sm: one labelled card per row below 720px.
            <table className="data cards-sm">
              <thead>
                <tr>{columns.map((key) => <th key={key}>{label(key)}</th>)}</tr>
              </thead>
              <tbody>
                {data.map((row) => (
                  <tr key={row.id}>
                    {columns.map((key) => (
                      <td key={key} data-label={label(key)}>
                        {renderValue(key, row[key])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
    </section>

    <Pager meta={meta} onPage={setPage} />
  </section>;
}

/**
 * Renew, or change plan. Two decisions, and the screen keeps them apart.
 *
 * Renew is the one people came for: the same plan again, at a price the server
 * has already worked out including GST, with nothing to choose. Moving to a
 * different package is a separate step behind its own button, because a plan
 * list sitting open next to a Renew button is an invitation to change plan by
 * accident - and a change closes the current plan record.
 */
export function PortalPlans() {
  const { data, loading, error, refetch } = useFetch("/portal/plans");
  const { data: account, refetch: refetchAccount } = useFetch("/portal/dashboard");
  const { data: quote, refetch: refetchQuote } = useFetch("/portal/renew/quote");
  const [choice, setChoice] = useState("");
  const [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState(null);
  const [message, setMessage] = useState(null);
  const [failure, setFailure] = useState(null);

  const gateway = usePayConfig();
  const { busy: paying, pay } = usePayNow({
    gateway,
    onPaid: async () => {
      refetch();
      refetchQuote();
      refetchAccount();
    },
  });

  const current = account?.active_plan;

  /**
   * Pay first, bill afterwards - when there is a gateway to pay through.
   *
   * `intent` tells the server what the money is FOR ("renew", or
   * "change:<planId>"). The invoice is written when the payment lands, so a
   * customer who opens the checkout and closes it leaves nothing behind: no
   * bill in their list, no due on their dashboard, and no spent invoice
   * number. Raising it up front and deleting it afterwards was a narrower
   * version of the same idea with more ways to go wrong.
   *
   * With no gateway there is nothing to pay through, so the old path stands:
   * raise the bill and tell them where to settle it.
   */
  async function payFor(intent, key, what) {
    setBusy(key);
    setMessage(null);
    setFailure(null);
    try {
      await pay({ intent, describe: what });
      setChanging(false);
    } finally {
      setBusy(null);
    }
  }

  /**
   * The no-gateway path: raise the bill, then say where to settle it.
   *
   * Only reachable when online payment is switched off. With a gateway the
   * screen uses payFor() above, and no invoice exists until the money does.
   */
  async function act(endpoint, payload, key, what) {
    setBusy(key);
    setMessage(null);
    setFailure(null);

    let invoice;
    let reused = false;
    try {
      const response = await post(endpoint, payload);
      const result = response?.data ?? response;
      invoice = result?.invoice;
      // A renewal points at an already-open bill rather than stacking a second
      // one. That bill existed before this click, so backing out of the
      // checkout must not remove it.
      reused = Boolean(result?.reused_open_invoice);
      setChanging(false);
    } catch (err) {
      // The old version put the raw error message in the success slot, so a
      // failed request looked like a confirmation.
      setFailure(err.detail || err.message || "That could not be done just now.");
      return;
    } finally {
      setBusy(null);
    }

    const due = Number(invoice?.balance ?? invoice?.total_amount ?? 0);
    const reference = invoice?.invoice_no || "";

    if (gateway?.enabled && invoice?.id && due > 0) {
      const outcome = await pay({
        invoiceId: invoice.id, amount: due, describe: reference || what,
      });

      /* Cancelling the checkout must not leave the customer owing money for a
       * renewal they just declined. The bill is raised before the checkout so
       * the payment has something to attach to, but if nothing was paid there
       * is nothing to keep - the plan dates are only extended when the money
       * lands, so withdrawing the bill undoes the whole thing.
       *
       * Deliberately NOT withdrawn on "pending" or "unknown": money may be in
       * flight and a bill is the recoverable direction to be wrong in. The
       * server checks with Cashfree before deleting anything regardless, so a
       * payment that did go through can never lose its bill here.
       */
      const abandoned = ["abandoned", "cancelled", "failed", "expired",
                         "order_failed", "unopened"].includes(outcome);
      if (abandoned && !reused) {
        try {
          await post(`/portal/invoices/${invoice.id}/discard`);
          refetchQuote();
          refetchAccount();
        } catch {
          // The bill stays. Saying so is better than a silent inconsistency
          // between what they were told and what their account shows.
          setMessage(`Invoice ${reference} is still on your account — `
            + "pay it when you are ready, or call the office to cancel it.");
        }
      }
      return;
    }

    // Only when there is no checkout to send them to. Then the bill IS the
    // deliverable, and where to pay it is the useful thing to say.
    setMessage(`Invoice ${reference || "created"} for ${inr(due)} is ready — `
      + `pay it by bank transfer or at the office, and your ${what} takes `
      + "effect once it clears.");
    refetchQuote();
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

    {/* ---------------------------------------------------------- renew --
        The same plan again. One button, one price, nothing to choose. */}
    {current && (
      <section className="panel renew-card">
        <div className="renew-card-head">
          <div>
            <h2>{current.plan_name}</h2>
            <p>{current.speed_mbps ? `${current.speed_mbps} Mbps` : "Active"}</p>
          </div>
          <span className="pill info">Your plan</span>
        </div>

        {/* The same counter as the dashboard, on the screen where the renewal
            decision is actually made. */}
        <PlanCountdown plan={current} />

        {/* The bill, itemised, before they commit to it. The server does the
            GST arithmetic - the same function the counter uses - so the
            figure here is the figure on the invoice. */}
        <PriceLines quote={quote} fallback={current.price} />

        {quote?.new_end_date && (
          <p className="renew-card-note">
            Renewing extends your plan to <strong>{fmtDate(quote.new_end_date)}</strong>.
            Days you have already paid for are not lost.
          </p>
        )}
        {quote?.open_invoice && (
          <p className="renew-card-note">
            You already have invoice {quote.open_invoice.invoice_no} open for{" "}
            {inr(quote.open_invoice.balance)} — renewing points at that bill
            rather than raising a second one.
          </p>
        )}

        {/* The label says what the button does, in full. "Renew this plan"
            gave no warning that a payment window was about to open, and a
            checkout nobody expected is a checkout people close. */}
        <button className="btn primary renew-card-go"
                disabled={busy === "renew" || paying}
                onClick={() => (gateway?.enabled
                  ? payFor("renew", "renew", "your renewal")
                  : act("/portal/renew", {}, "renew", "renewal"))}>
          {busy === "renew" ? (gateway?.enabled ? "Opening payment…" : "Working…")
            : gateway?.enabled
              /* The charge is the OPEN invoice when there is one, because
                 /portal/renew points at that bill instead of raising a second.
                 Quoting the plan price on the button and then charging a
                 different figure is the fastest way to lose someone's trust
                 at the exact moment they are paying. */
              ? `Renew & pay ${inr(quote?.open_invoice?.balance
                                   ?? quote?.total ?? current.price)}`
              : "Renew this plan"}
        </button>
      </section>
    )}

    {/* --------------------------------------------------------- change --
        Behind its own button. A plan list sitting open beside Renew is how
        people change plan without meaning to. */}
    {!changing ? (
      <button type="button" className="btn plan-change-open"
              onClick={() => setChanging(true)}>
        {current ? "Change to a different plan" : "Choose a plan"}
      </button>
    ) : (
      <section className="panel plan-choose-panel">
        <h2 className="section-title">
          {current ? "Move to a different plan" : "Choose a plan"}
        </h2>

        {loading ? <Loading label="Loading plans" rows={2} cols={2} />
          : !plans.length ? <Empty title="No plans available"
                                   hint="Please contact the office for plan options." />
            : <>
              <div className="plan-choose-row">
                <label htmlFor="portal-plan">Select an internet plan</label>
                {/* A native <select>: on a phone it opens the OS picker,
                    which beats a column of cards one-handed. Grouped by plan
                    family because Unlimited and FUP are not comparable like
                    for like. */}
                <select id="portal-plan" className="plan-select" value={choice}
                        onChange={(event) => setChoice(event.target.value)}>
                  <option value="">Choose a plan…</option>
                  {families.map(([family, items]) => (
                    <optgroup key={family} label={family}>
                      {items.map((plan) => (
                        <option key={plan.id} value={plan.id}>
                          {plan.name} — {inr(plan.total ?? plan.price_monthly)}
                          {" / "}{plan.validity_days} days
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
                    <div><dt>Plan price</dt><dd>{inr(selected.price ?? selected.price_monthly)}</dd></div>
                    {Number(selected.tax_amount) > 0 && (
                      <div>
                        <dt>GST {selected.tax_percent}%</dt>
                        <dd>{selected.tax_mode === "include"
                          ? `${inr(selected.tax_amount)} (included)`
                          : `+ ${inr(selected.tax_amount)}`}</dd>
                      </div>
                    )}
                    <div><dt>You pay</dt>
                      <dd><strong>{inr(selected.total ?? selected.price_monthly)}</strong></dd></div>
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
                <button className="btn primary"
                        disabled={!selected || isCurrentChoice || busy === "change" || paying}
                        onClick={() => (gateway?.enabled
                          ? payFor(`change:${selected.id}`, "change",
                                   `your move to ${selected.name}`)
                          : act("/portal/change-plan", { plan_id: selected.id },
                                "change", "plan change"))}>
                  {busy === "change" ? (gateway?.enabled ? "Opening payment…" : "Working…")
                    : !selected ? "Change to this plan"
                      : gateway?.enabled
                        ? `Change & pay ${inr(selected.total ?? selected.price_monthly)}`
                        : `Change to this plan — ${inr(selected.total ?? selected.price_monthly)}`}
                </button>
                <button type="button" className="btn"
                        onClick={() => { setChanging(false); setChoice(""); }}>
                  Cancel
                </button>
                {isCurrentChoice && (
                  <span className="muted">
                    That is the plan you are already on — use Renew this plan above.
                  </span>
                )}
              </div>
            </>}
      </section>
    )}
  </section>;
}

/**
 * The state of the connection, said plainly.
 *
 * A disabled customer can now sign in, which is the right call - they need to
 * see what they owe and pay it - but it means somebody can arrive at a working
 * portal while their internet is dead. Without this they would read "Active
 * plan, 200 days left" beside a connection that does not work and conclude the
 * portal is lying to them.
 *
 * The state and its explanation both come from the server, so the portal and
 * the office never describe the same account differently.
 */
export function AccountStatus({ status }) {
  if (!status?.code) return null;
  // Nothing worth a banner when everything is fine; the plan card already
  // says so, and a green bar on every visit is a green bar nobody reads.
  if (status.code === "active") return null;

  return (
    <section className={`pt-status is-${status.tone || "idle"}`} role="status">
      <span className="pt-status-tag">{status.label}</span>
      <p>{status.detail}</p>
    </section>
  );
}

/**
 * How long the plan has left, as a number rather than a sentence.
 *
 * "Runs to 16 Aug 2027" asks the customer to do date arithmetic to answer the
 * only question they came with - is my internet about to stop. The count is
 * the answer, so it is the biggest thing in the box, and the bar underneath
 * puts it in proportion: 30 days left means something different on a monthly
 * plan than on an annual one.
 *
 * Everything here comes off `start_date`, `end_date` and `days_left`, which
 * the dashboard already returns - there is no new call behind this.
 */
export function PlanCountdown({ plan }) {
  const days = plan?.days_left;
  if (typeof days !== "number") {
    return <p className="pt-hero-meta">Not active</p>;
  }

  const start = plan.start_date ? new Date(plan.start_date) : null;
  const end = plan.end_date ? new Date(plan.end_date) : null;
  const total = start && end
    ? Math.max(1, Math.round((end - start) / 86_400_000))
    : null;
  // Clamped: a plan renewed early can report more days left than its own
  // length, which would otherwise draw a negative-width bar.
  const percent = total === null ? null
    : Math.min(100, Math.max(0, Math.round(((total - days) / total) * 100)));

  const expired = days <= 0;
  const tone = expired ? "is-over"
    : days <= 7 ? "is-soon"
      : days <= 30 ? "is-warn" : "is-ok";

  return (
    <div className={`pt-countdown ${tone}`}>
      <p className="pt-countdown-num">
        <strong>{Math.abs(days)}</strong>
        <span>{expired
          ? `day${Math.abs(days) === 1 ? "" : "s"} ago`
          : `day${days === 1 ? "" : "s"} left`}</span>
      </p>

      {percent !== null && (
        <div className="pt-countdown-bar" role="progressbar" aria-valuenow={percent}
             aria-valuemin={0} aria-valuemax={100}
             aria-label="How much of your plan period has been used">
          <i style={{ width: `${percent}%` }} />
        </div>
      )}

      <p className="pt-countdown-note">
        {expired
          ? "Your plan has expired — renew to stay connected."
          : `Runs to ${fmtDate(plan.end_date)}.`}
      </p>
    </div>
  );
}

/** Plan price, GST and total — or just the price when no tax applies. */
function PriceLines({ quote, fallback }) {
  const total = quote?.total ?? fallback;
  const tax = Number(quote?.tax_amount || 0);
  const included = quote?.tax_mode === "include";

  return (
    <dl className="renew-price">
      <div><dt>Plan price</dt><dd>{inr(quote?.price ?? fallback)}</dd></div>
      {tax > 0 && (
        <div>
          <dt>GST {quote.tax_percent}%</dt>
          <dd>{included ? `${inr(tax)} (included)` : `+ ${inr(tax)}`}</dd>
        </div>
      )}
      <div className="is-total"><dt>You pay</dt><dd>{inr(total)}</dd></div>
    </dl>
  );
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
  // The same verdict as the dashboard, from the same place. A customer who
  // came here to check their details should not have to go back to the home
  // screen to find out whether their line is on.
  const { data: account } = useFetch("/portal/dashboard");
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
    ["Status", account?.account_status?.label],
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

/**
 * A short list of invoices or payments.
 *
 * Invoice rows open the bill; payment rows do not, because there is no
 * document behind a payment row to open. Same component, and the difference is
 * decided per row by whether it carries an invoice_no - a row that looks
 * clickable and is not is worse than one that plainly is not.
 */
function Rows({ title, rows, empty }) {
  const bill = useBillActions();

  return (
    <section className="panel">
      <h2>{title}</h2>
      {!rows?.length ? <Empty title="Nothing to show" hint={empty} /> : (
        <div className="list-cards">
          {rows.map((row) => {
            const isBill = Boolean(row.invoice_no);
            return (
              <article key={row.id}
                       {...(isBill ? billRowProps(row, bill.ask) : {})}>
                <div>
                  <strong>{row.invoice_no || row.receipt_no || row.payment_mode}</strong>
                  <p>{fmtDate(row.issue_date || row.payment_date)}</p>
                </div>
                <div>
                  <strong>{inr(row.total_amount ?? row.amount)}</strong>
                  <StatusPill value={row.status} />
                </div>
              </article>
            );
          })}
        </div>
      )}
      <BillActions {...bill} />
    </section>
  );
}
function renderValue(key, value) { if (key.includes("amount") || key === "balance") return inr(value); if (key.includes("date")) return fmtDate(value); if (key === "status") return <StatusPill value={value} />; return value || "—"; }
