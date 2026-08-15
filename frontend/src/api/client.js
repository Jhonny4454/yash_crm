import axios from "axios";

// Relative by default so Vite's development proxy and the Flask deployment
// both use the same origin. Set VITE_API_URL only when the API is separate.
const BASE_URL = (import.meta.env.VITE_API_URL || "/api/v1").replace(/\/$/, "");

export const TOKEN_KEY = "unicrm.access";
export const REFRESH_KEY = "unicrm.refresh";
export const AUTH_KEY = "unicrm.auth";

export const tokens = {
  get access() {
    return localStorage.getItem(TOKEN_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  set({ access_token, refresh_token }) {
    if (access_token) localStorage.setItem(TOKEN_KEY, access_token);
    if (refresh_token) localStorage.setItem(REFRESH_KEY, refresh_token);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(AUTH_KEY);
  },
};

const api = axios.create({
  baseURL: BASE_URL,
  timeout: 25_000,
  headers: { Accept: "application/json" },
});

api.interceptors.request.use((config) => {
  const accessToken = tokens.access;
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
  return config;
});

let refreshPromise = null;

function signedOut() {
  tokens.clear();
  window.dispatchEvent(new Event("unicrm:signed-out"));
}

/**
 * Calls that must never trigger a token refresh.
 *
 * This used to exclude the whole of `/auth/*`, which quietly included
 * `/auth/staff/me` - the very first call the app makes on load. So once the
 * access token expired, that call 401'd, no refresh was attempted, and the
 * user was signed out despite holding a valid refresh token. A 12-hour access
 * token made it rare; it is fatal the moment that token is short-lived.
 *
 * Login and refresh genuinely must not recurse, and logout has nothing to
 * refresh for.
 */
const NEVER_REFRESH = ["/auth/refresh", "/auth/logout", "/auth/staff/login",
  "/auth/customer/login"];

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const { response, config } = error;
    const url = config?.url || "";
    const isAuthCall = NEVER_REFRESH.some((path) => url.startsWith(path));
    if (!response || response.status !== 401 || config?.__retried || isAuthCall) {
      return Promise.reject(error);
    }

    const refreshToken = tokens.refresh;
    if (!refreshToken) {
      signedOut();
      return Promise.reject(error);
    }

    try {
      refreshPromise ??= axios.post(`${BASE_URL}/auth/refresh`, {
        refresh_token: refreshToken,
      });
      const refreshResponse = await refreshPromise;
      const payload = refreshResponse.data?.data || refreshResponse.data;
      refreshPromise = null;
      if (!payload?.access_token) throw new Error("refresh_failed");
      tokens.set(payload);
      config.__retried = true;
      config.headers.Authorization = `Bearer ${payload.access_token}`;
      return api(config);
    } catch (refreshError) {
      refreshPromise = null;

      // A refresh that fails because the SERVER IS DOWN says nothing about
      // whether the session is still valid - but this used to throw the
      // tokens away regardless. So a backend restart mid-session logged the
      // operator out and, because the tokens were gone, every following call
      // came back 401 until they signed in again. Only a refusal from a
      // server that actually answered means the session is finished.
      if (backendIsDown(refreshError)) {
        return Promise.reject(refreshError);
      }
      signedOut();
      return Promise.reject(refreshError);
    }
  },
);

/**
 * Statuses that mean "nothing is listening", not "the request was bad".
 *
 * In development these come from Vite's proxy when Flask is not running; in
 * production from nginx when the app is down. Either way the fix is the same
 * and it is not something the user can do anything about by retrying.
 */
const UPSTREAM_DOWN = new Set([502, 503, 504]);

/**
 * When the backend is unreachable, every panel on the page tries, retries and
 * fails in turn - one page load became a dozen requests and a dozen identical
 * console errors. After the first such failure we stop retrying for a few
 * seconds so the app fails fast and says one clear thing instead.
 */
let unreachableUntil = 0;

/** Did the APP itself answer? A JSON body carrying `error`/`detail` means a
 *  route ran and refused, whereas nginx/proxy 5xx bodies are HTML and mean
 *  nobody is listening. The two must be told apart: a route that answers 503
 *  on purpose (e.g. the payment gateway not being configured) is a business
 *  condition with its own message, not an outage. */
function hasAppPayload(response) {
  const data = response?.data;
  return Boolean(data && typeof data === "object"
    && (data.error || data.detail));
}

function backendIsDown(error) {
  if (!error.response) return true;
  if (!UPSTREAM_DOWN.has(error.response.status)) return false;
  return !hasAppPayload(error.response);
}

