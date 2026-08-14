import { useEffect, useMemo, useRef, useState } from "react";
import { get, post } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useLookup } from "../api/useLookup";
import { useToast } from "../context/ToastContext";
import {
  Empty, ErrorNote, Loading, Pager, fmtDate, readableError,
} from "../components/ui";
import "../styles/Forms.css";

/**
 * Bulk WhatsApp / reminders.
 *
 * Replaces masters/bulk_messages.html. POST /messages/bulk and
 * GET /messages/log existed with no React screen, so this was another
 * module you could not reach from the SPA.
 *
 * Sending is irreversible and hits every matching customer, so the flow is
 * deliberately two-step: compose, then confirm against the recipient count.
 *
 * The send itself runs on the server in the background. It used to run inside
 * the request: two hundred recipients meant a request that ran for minutes,
 * a browser that gave up before it finished - so the operator pressed Send
 * again - and a held thread that made every other screen in the CRM slow
 * while it ran. Now the request returns at once with a job id and this screen
 * follows the progress.
 */

const AUDIENCES = [
  { value: "all_active", label: "All active customers",
    hint: "Everyone with an active account." },
  { value: "expiring_7", label: "Plans expiring in 7 days",
    hint: "Active plans ending within a week." },
  { value: "expired", label: "Expired plans",
    hint: "Active accounts whose plan end date has passed." },
  { value: "unpaid", label: "Customers with unpaid invoices",
    hint: "Anyone with an outstanding balance." },
];

// Placeholders services/messaging.py resolves per customer.
const TOKENS = [
  ["{{name}}", "Customer's full name"],
  ["{{first_name}}", "First name only"],
  ["{{mobile}}", "Mobile number"],
  ["{{plan}}", "Current plan name"],
  ["{{expiry}}", "Plan expiry date"],
  ["{{outstanding}}", "Amount outstanding"],
];

const MAX_LENGTH = 1000;

