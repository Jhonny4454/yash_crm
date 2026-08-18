import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { del, get, post, put, upload } from "../api/client";
import { useDebounced } from "../api/useFetch";
import PlanPicker from "../components/customers/PlanPicker";
import MoneyInput from "../components/MoneyInput";
import { ErrorNote, Loading } from "../components/ui";
import { useToast } from "../context/ToastContext";
import "../styles/Forms.css";

/**
 * Add / edit customer.
 *
 * Replaces customers/add.html and customers/edit.html. One component serves
 * both: with an :id in the URL it loads the record and PUTs, without one it
 * POSTs. That keeps the field list, validation and layout in a single place.
 *
 * Zone and plan are real dropdowns fed from the masters API - the previous
 * version rendered empty <select>s, so those fields could never be set.
 */

// These MUST match the Enum() values on the Customer model exactly - MySQL
// rejects anything else, so a mismatch here fails every save with a 500.
//   title           Enum('Mr.', 'Mrs.', 'Ms.')
//   customer_type   Enum('Residential', 'Company', 'Commercial', 'Enterprise')
//   connection_type Enum('Ethernet', 'FTTH', 'Lease Line')
//   tax_type        Enum('Taxable', 'Non-Taxable')
const TITLES = ["Mr.", "Mrs.", "Ms."];
const CUSTOMER_TYPES = ["Residential", "Company", "Commercial", "Enterprise"];
const CONNECTION_TYPES = ["Ethernet", "FTTH", "Lease Line"];
const TAX_TYPES = ["Taxable", "Non-Taxable"];

//: Free text in the schema, so these are suggestions rather than a hard set -
//: the operator can type anything the customer actually produced.
const ADDRESS_PROOF_TYPES = ["Aadhaar Card", "Electricity Bill", "Rent Agreement",
  "Passport", "Driving Licence", "Bank Statement", "Ration Card"];
const ID_PROOF_TYPES = ["Aadhaar Card", "PAN Card", "Voter ID", "Passport",
  "Driving Licence"];

//: form field -> label, matching the KYC panel on the live CRM.
const KYC_SLOTS = [
  { field: "reg_form", label: "Reg. Form" },
  { field: "photo", label: "Photo" },
  { field: "address_proof", label: "Address Proof", typeField: "address_proof_type",
    suggestions: ADDRESS_PROOF_TYPES },
  { field: "id_proof", label: "ID Proof", typeField: "id_proof_type",
    suggestions: ID_PROOF_TYPES },
];

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.webp,.gif";
const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;

const today = () => new Date().toISOString().slice(0, 10);

const EMPTY = {
  title: "Mr.",
  customer_type: "Residential",
  company_name: "",
  first_name: "",
  middle_name: "",
  last_name: "",
  mobile: "",
  home_phone: "",
  email: "",
  username: "",
  password: "123456",
  zone: "Yashnet",
  locality: "",
  area: "",
  building: "",
  flat_no: "",
  billing_address: "",
  primary_address: "",
  connection_type: "FTTH",
  reference_id: "",
  gstin: "",
  pan: "",
  aadhar: "",
  tax_type: "Non-Taxable",
  is_active: true,
  registration_date: today(),
  billing_type: "Prepaid",
  ip_address: "",
  ipacct_id: "",
  service_provider_id: "",
  invoice_date: today(),
  address_proof_type: "",
  id_proof_type: "",
};

//: Held outside EMPTY because they are not Customer columns - they drive the
//: initial plan assignment, which the API handles as a separate step.
const EMPTY_PLAN = { plan_id: "", plan_start_date: today() };

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const MOBILE_RE = /^[0-9+\-\s()]{7,20}$/;
const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$/i;
const PAN_RE = /^[A-Z]{5}[0-9]{4}[A-Z]$/i;
const IP_RE = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

