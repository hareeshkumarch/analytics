export function stripEvidenceIds(markdown: string): string {
  return markdown.replace(/\s*\[ev_[0-9a-f]{12}(?:\s*,\s*ev_[0-9a-f]{12})*\]/g, "");
}
