import { useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "../api/client";
import { useFetch } from "../api/useFetch";
import { BillActions, billRowProps, useBillActions } from "../components/BillLink";
import { PayDuesPanel, usePayConfig, usePayNow } from "../components/PayNow";
import { Empty, ErrorNote, fmtDate, inr, Loading, Pager, StatusPill } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useT } from "../context/LanguageContext";
import "../styles/PortalPay.css";

export function PortalDashboard() {
  const { data, loading, error, refetch } = useFetch("/portal/dashboard");
  const gateway = usePayConfig();
  const t = useT();
  if (loading) return <Loading label={t("common.loading")} />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  const plan = data?.active_plan;
  const outstanding = Number(data?.outstanding || 0);
  const daysLeft = plan?.days_left;
  const expiring = typeof daysLeft === "number" && daysLeft <= 7;

  return <section className="page">
    <div className="page-heading">
      <div>
        <h1>{t("dashboard.welcome")}, {data?.customer?.first_name || "customer"}</h1>
        <p>{t("dashboard.subtitle")}</p>
      </div>
    </div>

    <AccountStatus status={data?.account_status} />

    <div className="pt-hero">
      <section className="panel pt-hero-box">
        <h2>{t("dashboard.renew_plan")}</h2>
        <p className="pt-hero-sub">
          {plan?.plan_name || t("dashboard.no_plan")}
          {plan?.speed_mbps ? ` · ${plan.speed_mbps} Mbps` : ""}
        </p>
        <PlanCountdown plan={plan} t={t} />
        {data?.can_renew === false && data?.renewal_note
          ? <p className="pt-hero-meta">{data.renewal_note}</p>
          : <Link className="btn primary pt-hero-go" to="/customer/plans">
              {t("dashboard.renew_cta")}
            </Link>}
      </section>

      <section className={`panel pt-hero-box pt-hero-due${outstanding > 0 ? " is-due" : ""}`}>
        <h2>{t("dashboard.amount_due")}</h2>
        <p className="pt-hero-sub">{t("dashboard.amount_due_sub")}</p>
        <strong className="pt-hero-amount">{inr(outstanding)}</strong>
        {outstanding > 0
          ? <Link className="btn primary pt-hero-go" to="/customer/invoices">
              {t("dashboard.see_bills")}
            </Link>
          : <p className="pt-hero-meta">{t("dashboard.nothing_due")}</p>}
      </section>
    </div>

    <PayDuesPanel outstanding={outstanding} invoiceCount={data?.due_invoice_count}
                  gateway={gateway} onPaid={refetch} />

    <Rows title={t("dashboard.recent_payments")} rows={data?.recent_payments} limit={4}
          hint={t("dashboard.tap_payment")}
          empty={t("dashboard.no_payments")} t={t} />
  </section>;
}

// Invoices moved to pages/PortalInvoices.jsx: that screen carries the
// pay-now flow, which needs far more than a generic read-only table.
export function PortalPayments() {
  const t = useT();
  return <PortalList endpoint="/portal/payments" title={t("payments.title")} columns={["receipt_no", "payment_date", "payment_mode", "amount", "status"]} t={t} />;
}