function errorFrom(error) {
  const status = error.response?.status || 0;
  const down = backendIsDown(error);

  const message = down
    ? "Cannot reach the server."
    : error.response?.data?.error || error.message || "network_error";

  const wrapped = new Error(message);
  wrapped.status = status;
  wrapped.unreachable = down;
  wrapped.detail = error.response?.data?.detail || (down
    ? "The API is not responding. If you are running this locally, check that "
      + "Flask is started (python app.py) and listening on port 5000."
    : undefined);

  // Endpoints that validate several fields at once answer with a list, not a
  // sentence. Flattening it into `detail` and dropping the array meant a form
  // could only ever show "one or more values are invalid" - the caller had no
  // way to put each message next to the field it belongs to.
  const payload = error.response?.data;
  if (Array.isArray(payload?.errors)) wrapped.errors = payload.errors;
  if (Array.isArray(payload?.fields)) wrapped.fields = payload.fields;
  wrapped.data = payload;

  logFailure(error, status, message, wrapped.detail);
  return wrapped;
}

/**
 * Put the REASON in the console, not just the status code.
 *
 * The browser's own line for a failed request is
 * `Failed to load resource: the server responded with a status of 424`, which
 * says what happened and nothing about why. The why is in the response body,
 * and reading it means opening devtools, finding the request and clicking
 * through to the Response tab - so in practice nobody sees it and a support
 * conversation turns into several rounds of "what did the body say?".
 *
 * One line, with the endpoint and the server's own explanation.
 */
function logFailure(error, status, message, detail) {
  const method = (error.config?.method || "get").toUpperCase();
  const url = error.config?.url || "(unknown)";
  const label = `[API] ${method} ${url} -> ${status || "no response"} ${message}`;
  if (detail) console.error(label + "\n       " + detail);
  else console.error(label);
}

/**
 * How many API calls are in flight, published to anyone who wants to show it.
 *
 * One thin bar at the top of the window replaces a dozen panels each throwing
 * up their own spinner: the page stops flashing, and the operator still knows
 * something is happening. Counted here rather than in a hook because it has to
 * cover every call the app makes, including ones no component is watching.
 */
let inFlight = 0;
const inFlightListeners = new Set();

export function onInFlightChange(listener) {
  inFlightListeners.add(listener);
  listener(inFlight);
  return () => inFlightListeners.delete(listener);
}

function trackInFlight(delta) {
  // Clamped: a double-decrement would wedge the counter negative and the bar
  // would never show again for the rest of the session.
  inFlight = Math.max(0, inFlight + delta);
  inFlightListeners.forEach((listener) => listener(inFlight));
}

async function request(method, url, { data, params, retry = false, headers } = {}) {
  trackInFlight(1);
  try {
    return await requestInner(method, url, { data, params, retry, headers });
  } finally {
    trackInFlight(-1);
  }
}

async function requestInner(method, url, { data, params, retry = false, headers } = {}) {
  // One retry, not two. A genuine blip is worth a second try; three attempts
  // per call only multiplies the noise when something is actually wrong.
  let attempts = retry && Date.now() > unreachableUntil ? 1 : 0;

  // Deliberate retry loop: every path either returns or throws, and
  // `attempts` strictly decreases, so this cannot spin.
  // eslint-disable-next-line no-constant-condition
  while (true) {
    try {
      const response = await api.request({ method, url, data, params, headers });
      const payload = response.data;
      if (payload?.ok === false) throw Object.assign(new Error(payload.error), {
        response: { status: response.status, data: payload },
      });
      unreachableUntil = 0;   // something answered, so open the gate again
      return payload;
    } catch (error) {
      if (backendIsDown(error)) {
        // Hold every other caller off for a moment rather than letting each
        // panel on the page discover the outage for itself.
        unreachableUntil = Date.now() + 5000;
        throw errorFrom(error);
      }
      if (attempts > 0 && error.response && error.response.status >= 500) {
        attempts -= 1;
        await new Promise((resolve) => setTimeout(resolve, 350));
        continue;
      }
      throw errorFrom(error);
    }
  }
}

export const get = (url, params, options = {}) =>
  request("get", url, { params, retry: options.retry !== false });
export const post = (url, data, options = {}) => request("post", url, { data, ...options });
export const put = (url, data, options = {}) => request("put", url, { data, ...options });
export const del = (url, data, options = {}) => request("delete", url, { data, ...options });
export const upload = (url, formData, options = {}) =>
  request("post", url, {
    data: formData,
    headers: { "Content-Type": "multipart/form-data" },
    ...options,
  });

export default api;
