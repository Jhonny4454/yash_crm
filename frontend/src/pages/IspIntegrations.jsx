import { useState } from "react";
import { get, post } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useLookup } from "../api/useLookup";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import {
  Empty, ErrorNote, Loading, Pager, ScrollArrows, fmtDate, readableError,
} from "../components/ui";
import "../styles/Forms.css";
import MoneyInput from "../components/MoneyInput";

/**
 * ISP integrations - credentials for the upstream provider APIs, plus the
 * sync log.
 *
 * Replaces isp/list.html and isp/form.html. The endpoints
 * (/isp/credentials, /isp/test, /isp/sync-logs) already existed and had no
 * React screen at all, so this module was unreachable in the SPA.
 *
 * Secrets are write-only: the API returns has_secret / has_api_key flags but
 * never the values, so the form shows "set" and leaves the input blank -
 * submitting blank keeps whatever is stored.
 */

const DRIVERS = [
  { value: "log2space", label: "Log2Space" },
  { value: "synnefo", label: "Synnefo" },
  { value: "24online", label: "24Online" },
  { value: "xceednet", label: "XceedNet" },
];

const driverLabel = (value) =>
  DRIVERS.find((d) => d.value === value)?.label || value || "—";

export default function IspIntegrations() {
  const { isAdmin } = useAuth();
  const { toast } = useToast();

  const [editing, setEditing] = useState(null);
  const [testingId, setTestingId] = useState(null);
  const [tab, setTab] = useState("credentials");

  const { data, loading, error, refetch } = useFetch("/isp/credentials");
  const rows = Array.isArray(data) ? data : [];

  async function testConnection(cred) {
    setTestingId(cred.id);
    try {
      const response = await post("/isp/test", { id: cred.id });
      const result = response?.data || response;
      if (result?.ok) toast.success(result.message || "Connection successful.");
      else toast.warning(result?.message || "The provider rejected the connection.");
      refetch();
    } catch (err) {
      toast.error(
        err.message === "no_credential"
          ? "Save this integration before testing it."
          : err.detail || readableError(err),
      );
    } finally {
      setTestingId(null);
    }
  }

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>ISP integrations</h1>
          <p>Credentials for the upstream provider APIs used to activate, suspend and renew connections.</p>
        </div>
        {isAdmin && (
          <button className="btn primary" onClick={() => setEditing({})} style={{ borderRadius: 8, padding: "9px 18px" }}>
            <i className="fas fa-plus" aria-hidden="true" /> Add integration
          </button>
        )}
      </div>

      <div className="filter-chips" role="tablist" style={{ marginBottom: "1.25rem" }}>
        <button type="button" role="tab" aria-selected={tab === "credentials"}
                className={tab === "credentials" ? "chip is-active" : "chip"}
                onClick={() => setTab("credentials")}>
          Integrations
        </button>
        <button type="button" role="tab" aria-selected={tab === "logs"}
                className={tab === "logs" ? "chip is-active" : "chip"}
                onClick={() => setTab("logs")}>
          Sync log
        </button>
      </div>

      {tab === "logs" ? (
        <SyncLogs />
      ) : (
        <>
          <ErrorNote error={error} onRetry={refetch} />
          <div className="card" style={{ borderRadius: 12, overflow: "hidden" }}>
            <ScrollArrows>
              {loading ? (
                <Loading label="Loading integrations" />
              ) : !rows.length ? (
                <Empty
                  title="No integrations configured"
                  hint="Add your provider's API details so plans can be activated automatically."
                  action={isAdmin && (
                    <button className="btn primary" onClick={() => setEditing({})} style={{ borderRadius: 8 }}>Add integration</button>
                  )}
                />
              ) : (
                <table className="data">
                  <thead>
                    <tr>
                      <th>Provider</th><th>Label</th><th>Base URL</th>
                      <th>Credentials</th><th>Health</th><th>Last OK</th>
                      <th className="right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((cred) => (
                      <tr key={cred.id} className={cred.health === "error" ? "rail rail-danger" : "rail rail-idle"}>
                        <td>
                          <strong>{driverLabel(cred.driver)}</strong>
                          {cred.is_sandbox && <span className="pill warn" style={{ marginLeft: 6 }}>sandbox</span>}
                          {!cred.is_active && <span className="pill idle" style={{ marginLeft: 6 }}>disabled</span>}
                        </td>
                        <td>{cred.label || "—"}</td>
                        <td className="wrap-cell"><code style={{ fontSize: "0.8rem", background: "#f1f5f9", padding: "2px 6px", borderRadius: 4 }}>{cred.base_url || "—"}</code></td>
                        <td>
                          <span className={`pill ${cred.has_secret ? "ok" : "idle"}`}>
                            {cred.has_secret ? "password set" : "no password"}
                          </span>
                          {cred.has_api_key && <span className="pill ok" style={{ marginLeft: 4 }}>API key</span>}
                        </td>
                        <td>
                          <HealthPill health={cred.health} />
                          {cred.last_error && (
                            <div className="field-error" title={cred.last_error} style={{ marginTop: 3, fontSize: "0.78rem" }}>
                              {cred.last_error.slice(0, 60)}
                              {cred.last_error.length > 60 ? "…" : ""}
                            </div>
                          )}
                        </td>
                        <td>{cred.last_ok_at ? fmtDate(cred.last_ok_at) : "never"}</td>
                        <td className="right">
                          <div className="row-actions">
                            {isAdmin && (
                              <button className="btn sm" disabled={testingId === cred.id}
                                      onClick={() => testConnection(cred)}
                                      style={{ borderRadius: 6 }}>
                                {testingId === cred.id ? "Testing…" : "Test"}
                              </button>
                            )}
                            {isAdmin && (
                              <button className="btn sm" onClick={() => setEditing({ ...cred })}
                                      style={{ borderRadius: 6 }}>Edit</button>
                            )}
                            {!isAdmin && <span className="muted" style={{ fontSize: "0.78rem" }}>Admin only</span>}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </ScrollArrows>
          </div>
        </>
      )}

      {editing && (
        <CredentialDialog
          value={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            toast.success("Integration saved.");
            refetch();
          }}
        />
      )}
    </section>
  );
}

