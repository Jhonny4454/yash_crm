import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AUTH_KEY, get, post, tokens } from "../api/client";
import { clearActivity, markActive, watchIdle } from "../session/idle";

const AuthContext = createContext(null);

function savedAuth() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_KEY) || "null");
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(savedAuth);
  // The API being unreachable is NOT the same as being signed out. Kept
  // separate so the shell can say "cannot reach the server" over the top of
  // the app the user was already using.
  const [offline, setOffline] = useState(false);
  // Milliseconds left before the idle sign-out, or null when not counting.
  const [idleIn, setIdleIn] = useState(null);

  const signOut = useCallback(async ({ reason } = {}) => {
    // Clear locally first. If the network call hangs, the operator has still
    // left the machine and the credentials must already be gone - waiting on
    // the server to agree is exactly the wrong order for a sign-out.
    const refresh = tokens.refresh;
    tokens.clear();
    clearActivity();
    setSession(null);
    setIdleIn(null);
    try {
      if (refresh) await post("/auth/logout", { refresh_token: refresh });
    } catch {
      // Logout remains local if the browser is offline or the token expired.
    }
    return reason || null;
  }, []);

  const persist = useCallback((next) => {
    localStorage.setItem(AUTH_KEY, JSON.stringify(next));
    setSession(next);
  }, []);

  const signIn = useCallback(async ({ username, password, audience = "staff" }) => {
    const payload = await post(`/auth/${audience}/login`,
      audience === "customer"
        ? { identifier: username, password }
        : { username, password },
    );
    const data = payload.data || payload;
    setOffline(false);
    tokens.set(data);
    // Start the idle clock from the sign-in, not from whatever stale stamp a
    // previous session left behind - otherwise a fresh login can inherit an
    // already-expired clock and bounce straight back out.
    markActive();
    const next = {
      audience,
      user: audience === "staff" ? data.user : data.customer,
      company: data.branding || null,
    };
    persist(next);
    return next;
  }, [persist]);

  const refreshProfile = useCallback(async () => {
    const active = savedAuth();
    if (!active || !tokens.access) return;
    try {
      const payload = await get(`/auth/${active.audience}/me`);
      const data = payload.data || payload;
      setOffline(false);
      persist({
        ...active,
        user: active.audience === "staff" ? data.user : data.customer,
        company: data.branding || active.company || null,
      });
    } catch (error) {
      // Every failure used to be treated as "your session is invalid", so a
      // backend restart threw the operator out to the login screen and threw
      // their tokens away with it - and logging back in was impossible until
      // the server returned. A server that is down says nothing at all about
      // whether the session is still good, so keep it and say what is wrong.
      if (error?.unreachable || (error?.status || 0) >= 500) {
        setOffline(true);
        setSession(active);
      } else {
        tokens.clear();
        clearActivity();
        setSession(null);
      }
    }
  }, [persist]);

  // Validate in the background, on mount. There is no gate in front of the
  // app while this runs: the saved session already says who is signed in, so
  // blocking every page load behind a "Restoring your session" screen bought
  // nothing and cost a full-screen flash on every refresh. If the server
  // disagrees, the catch above signs the user out a moment later.
  useEffect(() => {
    refreshProfile();
    const listener = () => signOut();
    window.addEventListener("unicrm:signed-out", listener);
    return () => window.removeEventListener("unicrm:signed-out", listener);
  }, [refreshProfile, signOut]);

  // --------------------------------------------------------- idle timeout --
  const signedIn = Boolean(session?.user);
  // watchIdle is started once per sign-in and must not be torn down and
  // rebuilt whenever signOut's identity changes, or the countdown restarts.
  const signOutRef = useRef(signOut);
  signOutRef.current = signOut;

  useEffect(() => {
    if (!signedIn) {
      setIdleIn(null);
      return undefined;
    }
    return watchIdle({
      onWarn: (remaining) => setIdleIn(remaining),
      onExpire: () => signOutRef.current({ reason: "idle" }),
    });
  }, [signedIn]);

  const staySignedIn = useCallback(() => {
    markActive();
    setIdleIn(null);
  }, []);

  /** Update the branding shown in the sidebar and on the login screen.
   *
   * Companies.jsx has always destructured this out of useAuth and called it
   * after saving the primary company - but it was never provided, so saving
   * branding threw "setCompany is not a function" and the logo only changed
   * after a re-login.
   */
  const setCompany = useCallback((company) => {
    setSession((current) => {
      if (!current) return current;
      const next = { ...current, company: company || null };
      localStorage.setItem(AUTH_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const value = useMemo(() => ({
    ...session,
    user: session?.user || null,
    company: session?.company || null,
    // Kept for callers that still read it. Nothing blocks on it any more:
    // the saved session answers "who is signed in?" synchronously.
    loading: false,
    isAuthenticated: signedIn,
    isAdmin: session?.audience === "staff" && session?.user?.role === "admin",
    isStaff: session?.audience === "staff",
    isCustomer: session?.audience === "customer",
    offline,
    idleIn,
    staySignedIn,
    setCompany,
    signIn,
    signOut,
    refreshProfile,
  }), [session, signedIn, offline, idleIn, staySignedIn, setCompany,
    signIn, signOut, refreshProfile]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
