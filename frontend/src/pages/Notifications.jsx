import { useState } from "react";
import { post, put } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useAuth } from "../context/AuthContext";
import { Empty, ErrorNote, Loading, fmtDate, readableError } from "../components/ui";

export default function Notifications() {
  const [tab, setTab] = useState("templates");
  return (
    <>
      <div className="toolbar">
        <button className={`btn${tab === "templates" ? " primary" : ""}`} onClick={() => setTab("templates")}>
          Message templates
        </button>
        <button className={`btn${tab === "send" ? " primary" : ""}`} onClick={() => setTab("send")}>
          Send a message
        </button>
        <button className={`btn${tab === "log" ? " primary" : ""}`} onClick={() => setTab("log")}>
          Sent history
        </button>
      </div>
      {tab === "templates" && <Templates />}
      {tab === "send" && <SendPanel />}
      {tab === "log" && <LogPanel />}
    </>
  );
}

function Templates() {
  const { isAdmin } = useAuth();
  const { data, loading, error, refetch } = useFetch("/notification-templates");
  const [open, setOpen] = useState(null);

  if (loading) return <Loading label="Loading templates" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;

  return (
    <div className="grid" style={{ gap: 12 }}>
      {data?.map((t) => (
        <div className="card" key={t.id}>
          <div className="card-head">
            <div>
              <h2>{t.name}</h2>
              <div className="hint">{t.description}</div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span className={`pill ${t.is_active ? "ok" : "idle"}`}>
                {t.is_active ? "on" : "off"}
              </span>
              {isAdmin && (
                <button className="btn sm" onClick={() => setOpen(open === t.id ? null : t.id)}>
                  {open === t.id ? "Close" : "Edit"}
                </button>
              )}
            </div>
          </div>
          {open === t.id
            ? <TemplateEditor template={t} onSaved={() => { setOpen(null); refetch(); }} />
            : (
              <div className="card-body">
                <div style={{ fontWeight: 600 }}>{t.title}</div>
                <div style={{ color: "var(--ink-soft)", whiteSpace: "pre-line" }}>{t.body}</div>
              </div>
            )}
        </div>
      ))}
    </div>
  );
}

function TemplateEditor({ template, onSaved }) {
  const [form, setForm] = useState(template);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const set = (k) => (e) =>
    setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));

  async function save() {
    setBusy(true); setError(null);
    try { await put(`/notification-templates/${form.id}`, form); onSaved(); }
    catch (err) { setError(err); setBusy(false); }
  }

  return (
    <div className="card-body">
      {error && <div className="alert error">{readableError(error)}</div>}
      <div className="field">
        <label>Title</label>
        <input className="input" value={form.title} onChange={set("title")} />
      </div>
      <div className="field">
        <label>Message</label>
        <textarea className="input" rows={3} value={form.body} onChange={set("body")} />
        <div className="hint">
          Placeholders in braces are filled in automatically, for example
          {" {customer_name}, {plan_name}, {end_date}, {amount}, {invoice_no}, {due_date}."}
        </div>
      </div>
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginBottom: 14 }}>
        <label style={{ display: "flex", gap: 7, alignItems: "center", fontSize: 13.5 }}>
          <input type="checkbox" checked={!!form.is_active} onChange={set("is_active")} /> Send this message
        </label>
        <label style={{ display: "flex", gap: 7, alignItems: "center", fontSize: 13.5 }}>
          <input type="checkbox" checked={!!form.send_push} onChange={set("send_push")} /> App notification
        </label>
        <label style={{ display: "flex", gap: 7, alignItems: "center", fontSize: 13.5 }}>
          <input type="checkbox" checked={!!form.send_whatsapp} onChange={set("send_whatsapp")} /> WhatsApp
        </label>
      </div>
      <button className="btn primary" onClick={save} disabled={busy}>
        {busy ? <span className="spinner" /> : "Save template"}
      </button>
    </div>
  );
}

function SendPanel() {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function send() {
    setBusy(true); setError(null); setResult(null);
    try {
      const res = await post("/notifications/send", { all: true, title, body });
      setResult(`Queued for ${res.data.queued} customers.`);
      setTitle(""); setBody("");
    } catch (err) { setError(err); }
    finally { setBusy(false); }
  }

  return (
    <div className="card" style={{ maxWidth: 560 }}>
      <div className="card-head"><h2>Send to all active customers</h2></div>
      <div className="card-body">
        {error && <div className="alert error">{readableError(error)}</div>}
        {result && <div className="alert success">{result}</div>}
        <div className="field">
          <label>Title</label>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="field">
          <label>Message</label>
          <textarea className="input" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        <button className="btn primary" onClick={send} disabled={busy || !title || !body}>
          {busy ? <span className="spinner" /> : "Send notification"}
        </button>
      </div>
    </div>
  );
}

function LogPanel() {
  const { data, loading, error, refetch } = useFetch("/notifications");
  if (loading) return <Loading label="Loading history" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data?.length) return <Empty title="Nothing sent yet" hint="Messages you send will be listed here." />;

  return (
    <div className="card">
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>Sent</th><th>Title</th><th>Message</th><th>Channel</th><th>Status</th></tr></thead>
          <tbody>
            {data.map((n) => (
              <tr key={n.id}>
                <td className="num">{fmtDate(n.created_at)}</td>
                <td>{n.title}</td>
                <td style={{ color: "var(--ink-soft)" }}>{n.body}</td>
                <td>{n.channel}</td>
                <td><span className={`pill ${n.status === "sent" ? "ok" : "idle"}`}>{n.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
