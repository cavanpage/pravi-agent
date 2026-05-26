import { useEffect, useReducer } from "react";

import type { TicketKind } from "./api";

export type SortKey = "updated_desc" | "updated_asc" | "title" | "status";

export type HomeViewState = {
  inFlightKind: TicketKind;
  sortBy: SortKey;
  search: string;
};

type Action =
  | { type: "SET_KIND"; kind: TicketKind }
  | { type: "SET_SORT"; sort: SortKey }
  | { type: "SET_SEARCH"; search: string };

const DEFAULTS: HomeViewState = {
  inFlightKind: "epic",
  sortBy: "updated_desc",
  search: "",
};

// Versioned key — bump the suffix if HomeViewState shape changes so stale
// entries don't reload as malformed state.
const STORAGE_KEY = "pravi.homeView.v1";

const VALID_KINDS: ReadonlySet<TicketKind> = new Set(["epic", "feature", "task"]);
const VALID_SORTS: ReadonlySet<SortKey> = new Set([
  "updated_desc",
  "updated_asc",
  "title",
  "status",
]);

function reducer(state: HomeViewState, action: Action): HomeViewState {
  switch (action.type) {
    case "SET_KIND":
      return { ...state, inFlightKind: action.kind };
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
      inFlightKind: VALID_KINDS.has(parsed.inFlightKind as TicketKind)
        ? (parsed.inFlightKind as TicketKind)
        : DEFAULTS.inFlightKind,
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
          inFlightKind: state.inFlightKind,
          sortBy: state.sortBy,
        }),
      );
    } catch {
      // localStorage full / disabled — non-fatal
    }
  }, [state.inFlightKind, state.sortBy]);

  return {
    state,
    setKind: (kind: TicketKind) => dispatch({ type: "SET_KIND", kind }),
    setSort: (sort: SortKey) => dispatch({ type: "SET_SORT", sort }),
    setSearch: (search: string) => dispatch({ type: "SET_SEARCH", search }),
  };
}