export default function BulkMessages() {
  const { toast, confirm } = useToast();

  const [audience, setAudience] = useState("all_active");
  const [zone, setZone] = useState("");
  const [message, setMessage] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [job, setJob] = useState(null);
  const [logKey, setLogKey] = useState(0);
  const jobId = useRef(null);

  /* Follow a running send.
   *
   * Polling stops the moment the job reports a finish time, so a completed
   * send does not keep asking the server about itself forever. */
  useEffect(() => {
    if (!job || job.finished_at) return undefined;
    const timer = setInterval(async () => {
      try {
        const response = await get(`/messages/jobs/${jobId.current}`);
        const next = response?.data ?? response;
        setJob(next);
        if (next?.finished_at) setLogKey((k) => k + 1);
      } catch {
        // The job is pruned half an hour after it finishes. Losing the
        // progress is not losing the send - the message log has every row.
        setJob((current) => (current ? { ...current, finished_at: Date.now() / 1000 } : null));
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [job]);

  const { options: zones, loading: zonesLoading } =
    useLookup("/masters/zones", { valueKey: "name", labelKey: "name" });

  const selected = AUDIENCES.find((a) => a.value === audience);

  const error_ = useMemo(() => {
    if (!message.trim()) return "Write the message you want to send.";
    if (message.length > MAX_LENGTH) return `Keep it under ${MAX_LENGTH} characters.`;
    return null;
  }, [message]);

  function insertToken(token) {
    setMessage((current) => (current ? `${current} ${token}` : token));
  }

  async function send(event) {
    event.preventDefault();
    setTouched(true);
    if (error_ || busy) return;

    const where = zone ? ` in ${zone}` : "";
    const confirmed = await confirm({
      title: "Send this message?",
      message: `It will go to “${selected?.label}”${where}. WhatsApp messages cannot be recalled once sent.`,
      confirmLabel: "Send now",
      tone: "danger",
    });
    if (!confirmed) return;

    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response = await post("/messages/bulk", {
        message: message.trim(),
        audience,
        zone: zone || undefined,
      });
      const data = response?.data || response;
      setResult(data);

      if (!data?.recipients) {
        toast.warning("No customers matched that audience — nothing was sent.");
      } else {
        jobId.current = data.job?.id || null;
        setJob(data.job || null);
        toast.success(`Sending to ${data.recipients} customer`
          + `${data.recipients === 1 ? "" : "s"}. You can leave this screen.`);
      }
      setLogKey((k) => k + 1);
    } catch (err) {
      setError(err);
      toast.error(
        err.message === "messaging_unavailable"
          ? "WhatsApp messaging is not configured. Check Settings."
          : err.message === "bulk_send_in_progress"
            ? err.detail
            : readableError(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>Bulk messages</h1>
          <p>Send a WhatsApp message or payment reminder to a group of customers.</p>
        </div>
      </div>

      <div className="grid-two">
        <form className="panel stack" onSubmit={send} noValidate>
          <h2>Compose</h2>

          {error && (
            <div className="alert error">
              {error.message === "messaging_unavailable"
                ? "WhatsApp messaging is not configured yet. Add your provider credentials under Settings."
                : readableError(error)}
            </div>
          )}

          {job && <JobProgress job={job} />}

          {result && !job && (
            <div className="alert success" role="status">
              Matched <strong>{result.recipients}</strong> customer
              {result.recipients === 1 ? "" : "s"}. {result.detail}
            </div>
          )}

          <div className="field">
            <label htmlFor="audience">Send to</label>
            <select id="audience" className="input" value={audience}
                    onChange={(e) => { setAudience(e.target.value); setResult(null); }}>
              {AUDIENCES.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
            {selected?.hint && <div className="hint">{selected.hint}</div>}
          </div>

          <div className="field">
            <label htmlFor="zone">Limit to zone</label>
            <select id="zone" className="input" value={zone}
                    onChange={(e) => { setZone(e.target.value); setResult(null); }}
                    disabled={zonesLoading}>
              <option value="">{zonesLoading ? "Loading…" : "All zones"}</option>
              {zones.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>

          <div className={`field${touched && error_ ? " has-error" : ""}`}>
            <label htmlFor="message">Message</label>
            <textarea
              id="message"
              className="input"
              rows={6}
              value={message}
              maxLength={MAX_LENGTH}
              onChange={(e) => { setMessage(e.target.value); setResult(null); }}
              onBlur={() => setTouched(true)}
              placeholder="Hello {{first_name}}, your plan expires on {{expiry}}. Please renew to avoid interruption."
              aria-invalid={Boolean(touched && error_)}
            />
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              {touched && error_
                ? <span className="field-error">{error_}</span>
                : <span className="hint">Placeholders are replaced per customer.</span>}
              <span className="hint" style={{ whiteSpace: "nowrap" }}>
                {message.length}/{MAX_LENGTH}
              </span>
            </div>
          </div>

          <div className="field">
            <label>Insert a placeholder</label>
            <div className="token-list">
              {TOKENS.map(([token, description]) => (
                <button key={token} type="button" className="chip" title={description}
                        onClick={() => insertToken(token)}>
                  {token}
                </button>
              ))}
            </div>
          </div>

          <button className="btn primary" disabled={busy}>
            {busy ? "Sending…" : "Review and send"}
          </button>
        </form>

        <section className="panel">
          <h2>Preview</h2>
          <div className="wa-preview">
            <div className="wa-bubble">
              {message.trim()
                ? message.split("\n").map((line, i) => <p key={i}>{line || " "}</p>)
                : <p className="muted">Your message will appear here.</p>}
            </div>
          </div>
          <p className="hint" style={{ marginTop: 12 }}>
            Placeholders show as-is here; each customer receives their own values.
          </p>
        </section>
      </div>

      <MessageLog key={logKey} />
    </section>
  );
}

/* ------------------------------------------------------------------ */

function MessageLog() {
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } = useFetch("/messages/log", { page });
  const rows = Array.isArray(data) ? data : [];

  return (
    <section className="panel" style={{ marginTop: "1.25rem" }}>
      <h2>Recent messages</h2>
      <ErrorNote error={error} onRetry={refetch} />
      <div className="table-wrap">
        {loading ? (
          <Loading label="Loading message log" />
        ) : !rows.length ? (
          <Empty title="No messages sent yet" hint="Messages you send will be logged here." />
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>When</th><th>Customer</th><th>Channel</th>
                <th>Message</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={row.status === "failed" ? "rail rail-danger" : "rail rail-ok"}>
                  <td>{fmtDate(row.created_at)}</td>
                  <td>{row.customer_name || (row.customer_id ? `#${row.customer_id}` : "—")}</td>
                  <td>{row.channel || "—"}</td>
                  <td className="wrap-cell">{(row.body || "").slice(0, 90)}{(row.body || "").length > 90 ? "…" : ""}</td>
                  <td><span className={`pill ${row.status === "sent" ? "ok" : row.status === "failed" ? "danger" : "warn"}`}>{row.status || "unknown"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <Pager meta={meta} onPage={setPage} />
    </section>
  );
}

/**
 * How far a background send has got.
 *
 * Shows the count rather than a bare spinner: an operator who has just
 * messaged four hundred customers wants to know how many have gone, and
 * whether any are failing, while it is still happening - not afterwards.
 */
function JobProgress({ job }) {
  const done = Number(job.done || 0);
  const total = Number(job.total || 0) || 1;
  const percent = Math.min(100, Math.round((done / total) * 100));
  const finished = Boolean(job.finished_at);
  const tone = job.failed ? "warning" : finished ? "success" : "info";

  return (
    <div className={`alert ${tone}`} role="status">
      <div>
        <strong>
          {finished ? "Finished" : "Sending"} — {done} of {job.total}
        </strong>
        {job.failed > 0 && <> · {job.failed} failed</>}
        <div className="bulk-progress" aria-hidden="true">
          <span style={{ width: `${percent}%` }} />
        </div>
        <div className="hint">
          {finished
            ? "Every attempt is in the message log below."
            : "This continues on the server — you can leave this screen."}
        </div>
      </div>
    </div>
  );
}