function PortalList({ endpoint, title, columns, t }) {
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } = useFetch(endpoint, { page });
  const label = (key) => key.replaceAll("_", " ");
  // The same Print / Download sheet the Bills screen uses. A customer looking
  // at their payment history is usually looking for the one they need a
  // receipt for.
  const bill = useBillActions();

  return <section className="page">
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        <p>{t?.("payments.subtitle") || "Your account history is always available here. Tap a row to print or save its receipt."}</p>
      </div>
    </div>

    <ErrorNote error={error} onRetry={refetch} />

    <section className="panel table-wrap">
      {loading ? <Loading label={t?.("common.loading") || "Loading\u2026"} />
        : !data?.length ? <Empty title={t?.("common.nothing_to_show") || "Nothing to show"} />
          : (
            // cards-sm: one labelled card per row below 720px.
            <table className="data cards-sm">
              <thead>
                <tr>{columns.map((key) => <th key={key}>{label(key)}</th>)}</tr>
              </thead>
              <tbody>
                {data.map((row) => (
                  <tr key={row.id}
                      {...(row.status === "approved"
                        ? billRowProps(row, bill.ask) : {})}>
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

    <BillActions {...bill} />
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
  const t = useT();
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

  const renewalOff = quote?.renewal_blocked
    ? (quote.reason || t("plans.renewal_blocked"))
    : (account?.can_renew === false && account?.renewal_note) || "";

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
        <h1>{t("plans.title")}</h1>
        <p>{t("plans.subtitle")}</p>
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
            <p>{current.speed_mbps ? `${current.speed_mbps} Mbps` : t("plans.active")}</p>
          </div>
          <span className="pill info">{t("plans.your_plan")}</span>
        </div>

        <PlanCountdown plan={current} t={t} />

        {!renewalOff && <PriceLines quote={quote} fallback={current.price} t={t} />}

        {renewalOff && (
          <p className="renew-card-off" role="status">{renewalOff}</p>
        )}

        {!renewalOff && quote?.new_end_date && (
          <p className="renew-card-note">
            {t("plans.extend_to", { date: fmtDate(quote.new_end_date) })}
            {" "}{t("plans.days_not_lost")}
          </p>
        )}
        {quote?.open_invoice && (
          <p className="renew-card-note">
            {t("plans.open_invoice", { invoice_no: quote.open_invoice.invoice_no, balance: inr(quote.open_invoice.balance) })}
          </p>
        )}

        {!renewalOff && (
        <button className="btn primary renew-card-go"
                disabled={busy === "renew" || paying}
                onClick={() => (gateway?.enabled
                  ? payFor("renew", "renew", "your renewal")
                  : act("/portal/renew", {}, "renew", "renewal"))}>
          {busy === "renew" ? (gateway?.enabled ? t("plans.opening_payment") : t("plans.working"))
            : gateway?.enabled
              ? t("plans.renew_and_pay", { amount: inr(quote?.open_invoice?.balance ?? quote?.total ?? current.price) })
              : t("plans.renew_this")}
        </button>
        )}
      </section>
    )}

    {renewalOff ? null : !changing ? (
      <button type="button" className="btn plan-change-open"
              onClick={() => setChanging(true)}>
        {current ? t("plans.change_different") : t("plans.choose_plan")}
      </button>
    ) : (
      <section className="panel plan-choose-panel">
        <h2 className="section-title">
          {current ? t("plans.move_different") : t("plans.choose_plan")}
        </h2>

        {loading ? <Loading label={t("common.loading")} rows={2} cols={2} />
          : !plans.length ? <Empty title={t("plans.no_plans")}
                                   hint={t("plans.contact_office")} />
            : <>
              <div className="plan-choose-row">
                <label htmlFor="portal-plan">{t("plans.select_plan")}</label>
                {/* A native <select>: on a phone it opens the OS picker,
                    which beats a column of cards one-handed. Grouped by plan
                    family because Unlimited and FUP are not comparable like
                    for like. */}
                <select id="portal-plan" className="plan-select" value={choice}
                        onChange={(event) => setChoice(event.target.value)}>
                  <option value="">{t("plans.choose_plan_label")}</option>
                  {families.map(([family, items]) => (
                    <optgroup key={family} label={family}>
                      {items.map((plan) => (
                        <option key={plan.id} value={plan.id}>
                          {plan.name} — {inr(plan.total ?? plan.price_monthly)}
                          {" / "}{plan.validity_days} {t("common.days")}
                          {plan.speed_mbps ? ` · ${plan.speed_mbps} Mbps` : ""}
                          {current && plan.id === current.plan_id ? `  ${t("common.current_plan")}` : ""}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </div>

              {selected && (
                <div className="plan-choose-detail">
                  <dl>
                    <div><dt>{t("plans.plan")}</dt><dd>{selected.name}</dd></div>
                    <div><dt>{t("plans.speed")}</dt><dd>{selected.speed_mbps ? `${selected.speed_mbps} Mbps` : "\u2014"}</dd></div>
                    <div><dt>{t("plans.validity")}</dt><dd>{selected.validity_days} {t("common.days")}</dd></div>
                    <div><dt>{t("plans.plan_price")}</dt><dd>{inr(selected.price ?? selected.price_monthly)}</dd></div>
                    {Number(selected.tax_amount) > 0 && (
                      <div>
                        <dt>{t("common.gst")} {selected.tax_percent}%</dt>
                        <dd>{selected.tax_mode === "include"
                          ? `${inr(selected.tax_amount)} ${t("common.included")}`
                          : `+ ${inr(selected.tax_amount)}`}</dd>
                      </div>
                    )}
                    <div><dt>{t("plans.you_pay")}</dt>
                      <dd><strong>{inr(selected.total ?? selected.price_monthly)}</strong></dd></div>
                    {selected.service_provider && (
                      <div><dt>{t("plans.provider")}</dt><dd>{selected.service_provider}</dd></div>
                    )}
                  </dl>
                  {difference !== null && difference !== 0 && (
                    <p className={`plan-diff ${difference > 0 ? "up" : "down"}`}>
                      {difference > 0
                        ? t("plans.more_than_current", { amount: inr(difference) })
                        : t("plans.less_than_current", { amount: inr(Math.abs(difference)) })}
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
                  {busy === "change" ? (gateway?.enabled ? t("plans.opening_payment") : t("plans.working"))
                    : !selected ? t("plans.change_to_this")
                      : gateway?.enabled
                        ? t("plans.change_and_pay", { amount: inr(selected.total ?? selected.price_monthly) })
                        : t("plans.change_to_this", { amount: inr(selected.total ?? selected.price_monthly) })}
                </button>
                <button type="button" className="btn"
                        onClick={() => { setChanging(false); setChoice(""); }}>
                  {t("plans.cancel")}
                </button>
                {isCurrentChoice && (
                  <span className="muted">
                    {t("plans.already_on")}
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
export function PlanCountdown({ plan, t: _t }) {
  const _tFn = _t || (k => k);
  const days = plan?.days_left;
  if (typeof days !== "number") {
    return <p className="pt-hero-meta">{_tFn("countdown.not_active")}</p>;
  }

  const start = plan.start_date ? new Date(plan.start_date) : null;
  const end = plan.end_date ? new Date(plan.end_date) : null;
  const total = start && end
    ? Math.max(1, Math.round((end - start) / 86_400_000))
    : null;
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
          ? (Math.abs(days) === 1 ? _tFn("countdown.day_ago") : _tFn("countdown.days_ago"))
          : (days === 1 ? _tFn("countdown.day_left") : _tFn("countdown.days_left"))}</span>
      </p>

      {percent !== null && (
        <div className="pt-countdown-bar" role="progressbar" aria-valuenow={percent}
             aria-valuemin={0} aria-valuemax={100}>
          <i style={{ width: `${percent}%` }} />
        </div>
      )}

      <p className="pt-countdown-note">
        {expired
          ? _tFn("countdown.expired")
          : _tFn("countdown.runs_to", { date: fmtDate(plan.end_date) })}
      </p>
    </div>
  );
}

/** Plan price, GST and total — or just the price when no tax applies. */
function PriceLines({ quote, fallback, t }) {
  const total = quote?.total ?? fallback;
  const tax = Number(quote?.tax_amount || 0);
  const included = quote?.tax_mode === "include";

  return (
    <dl className="renew-price">
      <div><dt>{t?.("common.plan_price") || "Plan price"}</dt><dd>{inr(quote?.price ?? fallback)}</dd></div>
      {tax > 0 && (
        <div>
          <dt>{t?.("common.gst") || "GST"} {quote.tax_percent}%</dt>
          <dd>{included ? `${inr(tax)} ${t?.("common.included") || "(included)"}` : `+ ${inr(tax)}`}</dd>
        </div>
      )}
      <div className="is-total"><dt>{t?.("common.you_pay") || "You pay"}</dt><dd>{inr(total)}</dd></div>
    </dl>
  );
}

export function PortalNotifications() {
  const { data, loading, error, refetch } = useFetch("/portal/notifications");
  const t = useT();
  const [markError, setMarkError] = useState(null);
  async function readAll() {
    setMarkError(null);
    try {
      await post("/portal/notifications/read-all");
      refetch();
    } catch (err) {
      setMarkError(err);
    }
  }
  return <section className="page"><div className="page-heading"><div><h1>{t("notifications.title")}</h1><p>{t("notifications.subtitle")}</p></div><button className="btn" onClick={readAll}>{t("notifications.mark_all")}</button></div>{markError && <ErrorNote error={markError} onRetry={readAll} />}<section className="panel">{loading ? <Loading /> : error ? <ErrorNote error={error} onRetry={refetch} /> : !data?.length ? <Empty title={t("notifications.no_notifications")} /> : <div className="list-cards">{data.map((notice) => <article key={notice.id}><div><strong>{notice.title || t("notifications.account_update")}</strong><p>{notice.body || notice.message}</p></div><small>{fmtDate(notice.created_at)}</small></article>)}</div>}</section></section>;
}

export function PortalProfile() {
  const { user, refreshProfile } = useAuth();
  const t = useT();
  const { data: account } = useFetch("/portal/dashboard");
  const [form, setForm] = useState({ old_password: "", new_password: "", confirm_password: "" });
  const [touched, setTouched] = useState({});
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const DETAILS = [
    [t("profile.detail.name"), user?.full_name],
    [t("profile.detail.customer_id"), user?.reference_id],
    [t("profile.detail.username"), user?.username],
    [t("profile.detail.mobile"), user?.mobile],
    [t("profile.detail.email"), user?.email],
    [t("profile.detail.connection"), user?.connection_type],
    [t("profile.detail.address"), user?.primary_address || user?.billing_address],
    [t("profile.detail.status"), account?.account_status?.label],
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
    <div className="page-heading"><div><h1>{t("profile.title")}</h1><p>{user?.full_name} · {user?.mobile}</p></div></div>
    <div className="grid-two">
      <section className="panel">
        <h2>{t("profile.my_details")}</h2>
        {DETAILS.length === 0
          ? <Empty title={t("profile.no_details")} hint={t("profile.no_details_hint")} />
          : <div className="detail-grid">
              {DETAILS.map(([label, value]) => (
                <div key={label}><span>{label}</span><strong>{String(value)}</strong></div>
              ))}
            </div>}
        <p className="hint" style={{ marginTop: 12 }}>
          {t("profile.correct_details")}
        </p>
      </section>

      <form className="panel stack" onSubmit={submit} noValidate>
        <h2>{t("profile.change_password")}</h2>
        {message && <div className="alert success">{message}</div>}
        <ErrorNote error={error} />

        <label className={errorFor("old_password") ? "has-error" : undefined}>
          {t("profile.current_password")}
          <input type="password" autoComplete="current-password" value={form.old_password}
                 onChange={set("old_password")} onBlur={blur("old_password")} />
          {errorFor("old_password") && <small className="field-error">{errorFor("old_password")}</small>}
        </label>

        <label className={errorFor("new_password") ? "has-error" : undefined}>
          {t("profile.new_password")}
          <input type="password" autoComplete="new-password" value={form.new_password}
                 onChange={set("new_password")} onBlur={blur("new_password")} />
          {errorFor("new_password")
            ? <small className="field-error">{errorFor("new_password")}</small>
            : <small>{t("profile.new_password_hint")}</small>}
        </label>

        <label className={errorFor("confirm_password") ? "has-error" : undefined}>
          {t("profile.confirm_password")}
          <input type="password" autoComplete="new-password" value={form.confirm_password}
                 onChange={set("confirm_password")} onBlur={blur("confirm_password")} />
          {errorFor("confirm_password") && <small className="field-error">{errorFor("confirm_password")}</small>}
        </label>

        <button className="btn primary" disabled={busy}>
          {busy ? t("profile.saving") : t("profile.save_cta")}
        </button>
      </form>
    </div>
  </section>;
}

/**
 * A short list of invoices or payments. Every row opens its own document.
 *
 * A bill opens the bill; a payment opens its receipt - the same Print /
 * Download sheet the Bills screen uses, so proof of payment is a tap away
 * instead of a phone call to the office.
 *
 * Which document is decided by `docFor`, on the payment_date: a payment also
 * carries the invoice_no of the bill it settled, so the invoice number cannot
 * tell them apart. Getting that wrong once meant every payment row here asked
 * for the INVOICE whose id matched the payment's - somebody else's bill, or
 * nothing at all.
 *
 * A payment still waiting to be checked is not offered: there is no receipt
 * for money the office has not confirmed, and a document that says otherwise
 * would be worse than none.
 */
function Rows({ title, rows, empty, limit, hint, t: _t }) {
  const tFn = _t || (k => k);
  const bill = useBillActions();
  const shown = limit ? (rows || []).slice(0, limit) : rows;

  return (
    <section className="panel">
      <div className="rows-head">
        <h2>{title}</h2>
        {hint && <span className="rows-hint">{hint}</span>}
      </div>
      {!shown?.length ? <Empty title={tFn("common.nothing_to_show")} hint={empty} /> : (
        <div className="list-cards">
          {shown.map((row) => {
            const isBill = !row.payment_date;
            const openable = isBill || row.status === "approved";
            return (
              <article key={row.id}
                       {...(openable ? billRowProps(row, bill.ask) : {})}>
                <div>
                  <strong>
                    {isBill
                      ? row.invoice_no
                      : row.receipt_no || row.payment_mode || tFn("common.payment")}
                  </strong>
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
