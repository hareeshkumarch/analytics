const EVIDENCE_ID_PATTERN = "ev_[0-9a-f]{12}";
const EVIDENCE_GROUP_STRIP_RE = new RegExp(`\\s*\\[${EVIDENCE_ID_PATTERN}(?:\\s*,\\s*${EVIDENCE_ID_PATTERN})*\\]`, "g");
const EVIDENCE_ID_EXACT_RE = new RegExp(`^${EVIDENCE_ID_PATTERN}$`);

const EVIDENCE_ID_ANY_RE = new RegExp(EVIDENCE_ID_PATTERN, "g");
const BOLD_FIGURE_RE = /\*\*([^*\n]{1,40}?\d[^*\n]{0,40}?)\*\*/;
const FIGURE_RE = /[-−]?[₹$€£]?\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|[KMB]\b|lakh|crore|million|billion))?/;

export function headlineFigure(markdown: string): string | null {
  const bold = BOLD_FIGURE_RE.exec(markdown)?.[1];
  return bold ? (FIGURE_RE.exec(bold)?.[0] ?? bold).trim() : null;
}

export function citationNumbers(markdown: string): Map<string, number> {
  const numbers = new Map<string, number>();
  for (const match of markdown.matchAll(EVIDENCE_ID_ANY_RE)) {
    if (!numbers.has(match[0])) numbers.set(match[0], numbers.size + 1);
  }
  return numbers;
}

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
