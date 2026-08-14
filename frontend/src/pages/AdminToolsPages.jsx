import { useState } from "react";
import { get, post, upload } from "../api/client";
import { useFetch } from "../api/useFetch";
import { Empty, ErrorNote, fmtDate, Loading } from "../components/ui";

export function BackupsPage() {
  const { data, meta, loading, error, refetch } = useFetch("/settings/backups"); const [busy, setBusy] = useState(false); const [message, setMessage] = useState(null);
  async function createBackup() { setBusy(true); setMessage(null); try { await post("/settings/backups"); setMessage("Backup created successfully."); refetch(); } catch (err) { setMessage(err.message); } finally { setBusy(false); } }
  return <section className="page"><div className="page-heading"><div><h1>Database backups</h1><p>Create and download protected database backups.</p></div><button className="btn primary" disabled={busy} onClick={createBackup}>{busy ? "Creating…" : "Create backup"}</button></div>{message && <div className="alert info">{message}</div>}<section className="panel table-wrap">{loading ? <Loading /> : error ? <ErrorNote error={error} onRetry={refetch} /> : !data?.length ? <Empty title="No backups yet" hint="Create a backup before making a major data change." /> : <table className="data"><thead><tr><th>File</th><th>Created</th><th>Size</th><th>Status</th><th /></tr></thead><tbody>{data.map((item) => <tr key={item.id}><td>{item.filename}</td><td>{fmtDate(item.created_at)}</td><td>{item.size_human}</td><td>{item.status}</td><td>{item.download_url && <a className="btn sm" href={item.download_url}>Download</a>}</td></tr>)}</tbody></table>}</section>{meta && <p className="muted">{meta.total} backups</p>}</section>;
}

export function ImportExportPage() {
  const [entity, setEntity] = useState("customers"); const [file, setFile] = useState(null); const [message, setMessage] = useState(null); const [busy, setBusy] = useState(false);
  function download() { window.open(`/api/v1/settings/export?entity=${encodeURIComponent(entity)}`, "_blank", "noopener"); }
  async function importFile(event) { event.preventDefault(); if (!file) { setMessage("Choose a CSV file first."); return; } setBusy(true); setMessage(null); try { const form = new FormData(); form.append("entity", entity); form.append("file", file); const result = await upload("/settings/import", form); setMessage(`${result.data?.created || 0} created, ${result.data?.updated || 0} updated.`); } catch (err) { setMessage(err.message); } finally { setBusy(false); } }
  return <section className="page"><div className="page-heading"><div><h1>Import and export</h1><p>Download a CSV backup or safely bulk-import supported data.</p></div></div><section className="panel stack"><label>Data type<select value={entity} onChange={(event) => setEntity(event.target.value)}>{["customers", "plans", "invoices", "payments", "products", "expenses"].map((item) => <option key={item} value={item}>{item}</option>)}</select></label><div className="row-actions"><button className="btn" type="button" onClick={download}>Download CSV</button></div></section><form className="panel stack" onSubmit={importFile}><h2>Import CSV</h2><p className="muted">Existing rows are updated only when their exported ID is present.</p>{message && <div className="alert info">{message}</div>}<input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] || null)} /><button className="btn primary" disabled={busy}>{busy ? "Importing…" : "Import file"}</button></form></section>;
}

export function ReportsPage({ endpoint, title }) {
  const { data, loading, error, refetch } = useFetch(endpoint);
  const rows = Array.isArray(data) ? data : data?.rows || data?.data || [];
  const columns = rows[0] ? Object.keys(rows[0]).filter((key) => typeof rows[0][key] !== "object") : [];
  return <section className="page"><div className="page-heading"><div><h1>{title}</h1><p>Up-to-date operational report generated from the Flask API.</p></div></div><section className="panel table-wrap">{loading ? <Loading label="Preparing report" /> : error ? <ErrorNote error={error} onRetry={refetch} /> : !rows.length ? <Empty title="No report rows" hint="Try again after more data is recorded." /> : <table className="data"><thead><tr>{columns.map((column) => <th key={column}>{column.replaceAll("_", " ")}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={row.id || index}>{columns.map((column) => <td key={column}>{String(row[column] ?? "—")}</td>)}</tr>)}</tbody></table>}</section></section>;
}

export function NotificationTemplatesPage() {
  const { data, loading, error, refetch } = useFetch("/notification-templates");
  return <section className="page"><div className="page-heading"><div><h1>Notification templates</h1><p>Templates used for customer communication.</p></div></div><section className="panel">{loading ? <Loading /> : error ? <ErrorNote error={error} onRetry={refetch} /> : !data?.length ? <Empty title="No templates configured" /> : <div className="list-cards">{data.map((item) => <article key={item.id}><div><strong>{item.name || item.template_type}</strong><p>{item.body || item.title}</p></div><small>{item.template_type}</small></article>)}</div>}</section></section>;
}
