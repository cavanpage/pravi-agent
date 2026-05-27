// Helpers for the inline activity-feed markers the backend embeds in
// streamed `raw_md` ("<!--pravi-progress: Read|src/foo.py-->"). Shared
// between DecomposePanel and the plan-draft view on TicketPlanPage.

export interface ProgressEvent {
  tool: string;
  summary: string;
}

const PROGRESS_RE = /<!--pravi-progress:\s*([^|]+)\|([^>]*?)\s*-->/g;

export function extractProgress(rawMd: string | null | undefined): ProgressEvent[] {
  if (!rawMd) return [];
  const out: ProgressEvent[] = [];
  PROGRESS_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = PROGRESS_RE.exec(rawMd))) {
    out.push({ tool: m[1].trim(), summary: m[2].trim() });
  }
  return out;
}

/** Remove the comment markers before rendering raw_md as markdown. The
 * comments are invisible in the rendered output anyway, but stripping
 * them avoids stray blank lines while the agent is mid-stream. */
export function stripProgressMarkers(rawMd: string): string {
  return rawMd.replace(PROGRESS_RE, "").replace(/\n{3,}/g, "\n\n");
}

export const TOOL_LABEL: Record<string, string> = {
  Read: "reading",
  Grep: "searching",
  Glob: "matching",
  WebFetch: "fetching",
};
