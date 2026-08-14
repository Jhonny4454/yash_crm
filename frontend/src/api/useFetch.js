import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./client";

/**
 * GET a URL and track loading/error state.
 *
 * `loading` means "there is nothing to show yet", NOT "a request is in
 * flight". The difference is the whole reason this hook feels different from
 * the one it replaces: `loading` used to flip true on every refetch, so typing
 * a letter in a search box tore the entire table off the screen and put a
 * spinner in its place, then put a near-identical table back. Paging did the
 * same. Saving a row did the same.
 *
 * Now the previous rows stay put while the new ones are fetched, and callers
 * that want to show that something is happening read `refreshing`. Every
 * screen in the app gates on `loading`, so they all inherited this at once.
 *
 * `params` is stringified for the dependency check so callers can pass an
 * inline object without causing an infinite re-fetch loop.
 */
export function useFetch(url, params, { skip = false } = {}) {
  const [data, setData] = useState(null);
  const [meta, setMeta] = useState(null);
  const [extra, setExtra] = useState(null);
  const [loading, setLoading] = useState(!skip);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [tick, setTick] = useState(0);

  const key = JSON.stringify(params || {});
  const refetch = useCallback(() => setTick((t) => t + 1), []);

  // Read inside the effect without making the effect depend on them: `hasData`
  // decides between "blank screen" and "keep what is there", and re-running the
  // fetch every time the data changes would be a loop.
  const hasData = useRef(false);
  const lastUrl = useRef(url);

  useEffect(() => {
    if (skip || !url) {
      setLoading(false);
      setRefreshing(false);
      return undefined;
    }

    // A different endpoint entirely - keeping the old rows on screen would
    // show zones on the localities page for as long as the request takes.
    // Only a change of params or a manual refetch is a genuine refresh.
    const sameResource = lastUrl.current === url;
    lastUrl.current = url;
    if (!sameResource) hasData.current = false;

    let cancelled = false;
    if (hasData.current) setRefreshing(true);
    else setLoading(true);
    setError(null);

    get(url, JSON.parse(key))
      .then((res) => {
        if (cancelled) return;
        setData(res.data ?? res);
        setMeta(res.meta || null);
        const { data: _d, meta: _m, ...rest } = res;
        setExtra(rest);
        hasData.current = true;
      })
      .catch((err) => !cancelled && setError(err))
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
        setRefreshing(false);
      });

    return () => {
      cancelled = true;
    };
  }, [url, key, tick, skip]);

  return { data, meta, extra, loading, refreshing, error, refetch, setData };
}

/** Debounce a rapidly-changing value (search boxes). */
export function useDebounced(value, delay = 350) {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setOut(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return out;
}
