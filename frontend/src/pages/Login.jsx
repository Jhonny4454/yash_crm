import { Navigate, Link, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { readableError } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { IDLE_MINUTES, signOutReason } from "../session/idle";
import "../styles/Login.css";
import logoImage from "../assets/logo.jpg";

export default function Login({ audience = "staff" }) {
  const { signIn, isAuthenticated, isCustomer, company } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState(""); const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState(null); const [showPassword, setShowPassword] = useState(false);
  // A password reset ends here, and ForgotPassword passes the fact along in
  // navigation state. Consumed once: reading it in the render body would keep
  // showing the notice on every re-render (and a back-button revisit).
  const [resetNotice] = useState(() => location.state?.passwordReset ? "Your password has been reset. Sign in with your new password." : null);
  // Read once, on the first render, because signOutReason() consumes the flag.
  // Reading it in the render body would clear it on a re-render and the
  // message would vanish the moment the user typed a character.
  const [reason] = useState(signOutReason);
  // <body> carries padding for the admin shell's fixed top bar and footer.
  // A sign-in screen has neither, so that padding is 92px of pure scroll on a
  // page that is meant to fill the viewport exactly. CSS cannot select a
  // parent, so the class goes on from here and comes off on the way out.
  useEffect(() => {
    document.body.classList.add("auth-screen");
    return () => document.body.classList.remove("auth-screen");
  }, []);

  // `busy` only disables the button on the NEXT render, so a fast double-click
  // or a double Enter fires two logins before React catches up - which is
  // exactly what the server log showed. A ref flips synchronously.
  const submitting = useRef(false);
  const formRef = useRef(null);

  /* Take the credentials off the FORM, not out of React state.
   *
   * These are controlled inputs, and a browser password manager fills them by
   * assigning to `input.value` directly - which does not fire React's
   * onChange. So the boxes visibly contain the username and password, the
   * `required` attribute is satisfied because the DOM really does hold values,
   * and React state is still "". The app then posted
   * {"username":"","password":""} and the server answered
   * `username_and_password_required` - an error that accuses the operator of
   * leaving fields blank while they are looking at fields that are not blank.
   *
   * Reproduced exactly by setting .value from the console and pressing Sign in.
   *
   * The DOM is the thing the person actually filled in, so read that, and fall
   * back to state only if the form has gone (it has not, but a null ref should
   * never throw here of all places). */
  function credentials() {
    const form = formRef.current;
    const fromDom = form
      ? { username: form.elements.username?.value ?? "",
          password: form.elements.password?.value ?? "" }
      : { username: "", password: "" };
    return {
      username: (fromDom.username || username || "").trim(),
      password: fromDom.password || password || "",
    };
  }

  if (isAuthenticated) return <Navigate to={isCustomer ? "/customer" : "/"} replace />;
  async function submit(event) {
    event.preventDefault();
    if (submitting.current) return;

    const entered = credentials();
    // Keep the visible state in step with what was actually submitted, so a
    // failed autofilled attempt does not leave the boxes and the component
    // disagreeing about what is in them.
    if (entered.username !== username) setUsername(entered.username);
    if (entered.password !== password) setPassword(entered.password);

    if (!entered.username || !entered.password) {
      setError(new Error(entered.username
        ? "Enter your password."
        : "Enter your username and password."));
      return;
    }

    submitting.current = true;
    setBusy(true); setError(null);
    try {
      const result = await signIn({ ...entered, audience });
      navigate(result.audience === "customer" ? "/customer" : "/", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  const customer = audience === "customer";
  return <main className={`login-page ${customer ? "is-customer" : "is-staff"}`}><section className="login-card"><div className="login-logo"><img src={company?.logo_url || logoImage} alt="YASH Internet Services" /><h1>{company?.name || "YASH Internet Services"}</h1><span className="tagline">{customer ? "Customer self-service portal" : "Staff operations portal"}</span><span className="portal-badge">{customer ? "Customer" : "Staff / Admin"}</span></div>{reason === "idle" && !error && <div className="login-notice" role="status">{`Signed out after ${IDLE_MINUTES} minutes with no activity. Please sign in again.`}</div>}{resetNotice && !error && <div className="login-notice" role="status">{resetNotice}</div>}{error && <div className="login-error" role="alert">{readableError(error)}<button type="button" onClick={() => setError(null)}>×</button></div>}<form ref={formRef} onSubmit={submit}><div className="login-field"><label htmlFor="username">{customer ? "Username, mobile or reference ID" : "Username"}</label><input id="username" name="username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required autoFocus /></div><div className="login-field login-password-wrap"><label htmlFor="password">Password</label><input id="password" name="password" type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /><button className="login-toggle" type="button" aria-label="Show or hide password" onClick={() => setShowPassword((shown) => !shown)}>{showPassword ? "Hide" : "Show"}</button></div><button className="login-btn" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button></form><div className="login-footer">{customer ? <><Link to="/login">Staff sign in</Link><Link to="/customer/forgot-password">Forgot password?</Link></> : <><Link to="/customer/login">Customer sign in</Link><Link to="/forgot-password">Forgot password?</Link></>}</div></section></main>;
}
