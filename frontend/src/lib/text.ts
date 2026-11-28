const EVIDENCE_ID_PATTERN = "ev_[0-9a-f]{12}";
const EVIDENCE_GROUP_STRIP_RE = new RegExp(`\\s*\\[${EVIDENCE_ID_PATTERN}(?:\\s*,\\s*${EVIDENCE_ID_PATTERN})*\\]`, "g");
const EVIDENCE_ID_EXACT_RE = new RegExp(`^${EVIDENCE_ID_PATTERN}$`);

const EVIDENCE_ID_ANY_RE = new RegExp(EVIDENCE_ID_PATTERN, "g");
const BOLD_SPAN_RE = /\*\*([^*\n]{1,120}?)\*\*/g;
const DATE_RE = /\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b/g;
const FIGURE_SCAN_RE =
  /(?<![\w.])(?:[-−]?[₹$€£¥]\s?|[₹$€£¥]\s?[-−]|[-−])(\d[\d,]*(?:\.\d+)?)(?:\s?(%|[KMB]\b|k\b|bn\b|lakh|crore|million|billion))?|(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?:\s?(%|[KMB]\b|k\b|bn\b|lakh|crore|million|billion))?/g;
const ORDINAL_AFTER_RE = /^(?:st|nd|rd|th)\b/i;
const YEAR_BEFORE_RE =
  /\b(?:in|since|during|from|to|until|till|by|of|for|year|years|fy|cy|before|after|through|between|and|early|late|mid|calendar|fiscal)\s*$/i;
const MONTH_OR_QUARTER_BEFORE_RE =
  /\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|q[1-4]|h[12])[\s,.-]*$/i;
const MONTH_AFTER_RE =
  /^\s*(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b/i;
const YEAR_RANGE_AFTER_RE = /^\s*[-–/]\s*\d{2,4}\b/;
const YEAR_RANGE_BEFORE_RE = /\b(?:19|20)\d{2}\s*[-–/]\s*$/;
const STRUCTURAL_BEFORE_RE =
  /(?:\b(?:top|bottom|first|last|step|steps|stage|phase|rank|ranked|no\.|number|version|v|page|part|tier|level|quarter|week|day|days|month|months)\s*#?|#)\s*$/i;

function isExcludedPlainInteger(full: string, digits: string, before: string, after: string): boolean {
  if (full.trim() !== digits || digits.includes(",") || digits.includes(".")) return false;
  if (digits.length > 1 && digits.startsWith("0")) return true;
  if (STRUCTURAL_BEFORE_RE.test(before) || YEAR_RANGE_BEFORE_RE.test(before)) return true;
  const n = Number(digits);
  if (n >= 1900 && n <= 2100) {
    return (
      YEAR_BEFORE_RE.test(before) ||
      MONTH_OR_QUARTER_BEFORE_RE.test(before) ||
      YEAR_RANGE_AFTER_RE.test(after) ||
      MONTH_AFTER_RE.test(after)
    );
  }
  return false;
}

export function headlineFigure(markdown: string): string | null {
  const cleaned = stripEvidenceIds(markdown).replace(DATE_RE, " ");
  for (const boldMatch of cleaned.matchAll(BOLD_SPAN_RE)) {
    const bold = boldMatch[1];
    for (const match of bold.matchAll(FIGURE_SCAN_RE)) {
      const full = match[0].trim();
      const digits = match[1] ?? match[3] ?? "";
      const start = match.index ?? 0;
      const before = bold.slice(0, start);
      const after = bold.slice(start + match[0].length);
      if (ORDINAL_AFTER_RE.test(after)) continue;
      if (isExcludedPlainInteger(full, digits, before, after)) continue;
      return full;
    }
  }
  return null;
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

/**
 * The first sentence of an answer as plain text, for previews. Heading lines are skipped so a
 * "## Summary" heading doesn't run into the sentence after it; they're used only when nothing else is left.
 */
export function excerptMarkdown(markdown: string | undefined, max = 240): string {
  if (!markdown) return "";
  const lines = stripEvidenceIds(markdown).split(/\r?\n/);
  const isHeading = (line: string) => /^\s*#{1,6}\s/.test(line);
  const body = lines.filter((line) => !isHeading(line));
  const source = body.some((line) => line.trim()) ? body : lines;
  const text = source
    .join("\n")
    .replace(/^\s*#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/[*_`>|]/g, "")
    .replace(/-{3,}/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .split(/(?<=[.!?])\s/)[0];
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text;
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
