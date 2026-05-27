import { useEffect, useReducer } from "react";

export type IssueState = "open" | "closed" | "all";

export interface IssuesViewState {
  /** "<owner>/<name>" or "" if nothing picked yet. */
  repo: string;
  state: IssueState;
  /** Comma-separated label filter, server-side. */
  labels: string;
  /** Client-side title/body search. */
  search: string;
}

type Action =
  | { type: "SET_REPO"; repo: string }
  | { type: "SET_STATE"; state: IssueState }
  | { type: "SET_LABELS"; labels: string }
  | { type: "SET_SEARCH"; search: string };

const DEFAULTS: IssuesViewState = {
  repo: "",
  state: "open",
  labels: "",
  search: "",
};

const STORAGE_KEY = "pravi.issuesView.v1";

const VALID_STATES: ReadonlySet<IssueState> = new Set(["open", "closed", "all"]);

function reducer(s: IssuesViewState, a: Action): IssuesViewState {
  switch (a.type) {
    case "SET_REPO":
      return { ...s, repo: a.repo };
    case "SET_STATE":
      return { ...s, state: a.state };
    case "SET_LABELS":
      return { ...s, labels: a.labels };
    case "SET_SEARCH":
      return { ...s, search: a.search };
  }
}

function init(): IssuesViewState {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<IssuesViewState>;
    return {
      repo: typeof parsed.repo === "string" ? parsed.repo : DEFAULTS.repo,
      state: VALID_STATES.has(parsed.state as IssueState)
        ? (parsed.state as IssueState)
        : DEFAULTS.state,
      labels: typeof parsed.labels === "string" ? parsed.labels : DEFAULTS.labels,
      // Search is intentionally ephemeral — restoring a stale query hides
      // issues silently on next visit.
      search: DEFAULTS.search,
    };
  } catch {
    return DEFAULTS;
  }
}

export function useIssuesViewState() {
  const [state, dispatch] = useReducer(reducer, undefined, init);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          repo: state.repo,
          state: state.state,
          labels: state.labels,
        }),
      );
    } catch {
      // ignore
    }
  }, [state.repo, state.state, state.labels]);

  return {
    state,
    setRepo: (r: string) => dispatch({ type: "SET_REPO", repo: r }),
    setState: (s: IssueState) => dispatch({ type: "SET_STATE", state: s }),
    setLabels: (l: string) => dispatch({ type: "SET_LABELS", labels: l }),
    setSearch: (q: string) => dispatch({ type: "SET_SEARCH", search: q }),
  };
}
