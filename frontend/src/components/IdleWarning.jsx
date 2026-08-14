import { useAuth } from "../context/AuthContext";
import "../styles/IdleWarning.css";

/**
 * The last few seconds of an idle session, made visible.
 *
 * An idle timeout with no warning means an operator who looked away mid-form
 * comes back to a login screen and an empty form. The countdown costs nothing
 * and turns a silent data loss into a keypress.
 *
 * Any real activity - including moving the mouse to reach this dialog -
 * cancels it, so the button is a fallback rather than the main way out.
 *
 * The button is deliberately NOT autofocused. Stealing focus from a half-typed
 * form is precisely the wrong thing to do to someone who is about to lose it,
 * and role="alertdialog" with aria-live already announces this to a screen
 * reader without moving the caret.
 */
export default function IdleWarning() {
  const { idleIn, isAuthenticated, staySignedIn } = useAuth();
  if (!isAuthenticated || idleIn == null) return null;

  const seconds = Math.max(0, Math.ceil(idleIn / 1000));

  return (
    <div className="idle-warning" role="alertdialog" aria-live="assertive"
      aria-labelledby="idle-warning-title">
      <div className="idle-warning-card">
        <span className="idle-warning-ring" aria-hidden="true">{seconds}</span>
        <div>
          <strong id="idle-warning-title">Signing you out</strong>
          <p>
            No activity for a while. You will be signed out in {seconds} second
            {seconds === 1 ? "" : "s"}.
          </p>
        </div>
        <button type="button" className="btn primary" onClick={staySignedIn}>
          Stay signed in
        </button>
      </div>
    </div>
  );
}
