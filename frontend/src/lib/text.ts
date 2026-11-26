const EVIDENCE_ID_PATTERN = "ev_[0-9a-f]{12}";
const EVIDENCE_GROUP_STRIP_RE = new RegExp(`\\s*\\[${EVIDENCE_ID_PATTERN}(?:\\s*,\\s*${EVIDENCE_ID_PATTERN})*\\]`, "g");
const EVIDENCE_ID_EXACT_RE = new RegExp(`^${EVIDENCE_ID_PATTERN}$`);

export function stripEvidenceIds(markdown: string): string {
  return markdown.replace(EVIDENCE_GROUP_STRIP_RE, "");
}

/** Parse a `[ev_..., ev_...]` citation group starting at `start`, returning the IDs and end index. */
export function parseEvidenceCitation(text: string, start: number): { ids: string[]; end: number } | null {
  if (text[start] !== "[" || !text.startsWith("ev_", start + 1)) return null;
  const close = text.indexOf("]", start + 1);
  if (close === -1 || close === start + 1) return null;
  const parts = text.slice(start + 1, close).split(",");
  const ids: string[] = [];
  for (const part of parts) {
    const trimmed = part.trim();
    if (!EVIDENCE_ID_EXACT_RE.test(trimmed)) return null;
    ids.push(trimmed);
  }
  return { ids, end: close + 1 };
}
