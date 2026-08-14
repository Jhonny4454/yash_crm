import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useFetch } from "../api/useFetch";
import { Empty, ErrorNote, Loading, Skeleton, fmtDate, inr, inrShort } from "../components/ui";
import "../styles/DashboardBoard.css";

/**
 * Operations dashboard, laid out like templates/dashboard.html.
 *
 * Structure, top to bottom:
 *   1. quick actions
 *   2. blue metric strip - customers / plans / billing, plus a collection
 *      donut and the authorisation queue
 *   3. plan lifecycle - one chip per day for the next and last 7 days
 *   4. monthly summary table
 *   5. zone-wise outstanding and collection tabs
 *
 * Data comes from /dashboard/summary, /dashboard/zones and
 * /dashboard/monthly, and they were NOT loading independently: the early
 * return below was `if (loading) return <Loading/>`, which unmounts the whole
 * page - so MonthlySummary and ZonePanels could not mount, and could not
 * start their own requests, until the summary had come back. Four requests
 * that should overlap were two round trips deep.
 *
 * That costs nothing on a database in the same building and roughly half a
 * second on one in another datacentre, on the screen everybody opens first.
 * The page shell now renders straight away and each panel carries its own
 * skeleton.
 */
export default function AdminDashboard() {
  const { data, loading, error, refetch } = useFetch("/dashboard/summary");
  // Loaded separately and never awaited: the summary must paint immediately,
  // and a total that arrives a moment later is better than a slower dashboard.
  const { data: authDays } = useFetch("/payments/authorisation-summary");

  // Only the strip needs the summary. Errors still take over the page,
  // because a dashboard reporting nothing is worse than one reporting why.
  if (error) return <ErrorNote error={error} onRetry={refetch} />;

  const ready = Boolean(data) && !loading;
  const pendingValue = (Array.isArray(authDays) ? authDays : [])
    .reduce((sum, day) => sum + Number(day.amount || 0), 0);

  const money = data?.collections?.this_month || {};
  const invoices = data?.invoices || {};

  // Zeros everywhere usually means an empty month, not a broken endpoint -
  // but the two look identical on screen, so say which it is.
  const isQuiet = ready
    && !data.customers?.total && !data.plans?.active
    && !invoices.total_bills && !money.total && !data.outstanding;

  return (
    <>
      {isQuiet && (
        <div className="alert info" role="status" style={{ marginBottom: "1rem" }}>
          No activity recorded yet this month. These totals cover the current
          month only, so they stay at zero until the first invoice or payment
          is raised.
        </div>
      )}

      <div className="quick-actions no-print">
        <Link className="btn primary sm" to="/customers/add">
          <i className="fas fa-user-plus" aria-hidden="true" /> Add Customer
        </Link>
        <Link className="btn sm" to="/authorizations">
          <i className="fas fa-check-circle" aria-hidden="true" /> Authorize Payments
        </Link>
        <Link className="btn sm" to="/masters/bulk-messages">
          <i className="fab fa-whatsapp" aria-hidden="true" /> Bulk Messages
        </Link>
        <Link className="btn sm" to="/reports/expiring">
          <i className="fas fa-calendar-times" aria-hidden="true" /> Plan Expiry
        </Link>
      </div>

      {/* ============ BLUE METRIC STRIP ============ */}
      {/* The one block that genuinely needs /dashboard/summary. Everything
          below mounts and fetches without waiting for it. */}
      {!ready ? <div className="strip is-loading" aria-busy="true">
        <Skeleton width="100%" height={72} />
      </div> : (
      <div className="strip">
        <div>
          <div className="cap">Customers &amp; plans</div>
          <Kpi icon="fa-users" value={data.customers?.total ?? 0}
               label="Total customers" to="/customers"
               note={`${data.customers?.active ?? 0} active · ${data.customers?.new_this_month ?? 0} new this month`} />
          <Kpi icon="fa-wifi" value={data.plans?.active ?? 0}
               label="Active plans" to="/customers/plan-status"
               note={`${data.plans?.expired ?? 0} expired`} />
        </div>

        <div>
          <div className="cap">Billing this month</div>
          <Kpi icon="fa-file-invoice" value={inrShort(invoices.total_amount)}
               label={`Invoiced (${invoices.total_bills ?? 0})`} to="/invoices" />
          <Kpi icon="fa-credit-card" value={inrShort(money.total)}
               label="Collected" to="/payments" />
          <Kpi icon="fa-hourglass-half" value={inrShort(data.outstanding)}
               label="Outstanding" to="/invoices" />
        </div>

        <div>
          <h6>Collection split</h6>
          <Donut breakdown={money} />
          <div className="legend">
            <div><span className="sw sw-cash" />Cash <b>{inrShort(money.cash)}</b></div>
            <div><span className="sw sw-cheque" />Cheque <b>{inrShort(money.cheque)}</b></div>
            <div><span className="sw sw-online" />Online <b>{inrShort(money.online)}</b></div>
            <div><span className="sw sw-other" />Other <b>{inrShort(money.other)}</b></div>
          </div>
        </div>

        <div>
          <h6>Authorisation</h6>
          <div className="auth-grid">
            <div>
              <div className="n">{data.pending_authorization ?? 0}</div>
              <div>Pending<br />authorisation</div>
              {pendingValue > 0 && (
                <div className="sub">{inrShort(pendingValue)} held</div>
              )}
            </div>
            <div>
              <div className="n">{inrShort(data.collections?.today?.total)}</div>
              <div>Collected<br />today</div>
            </div>
          </div>
          <div className="divider" />

          {/* The day-wise queue was being FETCHED and then thrown away - only
              its total reached the screen - so this box showed two numbers, a
              link, and nothing to act on. The whole point of the panel is to
              say which days have money waiting on a signature. */}
          <AuthorisationQueue days={authDays} />

          <div className="strip-link">
            <Link to="/authorizations">Review payments</Link>
          </div>
        </div>
      </div>
      )}

      <PlanLifecycle plans={data?.plans} />
      <MonthlySummary />
      <ZonePanels />
    </>
  );
}

