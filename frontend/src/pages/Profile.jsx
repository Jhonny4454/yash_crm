import { useEffect, useState } from "react";
import { post, put } from "../api/client";
import { ErrorNote, readableError } from "../components/ui";
import { useAuth } from "../context/AuthContext";

/**
 * My profile - staff details plus a password change.
 *
 * Replaces the old Jinja2 profile.html. The details panel is read-mostly
 * (role is shown but not editable; only an admin can change roles from the
 * Staff screen), and the password form enforces the same rules the API does
 * so the user gets feedback before a round trip.
 */
export default function Profile() {
  const { user, refreshProfile } = useAuth();

  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <h1>My profile</h1>
          <p>Your account details and password.</p>
        </div>
      </div>

      <div className="grid-two">
        <DetailsPanel user={user} onSaved={refreshProfile} />
        <PasswordPanel />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */

function DetailsPanel({ user, onSaved }) {
  const [form, setForm] = useState({ full_name: "", email: "", mobile: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  // Seed the form once the session has loaded (or when the user switches).
  useEffect(() => {
    setForm({
      full_name: user?.full_name || "",
      email: user?.email || "",
      mobile: user?.mobile || "",
    });
  }, [user?.id, user?.full_name, user?.email, user?.mobile]);

  function change(event) {
    const { name, value } = event.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    setSaved(false);
  }

  async function submit(event) {
    event.preventDefault();
    if (busy) return; // guards against a double click firing two requests
    setBusy(true);
    setError(null);
    try {
      await put("/auth/staff/me", form);
      await onSaved?.();
      setSaved(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel stack" onSubmit={submit}>
      <h2>My details</h2>

      <div className="profile-readonly">
        <div>
          <span>Username</span>
          <strong>{user?.username || "—"}</strong>
        </div>
        <div>
          <span>Role</span>
          <strong className="pill ok">{user?.role || "staff"}</strong>
        </div>
      </div>

      <ErrorNote error={error} />
      {saved && <div className="alert success" role="status">Your details were saved.</div>}

      <label>
        Full name
        <input
          name="full_name"
          value={form.full_name}
          onChange={change}
          maxLength={120}
          autoComplete="name"
        />
      </label>

      <label>
        Email
        <input
          name="email"
          type="email"
          value={form.email}
          onChange={change}
          maxLength={120}
          autoComplete="email"
        />
      </label>

      <label>
        Mobile
        <input
          name="mobile"
          type="tel"
          inputMode="numeric"
          value={form.mobile}
          onChange={change}
          maxLength={20}
          autoComplete="tel"
        />
      </label>

      <button className="btn primary" disabled={busy}>
        {busy ? "Saving…" : "Save details"}
      </button>
    </form>
  );
}

/* ------------------------------------------------------------------ */

const MIN_PASSWORD = 6;

function PasswordPanel() {
  const empty = { old_password: "", new_password: "", confirm_password: "" };
  const [form, setForm] = useState(empty);
  const [touched, setTouched] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  // Same rules the API enforces, checked here so the user sees them immediately.
  const errors = {};
  if (!form.old_password) errors.old_password = "Enter your current password.";
  if (form.new_password.length < MIN_PASSWORD) {
    errors.new_password = `Use at least ${MIN_PASSWORD} characters.`;
  } else if (form.new_password === form.old_password) {
    errors.new_password = "The new password must be different.";
  }
  if (form.confirm_password !== form.new_password) {
    errors.confirm_password = "The two passwords do not match.";
  }
  const isValid = Object.keys(errors).length === 0;

  function change(event) {
    const { name, value } = event.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    setDone(false);
  }

  const blur = (event) => setTouched((prev) => ({ ...prev, [event.target.name]: true }));
  const showError = (field) => (touched[field] ? errors[field] : undefined);

  async function submit(event) {
    event.preventDefault();
    setTouched({ old_password: true, new_password: true, confirm_password: true });
    if (!isValid || busy) return;

    setBusy(true);
    setError(null);
    try {
      await post("/auth/staff/change-password", form);
      setForm(empty);
      setTouched({});
      setDone(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel stack" onSubmit={submit} noValidate>
      <h2>Change password</h2>

      <ErrorNote error={error} />
      {done && <div className="alert success" role="status">Your password has been changed.</div>}

      <Field
        label="Current password"
        name="old_password"
        value={form.old_password}
        onChange={change}
        onBlur={blur}
        error={showError("old_password")}
        autoComplete="current-password"
      />
      <Field
        label="New password"
        name="new_password"
        value={form.new_password}
        onChange={change}
        onBlur={blur}
        error={showError("new_password")}
        hint={`At least ${MIN_PASSWORD} characters.`}
        autoComplete="new-password"
      />
      <Field
        label="Confirm new password"
        name="confirm_password"
        value={form.confirm_password}
        onChange={change}
        onBlur={blur}
        error={showError("confirm_password")}
        autoComplete="new-password"
      />

      <button className="btn primary" disabled={busy}>
        {busy ? "Changing…" : "Change password"}
      </button>
    </form>
  );
}

function Field({ label, name, value, onChange, onBlur, error, hint, autoComplete }) {
  const [visible, setVisible] = useState(false);
  return (
    <label className={error ? "has-error" : undefined}>
      {label}
      <span className="input-with-toggle">
        <input
          name={name}
          type={visible ? "text" : "password"}
          value={value}
          onChange={onChange}
          onBlur={onBlur}
          autoComplete={autoComplete}
          aria-invalid={Boolean(error)}
        />
        <button
          type="button"
          className="reveal"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Hide password" : "Show password"}
          tabIndex={-1}
        >
          {visible ? "Hide" : "Show"}
        </button>
      </span>
      {error ? <small className="field-error">{error}</small> : hint ? <small>{hint}</small> : null}
    </label>
  );
}

export { Profile };
