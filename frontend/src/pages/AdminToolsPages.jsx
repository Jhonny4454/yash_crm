import { useState } from "react";
import { get, post, upload } from "../api/client";
import { useFetch } from "../api/useFetch";
import { Empty, ErrorNote, fmtDate, Loading, ScrollArrows } from "../components/ui";

export function BackupsPage() {
  const { data, meta, loading, error, refetch } = useFetch("/settings/backups");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  async function createBackup() {
    setBusy(true);
    setMessage(null);
    try {
      await post("/settings/backups");
      setMessage("Backup created successfully.");
      refetch();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>Database backups</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: 2 }}>
            Create and download protected database backups.
          </p>
        </div>
        <button className="btn primary" disabled={busy} onClick={createBackup}
                style={{ borderRadius: 8, padding: "9px 18px" }}>
          {busy ? "Creating…" : "Create backup"}
        </button>
      </div>

      {message && <div className="alert info" style={{ borderRadius: 8 }}>{message}</div>}

      <div className="card" style={{ borderRadius: 12, overflow: "hidden" }}>
        <ScrollArrows>
          {loading ? <Loading /> : error ? <ErrorNote error={error} onRetry={refetch} />
            : !data?.length ? (
              <Empty title="No backups yet" hint="Create a backup before making a major data change." />
            ) : (
              <table className="data">
                <thead>
                  <tr><th>File</th><th>Created</th><th>Size</th><th>Status</th><th className="right">Action</th></tr>
                </thead>
                <tbody>
                  {data.map((item) => (
                    <tr key={item.id}>
                      <td><strong>{item.filename}</strong></td>
                      <td>{fmtDate(item.created_at)}</td>
                      <td>{item.size_human}</td>
                      <td><span className={`pill ${item.status === "complete" ? "ok" : "idle"}`}>{item.status}</span></td>
                      <td className="right">
                        {item.download_url && (
                          <a className="btn sm" href={item.download_url} style={{ borderRadius: 6 }}>Download</a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </ScrollArrows>
      </div>
      {meta && <p className="muted" style={{ marginTop: 8 }}>{meta.total} backups</p>}
    </section>
  );
}

export function ImportExportPage() {
  const [entity, setEntity] = useState("customers");
  const [file, setFile] = useState(null);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  function download() {
    window.open(
      `/api/v1/settings/export?entity=${encodeURIComponent(entity)}`,
      "_blank",
      "noopener",
    );
  }

  async function importFile(event) {
    event.preventDefault();
    if (!file) { setMessage("Choose a CSV file first."); return; }
    setBusy(true);
    setMessage(null);
    try {
      const form = new FormData();
      form.append("entity", entity);
      form.append("file", file);
      const result = await upload("/settings/import", form);
      setMessage(`${result.data?.created || 0} created, ${result.data?.updated || 0} updated.`);
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>Import and export</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: 2 }}>
            Download a CSV backup or safely bulk-import supported data.
          </p>
        </div>
      </div>

      <div className="card" style={{ borderRadius: 12, overflow: "hidden", marginBottom: 16 }}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid #f1f5f9", background: "linear-gradient(180deg, #f8fafc, #f1f5f9)" }}>
          <h3 style={{ margin: 0, fontSize: "0.92rem", fontWeight: 700 }}>Export data</h3>
        </div>
        <div style={{ padding: 20, display: "flex", gap: 12, alignItems: "end", flexWrap: "wrap" }}>
          <div className="field" style={{ marginBottom: 0, flex: "0 0 auto" }}>
            <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Data type</label>
            <select value={entity} onChange={(event) => setEntity(event.target.value)}
                    style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid #d0d5dd", fontSize: "0.88rem" }}>
              {["customers", "plans", "invoices", "payments", "products", "expenses"].map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
          </div>
          <button className="btn" type="button" onClick={download} style={{ borderRadius: 8 }}>
            <i className="fas fa-download" aria-hidden="true" /> Download CSV
          </button>
        </div>
      </div>

      <div className="card" style={{ borderRadius: 12, overflow: "hidden" }}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid #f1f5f9", background: "linear-gradient(180deg, #f8fafc, #f1f5f9)" }}>
          <h3 style={{ margin: 0, fontSize: "0.92rem", fontWeight: 700 }}>Import CSV</h3>
        </div>
        <form onSubmit={importFile} style={{ padding: 20 }}>
          <p className="muted" style={{ marginBottom: 12, fontSize: "0.85rem" }}>
            Existing rows are updated only when their exported ID is present.
          </p>
          {message && <div className="alert info" style={{ borderRadius: 8, marginBottom: 12 }}>{message}</div>}
          <div style={{ display: "flex", gap: 12, alignItems: "end", flexWrap: "wrap" }}>
            <div className="field" style={{ marginBottom: 0, flex: "0 0 auto" }}>
              <input type="file" accept=".csv,text/csv"
                     onChange={(event) => setFile(event.target.files?.[0] || null)}
                     style={{ fontSize: "0.88rem" }} />
            </div>
            <button className="btn primary" disabled={busy} style={{ borderRadius: 8, padding: "9px 18px" }}>
              {busy ? "Importing…" : "Import file"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}

export function ReportsPage({ endpoint, title }) {
  const { data, loading, error, refetch } = useFetch(endpoint);
  const rows = Array.isArray(data) ? data : data?.rows || data?.data || [];
  const columns = rows[0]
    ? Object.keys(rows[0]).filter((key) => typeof rows[0][key] !== "object")
    : [];

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>{title}</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: 2 }}>
            Up-to-date operational report generated from the Flask API.
          </p>
        </div>
      </div>

      <div className="card" style={{ borderRadius: 12, overflow: "hidden" }}>
        <ScrollArrows>
          {loading ? <Loading label="Preparing report" />
            : error ? <ErrorNote error={error} onRetry={refetch} />
            : !rows.length ? (
              <Empty title="No report rows" hint="Try again after more data is recorded." />
            ) : (
              <table className="data">
                <thead>
                  <tr>
                    {columns.map((column) => (
                      <th key={column}>{column.replaceAll("_", " ")}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={row.id || index}>
                      {columns.map((column) => (
                        <td key={column}>{String(row[column] ?? "—")}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </ScrollArrows>
      </div>
    </section>
  );
}

export function NotificationTemplatesPage() {
  const { data, loading, error, refetch } = useFetch("/notification-templates");

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>Notification templates</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: 2 }}>
            Templates used for customer communication.
          </p>
        </div>
      </div>

      {loading ? <Loading />
        : error ? <ErrorNote error={error} onRetry={refetch} />
        : !data?.length ? (
          <Empty title="No templates configured" />
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 16 }}>
            {data.map((item) => (
              <div key={item.id} className="card" style={{ borderRadius: 12, overflow: "hidden" }}>
                <div style={{ padding: "16px 20px", borderBottom: "1px solid #f1f5f9", background: "linear-gradient(180deg, #f8fafc, #f1f5f9)" }}>
                  <strong style={{ fontSize: "0.92rem" }}>{item.name || item.template_type}</strong>
                </div>
                <div style={{ padding: "14px 20px" }}>
                  <p style={{ margin: 0, fontSize: "0.85rem", color: "#475569", lineHeight: 1.5 }}>
                    {item.body || item.title}
                  </p>
                </div>
                <div style={{ padding: "0 20px 14px" }}>
                  <span className="pill info" style={{ fontSize: "0.72rem" }}>{item.template_type}</span>
                </div>
              </div>
            ))}
          </div>
        )}
    </section>
  );
}
