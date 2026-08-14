import { useRef, useState } from "react";
import { post, put, upload } from "../api/client";
import { useFetch } from "../api/useFetch";
import { useAuth } from "../context/AuthContext";
import { Empty, ErrorNote, Loading, readableError } from "../components/ui";

const FIELDS = [
  ["name", "Company name", true], ["mobile", "Mobile"], ["phone", "Phone"],
  ["email", "Email"], ["gstin", "GSTIN"], ["pan_no", "PAN"], ["sac_no", "SAC code"],
  ["state_code", "State code"], ["place_of_supply", "Place of supply"],
  ["b2b_invoice_series", "B2B invoice series"], ["b2c_invoice_series", "B2C invoice series"],
  ["website_url", "Website"], ["company_type", "Company type"],
];

// Mirrors ALLOWED_LOGO_EXT and MAX_CONTENT_LENGTH on the Flask side.
const LOGO_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "svg"];
const MAX_LOGO_BYTES = 16 * 1024 * 1024;

/* The two-column layout is set by the `company-layout` class, not inline.
   It used to be `style={{ gridTemplateColumns: "260px minmax(0,1fr)" }}`, and
   an inline style cannot be overridden by a media query - so on a phone the
   form column was squeezed to about 80px and every label, input and button
   burst out of the card. See Boxes.css. */
export default function Companies() {
  const { isAdmin, setCompany } = useAuth();
  const { data, loading, error, refetch } = useFetch("/companies");
  const [selected, setSelected] = useState(null);

  const active = selected ?? data?.[0] ?? null;

  return (
    <>
      <ErrorNote error={error} onRetry={refetch} />

      {loading ? <Loading label="Loading companies" /> : (
        <div className="grid company-layout">
          <div className="card">
            <div className="card-head">
              <h2>Companies</h2>
              {isAdmin && (
                <button className="btn sm primary" onClick={() => setSelected({ name: "" })}>Add</button>
              )}
            </div>
            <div style={{ padding: 8 }}>
              {!data?.length ? <Empty title="None yet" /> : data.map((c) => (
                <button key={c.id}
                  className={`sidebar-link${active?.id === c.id ? " active" : ""}`}
                  style={{ width: "100%", background: active?.id === c.id ? "var(--brand)" : "transparent", color: active?.id === c.id ? "#fff" : "var(--ink)", border: "none", cursor: "pointer", textAlign: "left" }}
                  onClick={() => setSelected(c)}>
                  {c.logo_url
                    ? <img src={c.logo_url} alt="" style={{ width: 22, height: 22, objectFit: "contain" }} />
                    : <span className="ico">◫</span>}
                  <span>{c.name}</span>
                </button>
              ))}
            </div>
          </div>

          {active ? (
            <CompanyForm key={active.id || "new"} company={active} readOnly={!isAdmin}
              onSaved={(saved) => {
                refetch();
                setSelected(saved);
                if (!data?.length || saved.id === data?.[0]?.id) setCompany(saved);
              }} />
          ) : <Empty title="Select a company" />}
        </div>
      )}
    </>
  );
}

function CompanyForm({ company, readOnly, onSaved }) {
  const [form, setForm] = useState(company);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const fileRef = useRef(null);
  const isNew = !form.id;

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save(e) {
    e.preventDefault();
    setBusy(true); setError(null); setNotice(null);
    try {
      const res = isNew ? await post("/companies", form) : await put(`/companies/${form.id}`, form);
      setForm(res.data);
      setNotice("Saved.");
      onSaved(res.data);
    } catch (err) { setError(err); }
    finally { setBusy(false); }
  }

  async function uploadLogo(e) {
    const file = e.target.files?.[0];
    if (!file || !form.id) return;

    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!LOGO_EXTENSIONS.includes(ext)) {
      setError(new Error(`Choose a ${LOGO_EXTENSIONS.join(", ")} image — “.${ext}” is not supported.`));
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      setError(new Error(`That image is ${(file.size / 1024 / 1024).toFixed(1)} MB. The limit is 16 MB.`));
      if (fileRef.current) fileRef.current.value = "";
      return;
    }

    setBusy(true); setError(null); setNotice(null);
    try {
      const fd = new FormData();
      fd.append("logo", file);
      const res = await upload(`/companies/${form.id}/logo`, fd);
      const next = { ...form, logo_url: res.data.logo_url, company_logo: res.data.company_logo };
      setForm(next);
      setNotice("Logo updated. It now appears on every bill and in the customer app.");
      onSaved(next);
    } catch (err) { setError(err); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  return (
    <form className="card" onSubmit={save}>
      <div className="card-head">
        <h2>{isNew ? "Add company" : form.name}</h2>
        {!readOnly && (
          <button className="btn primary" disabled={busy}>
            {busy ? <span className="spinner" /> : "Save changes"}
          </button>
        )}
      </div>

      <div className="card-body">
        {error && <div className="alert error">{readableError(error)}</div>}
        {notice && <div className="alert success">{notice}</div>}

        {!isNew && (
          <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 20, paddingBottom: 16, borderBottom: "1px solid var(--line)" }}>
            <div style={{ width: 92, height: 92, border: "1px dashed var(--line-strong)", borderRadius: "var(--radius)", display: "grid", placeItems: "center", background: "var(--canvas)", overflow: "hidden" }}>
              {form.logo_url
                ? <img src={form.logo_url} alt="Company logo" style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} />
                : <span style={{ color: "var(--muted)", fontSize: 12 }}>No logo</span>}
            </div>
            <div>
              <div style={{ fontWeight: 600, marginBottom: 3 }}>Company logo</div>
              <div className="hint" style={{ marginBottom: 8 }}>
                Used on invoices, receipts and the customer app. PNG or JPG, up to 16&nbsp;MB.
              </div>
              {!readOnly && (
                <>
                  <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.gif,.webp,.svg" onChange={uploadLogo} style={{ display: "none" }} />
                  <button type="button" className="btn sm" onClick={() => fileRef.current?.click()} disabled={busy}>
                    {form.logo_url ? "Replace logo" : "Upload logo"}
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        <div className="grid grid-2">
          {FIELDS.map(([key, label, required]) => (
            <div className="field" key={key}>
              <label>{label}</label>
              <input className="input" value={form[key] || ""} onChange={set(key)}
                required={required} disabled={readOnly} />
            </div>
          ))}
        </div>

        <div className="field">
          <label>Registered address</label>
          <textarea className="input" rows={3} value={form.address || ""} onChange={set("address")} disabled={readOnly} />
        </div>
        <div className="field">
          <label>Bank details shown on invoices</label>
          <textarea className="input" rows={3} value={form.bank_account_details || ""} onChange={set("bank_account_details")} disabled={readOnly} />
        </div>
        <div className="field">
          <label>Invoice footer note</label>
          <textarea className="input" rows={2} value={form.invoice_notes || ""} onChange={set("invoice_notes")} disabled={readOnly} />
        </div>
      </div>
    </form>
  );
}
