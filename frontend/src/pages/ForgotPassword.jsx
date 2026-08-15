import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { post } from "../api/client";
import { readableError } from "../components/ui";
import "../styles/Login.css";

/**
 * Password reset by one-time code, for both staff and customers.
 *
 * Staff had no route here at all: the admin login offered no way back in, so
 * a forgotten admin password meant editing the database by hand. The two
 * flows differ only in which endpoints they call and what the account is
 * identified by, so they share a screen rather than being copied.
 */
const FLOWS = {
  customer: {
    title: "Reset portal password",
    identifierLabel: "Username, mobile or reference ID",
    requestUrl: "/auth/customer/forgot-password",
    resetUrl: "/auth/customer/reset-password",
    identifierKey: "identifier",
    backTo: "/customer/login",
    backLabel: "Back to customer sign in",
  },
  staff: {
    title: "Reset admin password",
    identifierLabel: "Username",
    requestUrl: "/auth/staff/forgot-password",
    resetUrl: "/auth/staff/reset-password",
    identifierKey: "username",
    backTo: "/login",
    backLabel: "Back to staff sign in",
  },
};

export default function ForgotPassword({ audience = "customer" }) {
  const flow = FLOWS[audience] || FLOWS.customer;
  const navigate = useNavigate();

  const [step, setStep] = useState("request");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // `busy` only disables the button on the NEXT render, so a fast double-tap
  // fires two submits before React catches up. A ref flips synchronously.
  const submitting = useRef(false);

  /* Same reason as the sign-in form: a password manager fills these by
   * assigning to input.value, which does not fire React's onChange, so the
   * box shows a value the component has never seen. Read the form, not the
   * state - see the comment in pages/Login.jsx. */
  function fieldValue(event, name, fallback) {
    const el = event?.target?.elements?.[name];
    return (el && typeof el.value === "string" && el.value !== "")
      ? el.value : fallback;
  }

  async function requestCode(event) {
    event.preventDefault();
    if (submitting.current) return;
    const enteredId = fieldValue(event, "identifier", identifier).trim();
    if (enteredId !== identifier) setIdentifier(enteredId);
    if (!enteredId) return setError("Enter your username or mobile number.");
    submitting.current = true;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await post(flow.requestUrl,
                                  { [flow.identifierKey]: enteredId });
      const data = response?.data ?? response;

      // The API distinguishes "delivered" from "could not deliver". Reporting
      // both as success leaves someone waiting for a code that is not coming.
      if (data?.status === "not_sent" && data?.detail) {
        setNotice(data.detail);
      } else if (data?.masked_mobile) {
        setNotice(`A code has been sent to ${data.masked_mobile}. `
          + `It expires in ${data.expires_in_minutes || 10} minutes.`);
      } else {
        setNotice("If the account exists, a code has been sent to the mobile "
          + "number on file.");
      }
      setStep("reset");
    } catch (requestError) {
      setError(readableError(requestError));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  async function reset(event) {
    event.preventDefault();
    if (submitting.current) return;
    const enteredOtp = fieldValue(event, "otp", otp).trim();
    const enteredPassword = fieldValue(event, "password", password);
    const enteredConfirm = fieldValue(event, "confirm", confirm);
    if (enteredOtp !== otp) setOtp(enteredOtp);
    if (enteredPassword !== password) setPassword(enteredPassword);
    if (enteredConfirm !== confirm) setConfirm(enteredConfirm);

    if (!enteredOtp) return setError("Enter the code that was sent to you.");
    if (!enteredPassword) return setError("Choose a new password.");
    if (enteredPassword !== enteredConfirm) {
      return setError("The two passwords do not match.");
    }

    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      await post(flow.resetUrl, { otp: enteredOtp, password: enteredPassword });
      navigate(flow.backTo, { replace: true, state: { passwordReset: true } });
    } catch (resetError) {
      setError(resetError.detail || readableError(resetError));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-card">
        <div className="login-logo">
          <h1>{flow.title}</h1>
          <span className="tagline">
            Use the one-time code sent to the mobile number on the account.
          </span>
        </div>

        {error && <div className="login-error" role="alert">{error}</div>}
        {notice && <div className="login-notice">{notice}</div>}

        {step === "request" ? (
          <form onSubmit={requestCode}>
            <div className="login-field">
              <label htmlFor="identifier">{flow.identifierLabel}</label>
              <input id="identifier" name="identifier" value={identifier} autoFocus required
                     autoComplete="username"
                     onChange={(event) => setIdentifier(event.target.value)} />
            </div>
            {/* Enabled even when state says empty: a password manager fills the
                box directly, which never reaches React state, so a state-based
                disable would block a perfectly valid submit. Validation lives
                in the handler. */}
            <button className="login-btn" disabled={busy}>
              {busy ? "Sending…" : "Send code"}
            </button>
          </form>
        ) : (
          <form onSubmit={reset}>
            <div className="login-field">
              <label htmlFor="otp">Six-digit code</label>
              <input id="otp" inputMode="numeric" maxLength="6" required autoFocus
                     name="otp" autoComplete="one-time-code" value={otp}
                     onChange={(event) => setOtp(event.target.value)} />
            </div>
            <div className="login-field">
              <label htmlFor="password">New password</label>
              <input id="password" name="password" type="password" required minLength="6"
                     autoComplete="new-password" value={password}
                     onChange={(event) => setPassword(event.target.value)} />
            </div>
            <div className="login-field">
              <label htmlFor="confirm">Confirm new password</label>
              <input id="confirm" name="confirm" type="password" required minLength="6"
                     autoComplete="new-password" value={confirm}
                     onChange={(event) => setConfirm(event.target.value)} />
            </div>
            <button className="login-btn" disabled={busy}>
              {busy ? "Saving…" : "Reset password"}
            </button>
            <button type="button" className="login-link-btn"
                    onClick={() => { setStep("request"); setOtp(""); setError(null); }}>
              Send a new code
            </button>
          </form>
        )}

        <div className="login-footer">
          <Link to={flow.backTo}>{flow.backLabel}</Link>
        </div>
      </section>
    </main>
  );
}