function HealthPill({ health }) {
  const tone = health === "ok" ? "ok" : health === "error" ? "danger" : "idle";
  return <span className={`pill ${tone}`}>{health || "untested"}</span>;
}

/* ------------------------------------------------------------------ */

function CredentialDialog({ value, onClose, onSaved }) {
  const isNew = !value.id;
  const [form, setForm] = useState({
    id: value.id,
    driver: value.driver || "log2space",
    service_provider_id: value.service_provider_id || "",
    label: value.label || "",
    base_url: value.base_url || "",
    username: value.username || "",
    password: "",
    api_key: "",
    nas: value.nas || "",
    site: value.site || "",
    timeout_seconds: value.timeout_seconds ?? 20,
    verify_ssl: value.verify_ssl ?? true,
    is_active: value.is_active ?? true,
    is_sandbox: value.is_sandbox ?? false,
  });
  const [touched, setTouched] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const { options: providers, loading: providersLoading } =
    useLookup("/service-providers", { labelKey: "name" });

  const errors = {};
  if (!form.base_url.trim()) errors.base_url = "Base URL is required.";
  else if (!/^https?:\/\//i.test(form.base_url.trim())) {
    errors.base_url = "Start with http:// or https://";
  }
  if (isNew && !form.service_provider_id) {
    errors.service_provider_id = "Link this integration to a service provider.";
  }
  if (isNew && !form.password) {
    errors.password = "A password is required when creating an integration.";
  }
  const timeout = Number(form.timeout_seconds);
  if (Number.isNaN(timeout) || timeout < 1 || timeout > 300) {
    errors.timeout_seconds = "Use a value between 1 and 300 seconds.";
  }
  const isValid = Object.keys(errors).length === 0;

  const set = (key, type) => (e) =>
    setForm((f) => ({ ...f, [key]: type === "checkbox" ? e.target.checked : e.target.value }));
  const blur = (key) => () => setTouched((t) => ({ ...t, [key]: true }));
  const errorFor = (key) => (touched[key] ? errors[key] : undefined);

  async function save(event) {
    event.preventDefault();
    setTouched(Object.fromEntries(Object.keys(form).map((k) => [k, true])));
    if (!isValid || busy) return;

    setBusy(true);
    setError(null);

    // Blank secrets mean "leave the stored value alone" - never send "".
    const payload = { ...form };
    if (!payload.password) delete payload.password;
    if (!payload.api_key) delete payload.api_key;

    try {
      await post("/isp/credentials", payload);
      onSaved();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <div className="modal-scrim">
      <form className="card modal-card" style={{ maxWidth: 680, borderRadius: 16 }}
            onSubmit={save} noValidate>
        <div className="card-head" style={{ padding: "16px 24px", borderBottom: "1px solid #f1f5f9" }}>
          <h2 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 700 }}>{isNew ? "Add ISP integration" : `Edit ${driverLabel(form.driver)}`}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"
                  style={{ fontSize: 18, opacity: 0.5 }}>✕</button>
        </div>

        <div className="card-body" style={{ padding: "20px 24px" }}>
          {error && (
            <div className="alert error" style={{ borderRadius: 8, marginBottom: 16 }}>
              {error.message === "service_provider_id_required"
                ? "Create a service provider first, then link this integration to it."
                : error.message === "unknown_driver"
                  ? "That provider is not supported."
                  : readableError(error)}
              {error.detail && <div className="hint" style={{ marginTop: 4 }}>{error.detail}</div>}
            </div>
          )}

          <div className="grid grid-2" style={{ gap: "16px 24px" }}>
            <div className="field">
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Provider *</label>
              <select className="input" value={form.driver} onChange={set("driver")}
                      style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }}>
                {DRIVERS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </div>

            <div className={`field${errorFor("service_provider_id") ? " has-error" : ""}`}>
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Service provider {isNew && "*"}</label>
              <select className="input" value={form.service_provider_id}
                      onChange={set("service_provider_id")} onBlur={blur("service_provider_id")}
                      disabled={providersLoading || !isNew}
                      style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }}>
                <option value="">{providersLoading ? "Loading…" : "Select…"}</option>
                {providers.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              {errorFor("service_provider_id")
                ? <div className="field-error">{errorFor("service_provider_id")}</div>
                : !isNew && <div className="hint" style={{ marginTop: 4 }}>Cannot be changed after creation.</div>}
            </div>

            <div className="field">
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Label</label>
              <input className="input" value={form.label} onChange={set("label")}
                     placeholder="e.g. Primary NAS"
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} />
            </div>

            <div className={`field${errorFor("base_url") ? " has-error" : ""}`}>
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Base URL *</label>
              <input className="input" value={form.base_url} onChange={set("base_url")}
                     onBlur={blur("base_url")} placeholder="https://provider.example.com"
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} />
              {errorFor("base_url") && <div className="field-error">{errorFor("base_url")}</div>}
            </div>

            <div className="field">
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Username</label>
              <input className="input" value={form.username} onChange={set("username")}
                     autoComplete="off"
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} />
            </div>

            <div className={`field${errorFor("password") ? " has-error" : ""}`}>
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Password {isNew && "*"}</label>
              <input className="input" type="password" value={form.password}
                     onChange={set("password")} onBlur={blur("password")}
                     autoComplete="new-password"
                     placeholder={value.has_secret ? "•••••• (stored)" : ""}
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} />
              {errorFor("password")
                ? <div className="field-error">{errorFor("password")}</div>
                : <div className="hint" style={{ marginTop: 4 }}>
                    {value.has_secret ? "Leave blank to keep the stored password." : "Stored encrypted; never shown again."}
                  </div>}
            </div>

            <div className="field">
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>API key</label>
              <input className="input" type="password" value={form.api_key}
                     onChange={set("api_key")} autoComplete="off"
                     placeholder={value.has_api_key ? "•••••• (stored)" : ""}
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} />
              <div className="hint" style={{ marginTop: 4 }}>Optional, depending on the provider.</div>
            </div>

            <div className="field">
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>NAS</label>
              <input className="input" value={form.nas} onChange={set("nas")}
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} />
            </div>

            <div className="field">
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Site</label>
              <input className="input" value={form.site} onChange={set("site")}
                     style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} />
            </div>

            <div className={`field${errorFor("timeout_seconds") ? " has-error" : ""}`}>
              <label style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 5, display: "block" }}>Timeout (seconds)</label>
              <MoneyInput className="input" min={1} max={300}
                value={form.timeout_seconds} onChange={set("timeout_seconds")}
                onBlur={blur("timeout_seconds")}
                style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #d0d5dd" }} />
              {errorFor("timeout_seconds") && <div className="field-error">{errorFor("timeout_seconds")}</div>}
            </div>
          </div>

          <div style={{ display: "flex", gap: "1.25rem", flexWrap: "wrap", marginTop: 16, padding: "12px 0", borderTop: "1px solid #f1f5f9" }}>
            <label className="check-row">
              <input type="checkbox" checked={form.is_active} onChange={set("is_active", "checkbox")} />
              Active
            </label>
            <label className="check-row">
              <input type="checkbox" checked={form.verify_ssl} onChange={set("verify_ssl", "checkbox")} />
              Verify SSL certificate
            </label>
            <label className="check-row">
              <input type="checkbox" checked={form.is_sandbox} onChange={set("is_sandbox", "checkbox")} />
              Sandbox / test mode
            </label>
          </div>
        </div>

        <div className="modal-foot" style={{ padding: "14px 24px", borderTop: "1px solid #f1f5f9" }}>
          <span />
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button type="button" className="btn" onClick={onClose} style={{ borderRadius: 8 }}>Cancel</button>
            <button className="btn primary" disabled={busy} style={{ borderRadius: 8 }}>
              {busy ? <span className="spinner" /> : isNew ? "Add integration" : "Save changes"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function SyncLogs() {
  const [page, setPage] = useState(1);
  const { data, meta, loading, error, refetch } = useFetch("/isp/sync-logs", { page });
  const rows = Array.isArray(data) ? data : [];

  return (
    <>
      <ErrorNote error={error} onRetry={refetch} />
      <div className="card">
        <ScrollArrows>
          {loading ? (
            <Loading label="Loading sync log" />
          ) : !rows.length ? (
            <Empty
              title="Nothing synced yet"
              hint="Activations and renewals sent to the provider will be recorded here."
            />
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>When</th><th>Provider</th><th>Action</th><th>Customer</th>
                  <th>Result</th><th className="right">HTTP</th><th className="right">Duration</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((log) => (
                  <tr key={log.id} className={log.success ? "rail rail-ok" : "rail rail-danger"}>
                    <td>{fmtDate(log.created_at)}</td>
                    <td>{driverLabel(log.driver)}</td>
                    <td>{log.action || "—"}</td>
                    <td>{log.customer_id ? `#${log.customer_id}` : "—"}</td>
                    <td>
                      <span className={`pill ${log.success ? "ok" : "danger"}`}>
                        {log.success ? "success" : "failed"}
                      </span>
                      {!log.success && log.response_summary && (
                        <div className="field-error wrap-cell" title={log.response_summary}>
                          {log.response_summary.slice(0, 80)}
                          {log.response_summary.length > 80 ? "…" : ""}
                        </div>
                      )}
                    </td>
                    <td className="right num">{log.http_status ?? "—"}</td>
                    <td className="right num">{log.duration_ms != null ? `${log.duration_ms} ms` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </ScrollArrows>
        <Pager meta={meta} onPage={setPage} />
      </div>
    </>
  );
}