function validate(form) {
  const errors = {};

  if (!form.first_name.trim()) errors.first_name = "First name is required.";
  if (!form.last_name.trim()) errors.last_name = "Last name is required.";

  if (!form.mobile.trim()) errors.mobile = "Mobile number is required.";
  else if (!MOBILE_RE.test(form.mobile.trim())) errors.mobile = "Enter a valid mobile number.";

  if (form.email.trim() && !EMAIL_RE.test(form.email.trim())) {
    errors.email = "Enter a valid email address.";
  }
  if (form.customer_type !== "Residential" && !form.company_name.trim()) {
    errors.company_name = "Company name is required for a business account.";
  }
  if (form.username.trim() && form.username.trim().length < 3) {
    errors.username = "Use at least 3 characters.";
  }
  if (form.password && form.password.length < 6) {
    errors.password = "Use at least 6 characters.";
  }
  if (form.gstin.trim() && !GSTIN_RE.test(form.gstin.trim())) {
    errors.gstin = "That does not look like a valid GSTIN.";
  }
  if (form.pan.trim() && !PAN_RE.test(form.pan.trim())) {
    errors.pan = "That does not look like a valid PAN.";
  }

  if (form.ip_address.trim() && !IP_RE.test(form.ip_address.trim())) {
    errors.ip_address = "Enter a valid IPv4 address, e.g. 10.0.4.21.";
  }
  return errors;
}


