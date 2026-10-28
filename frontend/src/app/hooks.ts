import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, type Page, type Thought } from "../api/client";
import { useThoughtEvents } from "./bus";

export function useMedia(query: string): boolean {
  const [on, setOn] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const fn = () => setOn(m.matches);
    m.addEventListener("change", fn);
    return () => m.removeEventListener("change", fn);
  }, [query]);
  return on;
}

export function useOnline(): boolean {
  const [on, setOn] = useState(navigator.onLine);
  useEffect(() => {
    const up = () => setOn(true), down = () => setOn(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return on;
}

/** Load once per key; `data` is null while loading. */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data: T | null; error: string; loading: boolean }>({ data: null, error: "", loading: true });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    const ac = new AbortController();
    setState((s) => ({ ...s, loading: true, error: "" }));
    load(ac.signal).then(
      (data) => !ac.signal.aborted && setState({ data, error: "", loading: false }),
      (e) => !ac.signal.aborted && setState({ data: null, error: errorMessage(e), loading: false }),
    );
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);
  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, reload, setData: (data: T) => setState((s) => ({ ...s, data })) };
}

interface PagedOptions {
  /** Whether a thought created elsewhere belongs at the top of this list. */
  acceptsNew?: (t: Thought) => boolean;
  /** Keep only items matching this (client-side refinement of a server filter). */
  keep?: (t: Thought) => boolean;
}

/** Cursor pagination over a thought list that stays in sync with local mutations. */
export function usePaged(
  fetchPage: (cursor: string | null, signal: AbortSignal) => Promise<Page<Thought>>,
  deps: unknown[],
  opts: PagedOptions = {},
) {
  const [items, setItems] = useState<Thought[] | null>(null);
  const [pinned, setPinned] = useState<Thought[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);
  const fetchRef = useRef(fetchPage);
  fetchRef.current = fetchPage;
  const optsRef = useRef(opts);
  optsRef.current = opts;

  useEffect(() => {
    const ac = new AbortController();
    setItems(null);
    setError("");
    fetchRef.current(null, ac.signal).then(
      (p) => {
        if (ac.signal.aborted) return;
        const keep = optsRef.current.keep;
        setItems(keep ? p.items.filter(keep) : p.items);
        setPinned(p.pinned ?? []);
        setCursor(p.next_cursor ?? null);
      },
      (e) => {
        if (ac.signal.aborted) return;
        setError(errorMessage(e));
        setItems([]);
      },
    );
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const more = useCallback(async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const p = await fetchRef.current(cursor, new AbortController().signal);
      const keep = optsRef.current.keep;
      setItems((prev) => {
        const seen = new Set((prev ?? []).map((t) => t.id));
        return [...(prev ?? []), ...(keep ? p.items.filter(keep) : p.items).filter((t) => !seen.has(t.id))];
      });
      setCursor(p.next_cursor ?? null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, loadingMore]);

  useThoughtEvents((e) => {
    const replace = (list: Thought[]) =>
      e.type === "deleted"
        ? list.filter((t) => t.id !== e.id)
        : list.map((t) => (t.id === e.thought.id ? e.thought : t));
    setPinned((p) => replace(p));
    setItems((prev) => {
      if (!prev) return prev;
      if ((e.type === "created" || e.type === "restored") && optsRef.current.acceptsNew?.(e.thought)) {
        return [e.thought, ...prev.filter((t) => t.id !== e.thought.id)].sort((a, b) => b.created_at.localeCompare(a.created_at));
      }
      const next = replace(prev);
      const keep = optsRef.current.keep;
      return keep ? next.filter(keep) : next;
    });
  });

  return {
    items, pinned, error, hasMore: !!cursor, loadingMore, more,
    reload: useCallback(() => setNonce((n) => n + 1), []),
  };
}
