import { useEffect, useState } from "react";
import { get } from "./client";

/**
 * Options for a foreign-key dropdown, fetched once and shared.
 *
 * Several fields on one form (and several forms in a session) ask for the
 * same lists - vendors, categories, staff. Without sharing, opening the
 * vendor-bill dialog fires the vendor request again every time. So results
 * are cached per endpoint for the session, and concurrent callers await the
 * same in-flight promise instead of each firing their own request.
 */

const cache = new Map();     // endpoint -> rows
const inflight = new Map();  // endpoint -> Promise<rows>

/** Drop a cached list so the next form re-fetches it (after a create/edit). */
export function invalidateLookup(endpoint) {
  if (endpoint) {
    cache.delete(endpoint);
    inflight.delete(endpoint);
  } else {
    cache.clear();
    inflight.clear();
  }
}

export function fetchLookup(endpoint) {
  if (cache.has(endpoint)) return Promise.resolve(cache.get(endpoint));
  if (inflight.has(endpoint)) return inflight.get(endpoint);

  const promise = get(endpoint, { per_page: 500 })
    .then((payload) => {
      const rows = Array.isArray(payload?.data) ? payload.data
        : Array.isArray(payload) ? payload
          : payload?.data?.items || [];
      cache.set(endpoint, rows);
      return rows;
    })
    .finally(() => inflight.delete(endpoint));

  inflight.set(endpoint, promise);
  return promise;
}

/**
 * @param {string|null} endpoint  API path, or null to skip fetching
 * @param {{ valueKey?: string, labelKey?: string|Function }} [options]
 * @returns {{ options: Array<{value:any,label:string}>, byValue: Map, loading: boolean, error: any }}
 */
export function useLookup(endpoint, options = {}) {
  const { valueKey = "id", labelKey = "name" } = options;

  const [rows, setRows] = useState(() => cache.get(endpoint) || []);
  const [loading, setLoading] = useState(Boolean(endpoint) && !cache.has(endpoint));
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!endpoint) {
      setRows([]);
      setLoading(false);
      return undefined;
    }
    if (cache.has(endpoint)) {
      setRows(cache.get(endpoint));
      setLoading(false);
      return undefined;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchLookup(endpoint)
      .then((data) => { if (!cancelled) setRows(data); })
      .catch((err) => { if (!cancelled) setError(err); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [endpoint]);

  const label = typeof labelKey === "function"
    ? labelKey
    : (row) => row[labelKey] ?? row.name ?? row.title ?? `#${row[valueKey]}`;

  const opts = rows.map((row) => ({ value: row[valueKey], label: String(label(row)) }));
  const byValue = new Map(opts.map((o) => [String(o.value), o.label]));

  return { options: opts, byValue, loading, error, rows };
}

export default useLookup;