/* ------------------------------------------------------------------ */

function Kpi({ icon, value, label, note, to }) {
  const body = (
    <>
      <i className={`fas ${icon}`} aria-hidden="true" />
      <div>
        <div className="big">{value}</div>
        <div className="lbl">{label}</div>
        {note && <div className="note">{note}</div>}
      </div>
    </>
  );
  return to ? <Link className="kpi" to={to}>{body}</Link> : <div className="kpi">{body}</div>;
}

export const SPLIT_SLICES = [
  { key: "cash", label: "Cash", colour: "#e8f2fa" },
  { key: "cheque", label: "Cheque", colour: "#9ecbe8" },
  { key: "online", label: "Online", colour: "#4a9fd4" },
  { key: "other", label: "Other", colour: "#c7ddec" },
];

//: Radius whose circumference is exactly 100, so a dash length IS a percentage.
const RING_R = 15.915494;

/**
 * Collection split ring.
 *
 * This was a conic-gradient with a radial-gradient mask punching the hole.
 * Two things were wrong with that. A mask applies to an element's CHILDREN as
 * well as its background, so the transparent middle erased the amount printed
 * there - the ring rendered with an empty centre. And the gradient stops
 * accumulated float error across four slices, leaving a hairline seam where
 * the last slice met the first.
 *
 * An SVG ring has neither problem: the arcs are strokes, the label is their
 * sibling rather than a masked child, and each slice carries a <title> so
 * hovering gives the exact figure instead of guessing from the colour.
 */