export default function CustomerForm() {
  const { id } = useParams();
  const isEdit = Boolean(id);
  const navigate = useNavigate();

  const [form, setForm] = useState(EMPTY);
  const [touched, setTouched] = useState({});
  const [serverErrors, setServerErrors] = useState({});
  const [loading, setLoading] = useState(isEdit);
  const [loadError, setLoadError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const [zones, setZones] = useState([]);
  const [localities, setLocalities] = useState([]);
  const [allAreas, setAllAreas] = useState([]);
  const [allBuildings, setAllBuildings] = useState([]);
  const [providers, setProviders] = useState([]);
  const [l2sProviderId, setL2sProviderId] = useState("");

  // Plan choice, KYC files and the address mirror are not Customer columns, so
  // they live beside the form rather than inside it.
  const [plan, setPlan] = useState(EMPTY_PLAN);
  const [planAmounts, setPlanAmounts] = useState({});
  const [files, setFiles] = useState({});
  const [fileErrors, setFileErrors] = useState({});
  const [sameAsBilling, setSameAsBilling] = useState(!isEdit);
  const [documents, setDocuments] = useState([]);
  const { toast, confirm } = useToast();

  const clientErrors = useMemo(() => validate(form), [form]);
  const errors = useMemo(
    () => ({ ...clientErrors, ...serverErrors }),
    [clientErrors, serverErrors],
  );
  const isValid = Object.keys(clientErrors).length === 0;

  /* ---- dropdown data: one request each, in parallel, on mount ---------- */
  useEffect(() => {
    let cancelled = false;
    const pick = (payload) => (Array.isArray(payload?.data) ? payload.data : payload?.data || []);

    Promise.allSettled([
      get("/masters/zones", { per_page: 200 }),
      get("/masters/localities", { per_page: 200 }),
      get("/masters/areas", { per_page: 200 }),
      get("/masters/buildings", { per_page: 500 }),
      get("/service-providers"),
    ]).then(([z, l, a, b, sp]) => {
      if (cancelled) return;
      if (z.status === "fulfilled") setZones(pick(z.value));
      if (l.status === "fulfilled") setLocalities(pick(l.value));
      if (a.status === "fulfilled") setAllAreas(pick(a.value));
      if (b.status === "fulfilled") setAllBuildings(pick(b.value));
      if (sp.status === "fulfilled") {
        const list = pick(sp.value);
        setProviders(list);
        const l2s = list.find((p) => p.name === "L2S");
        if (l2s) setL2sProviderId(String(l2s.id));
      }
    });

    return () => { cancelled = true; };
  }, []);

  /* ---- set L2S default for new customers once providers load ----------- */
  useEffect(() => {
    if (!isEdit && l2sProviderId && !form.service_provider_id) {
      setForm((prev) => ({ ...prev, service_provider_id: l2sProviderId }));
    }
  }, [l2sProviderId, isEdit]);

  /* ---- existing record when editing ------------------------------------ */
  useEffect(() => {
    if (!isEdit) return undefined;
    let cancelled = false;

    setLoading(true);
    setLoadError(null);
    get(`/customers/${id}`)
      .then((payload) => {
        if (cancelled) return;
        const customer = payload?.data?.customer || payload?.customer || payload?.data;
        if (!customer) throw new Error("not_found");
        // Only copy keys the form knows about; never carry a password across.
        const next = { ...EMPTY };
        for (const key of Object.keys(EMPTY)) {
          if (key === "password") continue;
          const value = customer[key];
          if (value !== undefined && value !== null) next[key] = value;
        }
        next.is_active = Boolean(customer.is_active);
        setForm(next);
        setDocuments(customer.documents || []);
        // On an existing record the two addresses are whatever they are; do
        // not silently overwrite one with the other.
        setSameAsBilling(false);
      })
      .catch((err) => { if (!cancelled) setLoadError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [id, isEdit]);

  function change(event) {
    const { name, type, value, checked } = event.target;
    setForm((prev) => {
      const next = { ...prev, [name]: type === "checkbox" ? checked : value };
      // Cascade: reset area and building when locality changes
      if (name === "locality") {
        next.area = "";
        next.building = "";
      }
      // Cascade: reset building when area changes
      if (name === "area") {
        next.building = "";
      }
      return next;
    });
    // A server-side error on this field is stale as soon as it is edited.
    setServerErrors((prev) => {
      if (!(name in prev)) return prev;
      const next = { ...prev };
      delete next[name];
      return next;
    });
  }

  /* ---- cascading address dropdowns -------------------------------------- */
  // Derive which areas exist for the selected locality (from buildings table)
  const filteredAreas = useMemo(() => {
    if (!form.locality) return allAreas;
    const locObj = localities.find((l) => l.name === form.locality);
    if (!locObj) return allAreas;
    const areaIds = new Set(
      allBuildings.filter((b) => b.locality_id === locObj.id).map((b) => b.area_id)
    );
    if (areaIds.size === 0) return allAreas;
    return allAreas.filter((a) => areaIds.has(a.id));
  }, [form.locality, allAreas, allBuildings, localities]);

  // Derive which buildings exist for the selected locality + area
  const filteredBuildings = useMemo(() => {
    if (!form.locality && !form.area) return allBuildings;
    const locObj = localities.find((l) => l.name === form.locality);
    const areaObj = allAreas.find((a) => a.name === form.area);
    return allBuildings.filter((b) => {
      if (locObj && b.locality_id !== locObj.id) return false;
      if (areaObj && b.area_id !== areaObj.id) return false;
      return true;
    });
  }, [form.locality, form.area, allBuildings, localities, allAreas]);

  /* ---- auto-generate billing address from fields ----------------------- */
  const autoBillingAddress = useMemo(() => {
    const parts = [form.flat_no, form.building, form.area, form.locality]
      .filter((v) => v && v !== '-');
    if (parts.length === 0) return "";
    return parts.join(', ') + ', Navi Mumbai, Maharashtra';
  }, [form.flat_no, form.building, form.area, form.locality]);

  /** Mirror the billing address into the primary one while the box is ticked. */
  const primaryAddress = sameAsBilling ? (autoBillingAddress || form.billing_address) : form.primary_address;

  function chooseFile(field, fileList) {
    const file = fileList?.[0];
    setFileErrors((prev) => ({ ...prev, [field]: undefined }));

    if (!file) {
      setFiles((prev) => { const next = { ...prev }; delete next[field]; return next; });
      return;
    }
    // Check the size here rather than letting an 80 MB scan upload and fail:
    // the operator finds out immediately instead of after a long wait.
    if (file.size > MAX_UPLOAD_BYTES) {
      setFileErrors((prev) => ({
        ...prev,
        [field]: `${file.name} is ${(file.size / 1024 / 1024).toFixed(1)} MB. The limit is 8 MB.`,
      }));
      return;
    }
    setFiles((prev) => ({ ...prev, [field]: file }));
  }

  /**
   * Delete one KYC file from the record.
   *
   * Confirmed rather than instant: these are compliance documents, the delete
   * removes the file from disk as well as the row, and there is no undo.
   */
  async function removeDocument(slot) {
    const confirmed = await confirm({
      title: `Remove the ${slot.label}?`,
      message: "The file is deleted from the server as well as the record. "
        + "It cannot be recovered - the customer would have to supply it again.",
      confirmLabel: "Remove",
      tone: "danger",
    });
    if (!confirmed) return;

    try {
      const response = await del(`/customers/${id}/documents/${slot.field}`);
      setDocuments((response?.data ?? response)?.documents || []);
      toast.success(`${slot.label} removed.`);
    } catch (deleteError) {
      toast.error(deleteError.detail
        || `The ${slot.label} could not be removed. ${deleteError.message || ""}`);
    }
  }

  /** Upload whatever KYC files were staged. Returns a warning string, or "". */
  async function uploadDocuments(customerId) {
    const staged = Object.entries(files);
    const types = KYC_SLOTS.filter((slot) => slot.typeField && form[slot.typeField]);
    if (!staged.length && !types.length) return "";

    const payload = new FormData();
    for (const [field, file] of staged) payload.append(field, file);
    for (const slot of types) payload.append(`${slot.field}_type`, form[slot.typeField]);

    try {
      const response = await upload(`/customers/${customerId}/documents`, payload);
      const rejected = (response?.data ?? response)?.rejected || [];
      return rejected.length
        ? `The customer was saved, but ${rejected.length} document(s) were not: `
          + rejected.map((r) => `${r.field} — ${r.reason}`).join("; ")
        : "";
    } catch (uploadError) {
      return "The customer was saved, but the KYC files could not be uploaded: "
        + (uploadError.detail || uploadError.message);
    }
  }

  const blur = (event) => setTouched((prev) => ({ ...prev, [event.target.name]: true }));
  const errorFor = (field) =>
    touched[field] || serverErrors[field] ? errors[field] : undefined;

  async function submit(event) {
    event.preventDefault();
    setTouched(Object.fromEntries(Object.keys(EMPTY).map((k) => [k, true])));
    if (!isValid || busy) return; // duplicate-submit guard

    setBusy(true);
    setError(null);
    setServerErrors({});

    // Send only what the API accepts, and drop empty optional strings so we
    // store NULL rather than "".
    const payload = {};
    for (const [key, value] of Object.entries(form)) {
      if (key === "password" && !value) continue;
      if (key === "billing_address") continue;
      payload[key] = typeof value === "string" && value.trim() === "" ? null : value;
    }
    payload.billing_address = autoBillingAddress || null;
    payload.primary_address = primaryAddress?.trim() ? primaryAddress : null;
    payload.billing_type = "Prepaid";
    if (!payload.service_provider_id && l2sProviderId) {
      payload.service_provider_id = l2sProviderId;
    }

    // A plan is only assigned on create. Changing an existing customer's plan
    // goes through Assign/Change on the Plan tab, which terminates the old one
    // and keeps the history - editing the record must not do that silently.
    if (!isEdit && plan.plan_id) {
      payload.plan_id = Number(plan.plan_id);
      payload.plan_start_date = plan.plan_start_date;
    }

    try {
      let customerId = id;
      let warning = "";

      if (isEdit) {
        await put(`/customers/${id}`, payload);
      } else {
        const created = await post("/customers", payload);
        const record = created?.data ?? created;
        customerId = record?.id;
        if (record?.plan_warning) warning = record.plan_warning;
      }

      if (customerId) {
        const uploadWarning = await uploadDocuments(customerId);
        warning = [warning, uploadWarning].filter(Boolean).join(" ");
      }

      // The record saved; anything that only partly worked is a warning on the
      // way out, not a failure that discards what was just entered.
      if (warning) toast.warning(warning, { duration: 12000 });
      else toast.success(isEdit ? "Customer updated." : "Customer created.");

      navigate(customerId ? `/customers/${customerId}` : "/customers", { replace: true });
    } catch (err) {
      // The API answers 409 username_taken - surface it on the right field.
      if (err.message === "username_taken" || err.message === "username_unavailable") {
        setServerErrors({ username: err.detail || "That username is already in use." });
      } else if (err.message === "first_name_last_name_mobile_required") {
        setServerErrors({
          first_name: "Required.",
          last_name: "Required.",
          mobile: "Required.",
        });
      } else if (err.message === "invalid_field_value") {
        // A DB-level enum rejection - name the fields most likely at fault.
        setError(Object.assign(
          new Error("One of the selected values is not allowed."),
          { detail: err.detail },
        ));
      } else if (err.message === "duplicate_value") {
        setError(Object.assign(
          new Error("A customer with that username or reference ID already exists."),
          { detail: err.detail },
        ));
      } else {
        setError(err);
      }
    } finally {
      setBusy(false);
    }
  }

  // Live username check.
  //
  // Asks the SAME endpoint the create call will use, so the form cannot show
  // a green tick for a name that is then refused on submit. Skipped entirely
  // when editing, because the username is fixed once the account exists.
  const typedUsername = (form.username || "").trim();
  const debouncedUsername = useDebounced(typedUsername, 400);
  const [nameCheck, setNameCheck] = useState(null);

  useEffect(() => {
    if (isEdit || debouncedUsername.length < 3) { setNameCheck(null); return undefined; }
    let cancelled = false;
    setNameCheck({ state: "checking" });
    get("/customers/username-available", { username: debouncedUsername })
      .then((response) => {
        if (cancelled) return;
        const payload = response?.data ?? response;
        setNameCheck({ state: payload.available ? "free" : "taken",
                       reason: payload.reason });
      })
      // A failed check must not read as "available" - stay silent and let the
      // server decide on submit.
      .catch(() => !cancelled && setNameCheck(null));
    return () => { cancelled = true; };
  }, [debouncedUsername, isEdit]);

  // Only trust the answer for the value currently in the box: the debounce
  // means a stale reply can land after the operator has typed something else.
  const nameStatus = debouncedUsername === typedUsername ? nameCheck : null;

  if (loading) return <Loading label="Loading customer" />;
  if (loadError) {
    return (
      <section className="page">
        <ErrorNote error={loadError} onRetry={() => window.location.reload()} />
        <Link className="btn" to="/customers">Back to customers</Link>
      </section>
    );
  }

  const field = (name, label, extra = {}) => (
    <Field
      name={name}
      label={label}
      value={form[name] ?? ""}
      onChange={change}
      onBlur={blur}
      error={errorFor(name)}
      {...extra}
    />
  );

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>{isEdit ? "Edit customer" : "Add customer"}</h1>
          <p>
            {isEdit
              ? "Update this customer's contact, address and billing details."
              : "Create a new customer account. Fields marked * are required."}
          </p>
        </div>
        <Link className="btn" to={isEdit ? `/customers/${id}` : "/customers"}>Cancel</Link>
      </div>

      <ErrorNote error={error} />
      {error?.detail && <div className="hint" style={{ marginTop: -8, marginBottom: 12 }}>{error.detail}</div>}

      <form className="form-page" onSubmit={submit} noValidate>
        <fieldset className="panel">
          <legend>Personal Information</legend>
          <div className="field-grid">
            {field("title", "Title", { as: "select", options: TITLES })}
            {field("customer_type", "Customer Type", { as: "select", options: CUSTOMER_TYPES })}
            {form.customer_type !== "Residential"
              ? field("company_name", "Company Name", { required: true })
              : <div className="field" aria-hidden="true" />}

            {field("first_name", "First Name", { required: true })}
            {field("middle_name", "Middle Name")}
            {field("last_name", "Last Name", { required: true })}

            {field("email", "Email", { type: "email", inputMode: "email" })}
            {field("home_phone", "Home Phone", { type: "tel", inputMode: "numeric" })}
            {field("mobile", "Mobile", { required: true, type: "tel", inputMode: "numeric" })}

            {field("gstin", "GSTIN No")}
            {field("pan", "Pan No")}
            {field("aadhar", "Adhar Card No")}

            {field("registration_date", "Registration Date", { type: "date", required: true })}
            {field("zone", "Zone", {
              as: "select", options: zones.map((z) => z.name), allowEmpty: "-Select-",
            })}
            {field("tax_type", "Tax Type", { as: "select", options: TAX_TYPES })}

            {field("reference_id", "Refrence ID")}
            {field("connection_type", "Connection Type", { as: "select", options: CONNECTION_TYPES })}
          </div>
        </fieldset>

        <fieldset className="panel">
          <legend>Billing Address</legend>
          <div className="field-grid">
            {field("flat_no", "Flat No.")}
            {field("locality", "Locality", {
              as: "select", options: localities.map((l) => l.name), allowEmpty: "-Select-",
            })}
            {field("area", "Area", {
              as: "select", options: filteredAreas.map((a) => a.name), allowEmpty: "-Select-",
            })}
            {field("building", "Building", {
              as: "select", options: ["-", ...filteredBuildings.map((b) => b.name)], allowEmpty: "-Select-",
            })}
            <div className="field" style={{ gridColumn: "span 2" }}>
              <label>Billing Address (auto-generated)</label>
              <textarea readOnly rows={2} value={autoBillingAddress}
                style={{ background: "#f5f5f5", cursor: "default", resize: "none" }} />
            </div>
          </div>
        </fieldset>

        <fieldset className="panel">
          <legend>Primary Address</legend>
          <label className="check-row">
            <input type="checkbox" checked={sameAsBilling}
                   onChange={(event) => setSameAsBilling(event.target.checked)} />
            <span>Same as Billing Address</span>
          </label>
          {!sameAsBilling && (
            <div className="field-grid">
              {field("primary_address", "Installation address", { as: "textarea", span: 2 })}
            </div>
          )}
          {sameAsBilling && (
            <p className="hint">
              The installation address will be saved as{" "}
              {autoBillingAddress
                ? <strong>{autoBillingAddress}</strong>
                : "the billing address once you fill in the address fields above"}.
            </p>
          )}
        </fieldset>

        <fieldset className="panel">
          <legend>KYC</legend>
          <div className="field-grid">
            {KYC_SLOTS.map((slot) => (
              <KycSlot key={slot.field} slot={slot} form={form} onChange={change}
                       file={files[slot.field]} error={fileErrors[slot.field]}
                       existing={documents.find((d) => d.slot === slot.field)}
                       onRemove={id ? removeDocument : undefined}
                       onFile={(list) => chooseFile(slot.field, list)} />
            ))}
          </div>
          <p className="hint">
            PDF or image, up to 8 MB each. Uploading again replaces the file
            already on record.
          </p>
        </fieldset>

        <fieldset className="panel" id="username">
          <legend>Assign Username</legend>
          <div className="field-grid">
            {field("username", "Username", {
              autoComplete: "off",
              // Fixed once issued: it is the identity every log line, receipt
              // and message already written refers to, and the API refuses to
              // change it - so the field must not invite the attempt.
              readOnly: isEdit,
              disabled: isEdit,
              hint: isEdit
                ? "The username cannot be changed after the account is created."
                : undefined,
              status: nameStatus,
            })}
            {field("password", "Password", {
              type: "password",
              autoComplete: "new-password",
              hint: isEdit ? "Leave blank to keep the current password." : "Default: 123456. Customer can change after login.",
            })}
            {field("ip_address", "Ip Address", { placeholder: "10.0.4.21" })}
            {field("service_provider_id", "Service Provider", {
              as: "select",
              options: providers.map((provider) => ({
                value: String(provider.id), label: provider.name,
              })),
              allowEmpty: "-Select-",
            })}
            {field("ipacct_id", "Ipacct Id")}
            {field("invoice_date", "Invoice Date", { type: "date" })}
          </div>
        </fieldset>

        {!isEdit && (
          <fieldset className="panel">
            <legend>Assign Plan</legend>
            <div className="field-grid">
              <Field name="plan_start_date" label="Plan Start Date" type="date"
                     value={plan.plan_start_date} required
                     onChange={(event) =>
                       setPlan({ ...plan, plan_start_date: event.target.value })} />
            </div>
            <PlanPicker value={plan.plan_id}
                        overrides={planAmounts}
                        onChange={(planId) => setPlan({ ...plan, plan_id: planId })}
                        onOverride={(planId, key, value) => setPlanAmounts((prev) => ({
                          ...prev, [planId]: { ...prev[planId], [key]: value },
                        }))} />
            <p className="hint">
              Optional — you can save the customer now and assign a plan from
              their Plan tab later.
            </p>
          </fieldset>
        )}

        <fieldset className="panel">
          <legend>Status</legend>
          <label className="check-row">
            <input type="checkbox" name="is_active" checked={Boolean(form.is_active)} onChange={change} />
            <span>Active — an inactive customer cannot sign in to the portal.</span>
          </label>
        </fieldset>

        <div className="form-actions">
          <Link className="btn" to={isEdit ? `/customers/${id}` : "/customers"}>Cancel</Link>
          <button className="btn primary" disabled={busy}>
            {busy ? "Saving…" : isEdit ? "Save changes" : "Create customer"}
          </button>
        </div>
      </form>
    </section>
  );
}

/* ------------------------------------------------------------------ */

/** One KYC slot: an optional document type, then the file itself. */
function KycSlot({ slot, form, onChange, onFile, file, error, existing, onRemove }) {
  const inputId = `kyc-${slot.field}`;
  const listId = `${inputId}-types`;
  const [removing, setRemoving] = useState(false);

  async function remove() {
    setRemoving(true);
    try { await onRemove(slot); } finally { setRemoving(false); }
  }

  return (
    <div className={`field kyc-slot${error ? " has-error" : ""}`}>
      <label htmlFor={inputId}>{slot.label}</label>

      {slot.typeField && (
        <>
          <input list={listId} name={slot.typeField} value={form[slot.typeField] || ""}
                 placeholder={`-Select ${slot.label}-`} onChange={onChange} />
          <datalist id={listId}>
            {slot.suggestions.map((option) => <option key={option} value={option} />)}
          </datalist>
        </>
      )}

      <input id={inputId} type="file" accept={ACCEPT}
             onChange={(event) => onFile(event.target.files)} />

      {error ? <small className="field-error">{error}</small>
        : file ? <small className="ok">{file.name} ready to upload</small>
          : existing?.url
            ? <small>
                <a href={existing.url} target="_blank" rel="noreferrer noopener">
                  View the file on record
                </a>
              </small>
            : <small>Nothing uploaded yet.</small>}
    </div>
  );
}

function Field({
  name, label, value, onChange, onBlur, error, hint, status,
  as = "input", options = [], allowEmpty, required, span, ...rest
}) {
  const id = `f-${name}`;
  const describedBy = error ? `${id}-err` : hint ? `${id}-hint` : undefined;

  return (
    <div className={`field${span === 2 ? " span-2" : ""}${error ? " has-error" : ""}`}>
      <label htmlFor={id}>
        {label} {required && <abbr title="required">*</abbr>}
      </label>

      {as === "select" ? (
        <select id={id} name={name} value={value} onChange={onChange} onBlur={onBlur}
                aria-invalid={Boolean(error)} aria-describedby={describedBy} {...rest}>
          {allowEmpty && <option value="">{allowEmpty}</option>}
          {/* Options may be plain strings (enums) or {value,label} pairs (foreign
              keys, where the id is stored but the name is shown). */}
          {options.map((option) => {
            const isPair = option !== null && typeof option === "object";
            const optionValue = isPair ? option.value : option;
            return (
              <option key={String(optionValue)} value={optionValue}>
                {isPair ? option.label : option}
              </option>
            );
          })}
        </select>
      ) : as === "textarea" ? (
        <textarea id={id} name={name} value={value} onChange={onChange} onBlur={onBlur} rows={3}
                  aria-invalid={Boolean(error)} aria-describedby={describedBy} {...rest} />
      ) : as === "money" ? (
        // Whole rupees, no spinner - see components/MoneyInput.jsx.
        <MoneyInput id={id} name={name} value={value} onChange={onChange} onBlur={onBlur}
                    aria-invalid={Boolean(error)} aria-describedby={describedBy} {...rest} />
      ) : (
        <input id={id} name={name} value={value} onChange={onChange} onBlur={onBlur}
               aria-invalid={Boolean(error)} aria-describedby={describedBy} {...rest} />
      )}

      {/* A live availability verdict outranks the generic hint but not a real
          validation error - the error is about what they typed, the status is
          about whether it can be used. */}
      {status && !error && (
        <small className={`field-status is-${status.state}`} role="status">
          {status.state === "checking" ? "Checking…"
            : status.state === "free" ? "Available"
              : status.reason}
        </small>
      )}

      {error ? (
        <small className="field-error" id={`${id}-err`}>{error}</small>
      ) : hint ? (
        <small id={`${id}-hint`}>{hint}</small>
      ) : null}
    </div>
  );
}
