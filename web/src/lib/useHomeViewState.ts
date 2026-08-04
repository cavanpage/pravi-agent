import { useEffect, useReducer } from "react";

import type { TicketKind } from "./api";

export type SortKey = "updated_desc" | "updated_asc" | "title" | "status";
/** Kind filter for the whole page — "all" (default) means no filtering,
 * so nothing is ever silently hidden. */
export type KindFilter = TicketKind | "all";

export type HomeViewState = {
  kindFilter: KindFilter;
  sortBy: SortKey;
  search: string;
};

type Action =
  | { type: "SET_KIND"; kind: KindFilter }
  | { type: "SET_SORT"; sort: SortKey }
  | { type: "SET_SEARCH"; search: string };

const DEFAULTS: HomeViewState = {
  kindFilter: "all",
  sortBy: "updated_desc",
  search: "",
};

// Versioned key — bumped to v2 when `inFlightKind` became `kindFilter`
// (with "all"): stale v1 entries reload as defaults instead of malformed
// state.
const STORAGE_KEY = "pravi.homeView.v2";

const VALID_KINDS: ReadonlySet<KindFilter> = new Set([
  "all",
  "epic",
  "feature",
  "task",
]);
const VALID_SORTS: ReadonlySet<SortKey> = new Set([
  "updated_desc",
  "updated_asc",
  "title",
  "status",
]);

function reducer(state: HomeViewState, action: Action): HomeViewState {
  switch (action.type) {
    case "SET_KIND":
      return { ...state, kindFilter: action.kind };
    case "SET_SORT":
      return { ...state, sortBy: action.sort };
    case "SET_SEARCH":
      return { ...state, search: action.search };
  }
}

function init(): HomeViewState {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<HomeViewState>;
    return {
      kindFilter: VALID_KINDS.has(parsed.kindFilter as KindFilter)
        ? (parsed.kindFilter as KindFilter)
        : DEFAULTS.kindFilter,
      sortBy: VALID_SORTS.has(parsed.sortBy as SortKey)
        ? (parsed.sortBy as SortKey)
        : DEFAULTS.sortBy,
      search: DEFAULTS.search,
    };
  } catch {
    return DEFAULTS;
  }
}

export function useHomeViewState() {
  const [state, dispatch] = useReducer(reducer, undefined, init);

  // Search is intentionally excluded from persistence — restoring a stale
  // query on next visit would silently hide tickets and confuse users.
  useEffect(() => {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          kindFilter: state.kindFilter,
          sortBy: state.sortBy,
        }),
      );
    } catch {
      // localStorage full / disabled — non-fatal
    }
  }, [state.kindFilter, state.sortBy]);

  return {
    state,
    setKind: (kind: KindFilter) => dispatch({ type: "SET_KIND", kind }),
    setSort: (sort: SortKey) => dispatch({ type: "SET_SORT", sort }),
    setSearch: (search: string) => dispatch({ type: "SET_SEARCH", search }),
  };
}