function Donut({ breakdown }) {
  const parts = SPLIT_SLICES
    .map((s) => ({ ...s, value: Number(breakdown?.[s.key] || 0) }))
    .filter((s) => s.value > 0);
  const total = parts.reduce((sum, s) => sum + s.value, 0);

  let cursor = 0;
  const arcs = parts.map((s) => {
    const share = (s.value / total) * 100;
    // dashoffset counts backwards; the extra 25 rotates 0% to 12 o'clock.
    const arc = { ...s, share, offset: 25 - cursor };
    cursor += share;
    return arc;
  });

  return (
    <div className="donut-wrap">
      <svg className="donut" viewBox="0 0 42 42" role="img"
           aria-label={`Collection split this month, total ${inr(total)}`}>
        {/* Track shows through wherever nothing was collected. */}
        <circle className="donut-track" cx="21" cy="21" r={RING_R} />
        {arcs.map((a) => (
          <circle key={a.key} className="donut-arc" cx="21" cy="21" r={RING_R}
                  stroke={a.colour}
                  strokeDasharray={`${a.share} ${100 - a.share}`}
                  strokeDashoffset={a.offset}>
            <title>{`${a.label}: ${inr(a.value)} (${Math.round(a.share)}%)`}</title>
          </circle>
        ))}
        <text className="donut-value" x="21" y="20.6">{inrShort(total)}</text>
        <text className="donut-cap" x="21" y="25">this month</text>
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------------ */

/**
 * The days that have payments waiting to be signed off.
 *
 * `null` (still loading) and `[]` (nothing to review) are different states and
 * were both being rendered as blank. An empty queue is good news and should
 * say so, or the operator cannot tell it apart from a panel that is broken.
 */
function AuthorisationQueue({ days }) {
  if (days == null) {
    return <div className="auth-queue is-loading"><Skeleton width="100%" height={13} />
      <Skeleton width="80%" height={13} /></div>;
  }

  const rows = Array.isArray(days) ? days : [];
  if (!rows.length) {
    return (
      <p className="auth-queue-empty">
        Nothing waiting — every payment recorded so far has been authorised.
      </p>
    );
  }

  // Newest first, and capped: this is a summary panel, not the queue screen.
  const shown = rows.slice(0, 5);
  const hidden = rows.length - shown.length;

  return (
    <div className="auth-queue">
      {shown.map((day) => (
        <Link key={day.date} className="auth-queue-row"
              to={`/authorizations?from=${day.date}&to=${day.date}`}
              title={`Review ${day.count} payment${day.count === 1 ? "" : "s"} from ${fmtDate(day.date)}`}>
          <span className="d">{fmtDate(day.date)}</span>
          <span className="c">{day.count}</span>
          <span className="a">{inrShort(day.amount)}</span>
        </Link>
      ))}
      {hidden > 0 && (
        <Link className="auth-queue-more" to="/authorizations">
          +{hidden} more day{hidden === 1 ? "" : "s"}
        </Link>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

/**
 * The three lifecycle rows: Expired, Renewed, Expiring.
 *
 * EVERY CHIP IS AN EXACT DATE. A plan whose end date is 15 Aug appears in the
 * 15 Aug column and nowhere else — not the day before, not the day after.
 *
 * The Expired row was briefly a running backlog: today's chip held every
 * lapsed connection and each later day added the previous day's expiries, so
 * three real expiries read 3, 4, 5, 6, 7, 8, 9 and one customer appeared in
 * all seven columns. It answered a question the chips did not look like they
 * were asking.
 *
 * ONLY THE EXPIRING ROW CARRIES THE DAY-BY-DAY CHIPS - today at the left edge,
 * then one column per day to the end of the week (14 Aug ... 20 Aug). That is
 * the row where the date is the actionable part: it says which morning to make
 * which calls.
 *
 * Expired and Renewed are a headline and a link. Both count what has ALREADY
 * happened, so spreading them across seven dated columns spent most of the
 * panel restating a week that is over - and, sitting under a dated row, read
 * as a third axis to compare against when it was nothing of the kind. They
 * keep their own date span in words beside the name, their count for that
 * span, and "View all" for the whole book.
 */
function PlanLifecycle({ plans }) {
  const expiring = plans?.expiring || [];
  const expired = plans?.recently_expired || [];
  const renewed = plans?.renewed || [];
  if (!expiring.length && !expired.length && !renewed.length) return null;

  // Today, in the browser's own timezone - "en-CA" is ISO order. The server
  // now answers in Asia/Kolkata too, so these agree instead of drifting a day
  // apart between midnight and 05:30 IST.
  const todayIso = new Date().toLocaleDateString("en-CA");
  const columns = Math.max(expired.length, renewed.length, expiring.length, 7);
  const shared = { todayIso, columns };
  const spans = plans?.window || {};

  return (
    <div className="panel-card">
      <div className="panel-head">Plan lifecycle</div>
      <div className="panel-body">
        {/* Each row carries TWO numbers. The pill is the week on screen; "View
            all" is the whole book. They were the same control before, which
            read as a total and was not one: a customer who lapsed three weeks
            ago appeared in neither the chips nor the count, so the row could
            say (0) with a hundred dead connections behind it. */}
        <LifecycleRow name="Expired" days={expired} tone="danger" page="expired"
                      unit="lapsed connection" span={spans.recently_expired?.label}
                      chips={false}
                      total={plans?.expired_total ?? plans?.expired_all ?? 0}
                      allTotal={plans?.expired_all} allRange=""
                      allLabel="every expired plan" {...shared} />
        {/* Between the two: without it the panel only ever reports trouble,
            and the operator has no read on whether the chasing is working.
            Green on purpose. */}
        <LifecycleRow name="Customer renewed" days={renewed} tone="ok" page="renewed"
                      unit="customer" span={spans.renewed?.label}
                      chips={false}
                      total={plans?.renewed_total
                        ?? renewed.reduce((s, d) => s + d.count, 0)}
                      allTotal={plans?.renewed_all} allRange="?range=all"
                      allLabel="every renewal on record" {...shared} />
        <LifecycleRow name="Expiring" days={expiring} tone="warn" page="expiring"
                      unit="plan" span={spans.expiring?.label}
                      total={plans?.expiring_total
                        ?? expiring.reduce((s, d) => s + d.count, 0)}
                      allTotal={plans?.expiring_all} allRange="?range=all"
                      allLabel="every plan still to expire" {...shared} />
      </div>
    </div>
  );
}

function LifecycleRow({ name, days, total, allTotal, allRange, allLabel, tone,
                       page, unit = "plan", span, chips = true,
                       todayIso, columns }) {
  return (
    <div className={chips ? "life-row" : "life-row is-summary"}>
      <span className="name">
        {name}
        {/* Each row covers a different week - the past two look back, the
            future one looks forward - so the row has to say which. The
            dates no longer line up between rows to tell you. */}
        {span && <small className="life-span">{span}</small>}
      </span>
      <Link className={`pill-total ${tone}`} to={`/reports/${page}`}
            title={`${total} across ${span || "the week shown here"}`}>
        ({total})
      </Link>
      {Number.isFinite(allTotal) && (
        <Link className={`life-all ${tone}`}
              to={`/reports/${page}${allRange}`}
              title={`Open ${allLabel} — ${allTotal} in total`}>
          View all <strong>{allTotal}</strong>
        </Link>
      )}
      {/* Only the Expiring row draws these. They live in their own grid rather
          than being flex siblings of the label: as siblings each chip sized
          itself to its own text, so "05 Aug (0)" and "12 Aug (10)" were
          different widths and the columns never lined up. Fixed tracks fix
          that. */}
      {chips && (
      <div className="life-days" style={{ "--life-cols": columns }}>
        {days.map((day) => {
          const isToday = day.date === todayIso;
          return (
            <Link key={day.date}
                  className={`chip-day${isToday ? " is-today" : ""}`}
                  to={`/reports/${page}`}
                  title={`${day.count} ${unit}${day.count === 1 ? "" : "s"} on ${day.label}`
                    + (isToday ? " (today)" : "")}>
              <span className="day">{day.label}</span>
              <span className={`cnt${day.count ? "" : " zero"}`}>({day.count})</span>
            </Link>
          );
        })}
      </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

function MonthlySummary() {
  const { data, loading, error, refetch } = useFetch("/dashboard/monthly");
  const rows = Array.isArray(data) ? data : [];

  return (
    <div className="panel-card">
      <div className="panel-head">New customers, sales &amp; collection summary</div>
      <ErrorNote error={error} onRetry={refetch} />
      <div className="table-wrap scroll-y">
        {loading ? (
          <Loading label="Loading monthly summary" />
        ) : !rows.length ? (
          <Empty title="No billing data yet" hint="Invoices raised will be summarised here." />
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Month</th><th className="num">New clients</th>
                <th className="num">Total bills</th><th className="num">Total amount</th>
                <th className="num">Pending bills</th><th className="num">Pending amount</th>
                <th className="num">Paid bills</th><th className="num">Paid amount</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => {
                // Each month is a YYYY-MM key; build its date range once so
                // every drill-down on the row filters to the same window.
                const [year, mon] = m.month.split("-").map(Number);
                const from = `${m.month}-01`;
                const to = new Date(year, mon, 0).toISOString().slice(0, 10);
                const range = `from=${from}&to=${to}`;

                return (
                  <tr key={m.month}>
                    <td>{m.label}</td>
                    <td className="num">
                      <Drill to={`/customers?${range}&label=${encodeURIComponent(`new customers in ${m.label}`)}`}
                             value={m.new_clients} />
                    </td>
                    <td className="num">
                      <Drill to={`/invoices?${range}&label=${encodeURIComponent(`all bills for ${m.label}`)}`}
                             value={m.total_bills} />
                    </td>
                    <td className="num">{m.total_amount ? inrShort(m.total_amount) : ""}</td>
                    <td className="num">
                      <Drill to={`/invoices?status=pending&${range}&label=${encodeURIComponent(`pending bills for ${m.label}`)}`}
                             value={m.pending_bills} tone="due" />
                    </td>
                    <td className="num">
                      {m.pending_amount ? <strong className="due">{inrShort(m.pending_amount)}</strong> : ""}
                    </td>
                    <td className="num">
                      <Drill to={`/invoices?status=paid&${range}&label=${encodeURIComponent(`paid bills for ${m.label}`)}`}
                             value={m.paid_bills} />
                    </td>
                    <td className="num">{m.paid_amount ? inrShort(m.paid_amount) : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

/**
 * A count that opens the matching filtered list.
 *
 * Zero is rendered as a dash rather than a link - there is nothing to drill
 * into, and a clickable 0 that lands on an empty table reads as a bug.
 */
function Drill({ to, value, tone }) {
  const n = Number(value || 0);
  if (!n) return <span className="muted">—</span>;
  return (
    <Link className={`drill${tone ? ` ${tone}` : ""}`} to={to} title="Open this list">
      {n}
    </Link>
  );
}

function ZonePanels() {
  const [tab, setTab] = useState("outstanding");
  const { data, loading, error, refetch } = useFetch("/dashboard/zones");

  const rows = useMemo(() => {
    const source = tab === "outstanding" ? data?.outstanding : data?.collection;
    return Array.isArray(source) ? source : [];
  }, [data, tab]);

  const total = rows.reduce((sum, z) => sum + Number(z.amount || 0), 0);

  return (
    <div className="panel-card">
      <div className="panel-head">Current month — zone breakdown</div>
      <div className="zone-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "outstanding"}
                className={tab === "outstanding" ? "is-active" : ""}
                onClick={() => setTab("outstanding")}>
          Zone-wise outstanding
        </button>
        <button type="button" role="tab" aria-selected={tab === "collection"}
                className={tab === "collection" ? "is-active" : ""}
                onClick={() => setTab("collection")}>
          Zone-wise collection
        </button>
      </div>

      <ErrorNote error={error} onRetry={refetch} />
      <div className="table-wrap">
        {loading ? (
          <Loading label="Loading zones" />
        ) : !rows.length ? (
          <Empty
            title={tab === "outstanding" ? "No outstanding invoices" : "No collections this month"}
            hint={tab === "outstanding"
              ? "Every invoice is settled — nothing is pending."
              : "Approved payments will be grouped by zone here."}
          />
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Zone</th><th className="num">Count</th>
                <th className="num">Amount</th><th className="bar-col" />
              </tr>
            </thead>
            <tbody>
              {rows.map((z) => (
                <tr key={z.zone}>
                  <td>{z.zone}</td>
                  <td className="num">({z.count})</td>
                  <td className="num">
                    <strong className={tab === "outstanding" ? "due" : undefined}>{inr(z.amount)}</strong>
                  </td>
                  <td className="bar-col">
                    <span className={`zbar ${tab}`}
                          style={{ width: `${total > 0 ? Math.max(3, (z.amount / total) * 100) : 0}%` }} />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td><strong>Total</strong></td>
                <td className="num"><strong>({rows.reduce((s, z) => s + z.count, 0)})</strong></td>
                <td className="num"><strong>{inr(total)}</strong></td>
                <td />
              </tr>
            </tfoot>
          </table>
        )}
      </div>
    </div>
  );
}
