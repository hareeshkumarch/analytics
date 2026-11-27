"use client";

import React, { createContext, memo, useContext, useMemo } from "react";
import { HScroll } from "@/components/HScroll";
import { citationNumbers, parseEvidenceCitation } from "@/lib/text";

const SAFE_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

function safeHref(href: string): string | null {
  try {
    const url = new URL(href);
    return SAFE_PROTOCOLS.has(url.protocol) ? url.toString() : null;
  } catch {
    return null;
  }
}

export const EvidenceContext = createContext<((evidenceId: string) => void) | null>(null);
const CitationNumbers = createContext<Map<string, number>>(new Map());

function EvidenceChip({ id }: { id: string }) {
  const open = useContext(EvidenceContext);
  const number = useContext(CitationNumbers).get(id) ?? 0;
  const label = number ? String(number) : "•";
  if (!open) return <sup className="ml-0.5 text-[0.72em] font-medium text-secondary">{label}</sup>;
  return (
    <sup className="ml-0.5">
      <button
        type="button"
        onClick={() => open(id)}
        aria-label={`Source ${label}: show the query and rows behind this figure`}
        title="Show the query and rows behind this figure"
        className="inline-flex min-w-[1.4em] items-center justify-center bg-plum-soft px-1 text-[0.72em] font-semibold leading-[1.5] text-plum transition-colors hover:bg-plum hover:text-on-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-plum"
      >
        {label}
      </button>
    </sup>
  );
}

const MAX_INLINE_DEPTH = 6;

const isSpace = (ch: string | undefined): boolean => ch === " " || ch === "\t" || ch === "\n" || ch === "\r";
const isWordChar = (ch: string | undefined): boolean => ch !== undefined && /\w/.test(ch);

function findClosingDelimiter(
  text: string,
  from: number,
  marker: string,
  requireBoundary: boolean,
): { index: number; lineExhausted: boolean } {
  const len = marker.length;
  const lineEnd = text.indexOf("\n", from);
  const limit = lineEnd === -1 ? text.length : lineEnd;
  for (let idx = text.indexOf(marker, from); idx !== -1 && idx <= limit - len; idx = text.indexOf(marker, idx + 1)) {
    if (idx === from) continue;
    if (isSpace(text[idx - 1])) continue;
    if (len === 1 && (text[idx - 1] === marker || text[idx + 1] === marker)) continue;
    if (requireBoundary && isWordChar(text[idx + len])) continue;
    return { index: idx, lineExhausted: false };
  }
  return { index: -1, lineExhausted: lineEnd === -1 };
}

function stripTrailingHeadingHashes(text: string): string {
  let end = text.length;
  while (end > 0 && text[end - 1] === "#") end--;
  if (end === text.length || end === 0 || !isSpace(text[end - 1])) return text;
  while (end > 0 && isSpace(text[end - 1])) end--;
  return text.slice(0, end);
}

function renderInline(text: string, keyPrefix: string, depth = 0): React.ReactNode[] {
  if (!text || depth >= MAX_INLINE_DEPTH) return text ? [text] : [];
  const out: React.ReactNode[] = [];
  let plainStart = 0;
  let pos = 0;
  let seq = 0;
  let nextCloseBracket = -2;
  const noMore = { code: false, dstar: false, star: false, dunder: false, under: false };

  const flushPlain = (end: number) => {
    if (end > plainStart) out.push(text.slice(plainStart, end));
  };

  while (pos < text.length) {
    const ch = text[pos];

    if (ch === "`" && !noMore.code) {
      const close = text.indexOf("`", pos + 1);
      if (close === -1 || text.slice(pos + 1, close).includes("\n")) {
        if (close === -1) noMore.code = true;
        pos += 1;
        continue;
      }
      if (close > pos + 1) {
        flushPlain(pos);
        out.push(
          <code key={`${keyPrefix}-${seq++}`} className="bg-panel px-1.5 py-0.5 font-mono text-[0.86em] text-ink">
            {text.slice(pos + 1, close)}
          </code>
        );
        pos = close + 1;
        plainStart = pos;
        continue;
      }
      pos += 1;
      continue;
    }

    if (ch === "[") {
      const citation = parseEvidenceCitation(text, pos);
      if (citation) {
        flushPlain(pos);
        const key = `${keyPrefix}-${seq++}`;
        citation.ids.forEach((id, n) => out.push(<EvidenceChip key={`${key}-${n}`} id={id} />));
        pos = citation.end;
        plainStart = pos;
        continue;
      }
      if (nextCloseBracket !== -1 && nextCloseBracket <= pos) {
        nextCloseBracket = text.indexOf("]", pos + 1);
      }
      if (
        nextCloseBracket > pos + 1 &&
        text[nextCloseBracket + 1] === "(" &&
        !text.slice(pos + 1, nextCloseBracket).includes("\n")
      ) {
        let k = nextCloseBracket + 2;
        while (k < text.length && text[k] !== ")" && !isSpace(text[k])) k++;
        if (k < text.length && text[k] === ")" && k > nextCloseBracket + 2) {
          flushPlain(pos);
          const label = text.slice(pos + 1, nextCloseBracket);
          const rawHref = text.slice(nextCloseBracket + 2, k);
          const href = safeHref(rawHref);
          const key = `${keyPrefix}-${seq++}`;
          out.push(
            href ? (
              <a
                key={key}
                href={href}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="font-medium text-plum underline decoration-plum/30 underline-offset-2 hover:decoration-plum"
              >
                {label}
                <span className="ml-1 text-[0.8em] font-normal text-secondary">({new URL(href).host || "email"})</span>
              </a>
            ) : (
              text.slice(pos, k + 1)
            )
          );
          pos = k + 1;
          plainStart = pos;
          continue;
        }
      }
      pos += 1;
      continue;
    }

    if (ch === "*" && text[pos + 1] === "*") {
      if (!noMore.dstar && !isSpace(text[pos + 2])) {
        const close = findClosingDelimiter(text, pos + 2, "**", false);
        if (close.index !== -1) {
          flushPlain(pos);
          const key = `${keyPrefix}-${seq++}`;
          out.push(
            <strong key={key} className="font-semibold text-ink">
              {renderInline(text.slice(pos + 2, close.index), key, depth + 1)}
            </strong>
          );
          pos = close.index + 2;
          plainStart = pos;
          continue;
        }
        if (close.lineExhausted) noMore.dstar = true;
      }
      pos += 2;
      continue;
    }

    if (ch === "*") {
      if (!noMore.star && !isSpace(text[pos + 1])) {
        const close = findClosingDelimiter(text, pos + 1, "*", false);
        if (close.index !== -1) {
          flushPlain(pos);
          const key = `${keyPrefix}-${seq++}`;
          out.push(
            <em key={key} className="italic">
              {renderInline(text.slice(pos + 1, close.index), key, depth + 1)}
            </em>
          );
          pos = close.index + 1;
          plainStart = pos;
          continue;
        }
        if (close.lineExhausted) noMore.star = true;
      }
      pos += 1;
      continue;
    }

    if (ch === "_" && text[pos + 1] === "_") {
      if (!noMore.dunder && !isWordChar(text[pos - 1]) && !isSpace(text[pos + 2])) {
        const close = findClosingDelimiter(text, pos + 2, "__", true);
        if (close.index !== -1) {
          flushPlain(pos);
          const key = `${keyPrefix}-${seq++}`;
          out.push(
            <strong key={key} className="font-semibold text-ink">
              {renderInline(text.slice(pos + 2, close.index), key, depth + 1)}
            </strong>
          );
          pos = close.index + 2;
          plainStart = pos;
          continue;
        }
        if (close.lineExhausted) noMore.dunder = true;
      }
      pos += 2;
      continue;
    }

    if (ch === "_") {
      if (!noMore.under && !isWordChar(text[pos - 1]) && !isSpace(text[pos + 1])) {
        const close = findClosingDelimiter(text, pos + 1, "_", true);
        if (close.index !== -1) {
          flushPlain(pos);
          const key = `${keyPrefix}-${seq++}`;
          out.push(
            <em key={key} className="italic">
              {renderInline(text.slice(pos + 1, close.index), key, depth + 1)}
            </em>
          );
          pos = close.index + 1;
          plainStart = pos;
          continue;
        }
        if (close.lineExhausted) noMore.under = true;
      }
      pos += 1;
      continue;
    }

    pos += 1;
  }

  flushPlain(text.length);
  return out;
}

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "para"; text: string }
  | { kind: "list"; ordered: boolean; items: { text: string; children: string[] }[] }
  | { kind: "quote"; text: string }
  | { kind: "code"; lang: string; text: string }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "rule" };

const splitRow = (line: string) =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());

function parse(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i++;
      continue;
    }

    const fence = /^(```|~~~)\s*([\w+-]*)/.exec(trimmed);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      blocks.push({ kind: "code", lang: fence[2], text: body.join("\n") });
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: stripTrailingHeadingHashes(heading[2]) });
      i++;
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push({ kind: "rule" });
      i++;
      continue;
    }

    if (trimmed.startsWith("|") && i + 1 < lines.length && /^\|?\s*:?-{2,}/.test(lines[i + 1].trim())) {
      const head = splitRow(trimmed);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(splitRow(lines[i++]));
      blocks.push({ kind: "table", head, rows });
      continue;
    }

    if (trimmed.startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) body.push(lines[i++].trim().replace(/^>\s?/, ""));
      blocks.push({ kind: "quote", text: body.join(" ") });
      continue;
    }

    const listItem = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
    const first = listItem.exec(line);
    if (first) {
      const ordered = /\d/.test(first[2]);
      const baseIndent = first[1].length;
      const items: { text: string; children: string[] }[] = [];
      while (i < lines.length) {
        const m = listItem.exec(lines[i]);
        if (!m) {
          if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) {
            items[items.length - 1].text += ` ${lines[i].trim()}`;
            i++;
            continue;
          }
          break;
        }
        if (m[1].length > baseIndent && items.length) items[items.length - 1].children.push(m[3]);
        else if (/\d/.test(m[2]) === ordered) items.push({ text: m[3], children: [] });
        else break;
        i++;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }

    const para: string[] = [trimmed];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,4}\s|```|~~~|>|\||(-{3,}|\*{3,})$)/.test(lines[i].trim()) &&
      !listItem.test(lines[i])
    ) {
      para.push(lines[i++].trim());
    }
    blocks.push({ kind: "para", text: para.join(" ") });
  }
  return blocks;
}

const HEADING_CLS: Record<number, string> = {
  1: "font-display text-[22px] font-semibold tracking-[-0.015em]",
  2: "font-display text-[19px] font-semibold tracking-[-0.01em]",
  3: "font-display text-[17px] font-semibold",
  4: "text-[15px] font-semibold",
};

export const SafeMarkdown = memo(function SafeMarkdown({ content }: { content: string }) {
  const blocks = useMemo(() => parse(content), [content]);
  const numbers = useMemo(() => citationNumbers(content), [content]);
  return (
    <CitationNumbers.Provider value={numbers}>
      <div className="space-y-3.5 break-words text-[14.5px] leading-[1.7] text-body">
        {blocks.map((b, idx) => {
          const key = `b${idx}`;
          switch (b.kind) {
            case "heading": {
              const Tag = (["h2", "h3", "h4", "h5"] as const)[Math.min(Math.max(b.level, 1), 4) - 1];
              return (
                <Tag key={key} className={`pt-1.5 text-ink first:pt-0 ${HEADING_CLS[b.level]}`}>
                  {renderInline(b.text, key)}
                </Tag>
              );
            }
            case "para":
              return <p key={key}>{renderInline(b.text, key)}</p>;
            case "rule":
              return <hr key={key} className="border-border" />;
            case "quote":
              return (
                <blockquote key={key} className="border-l-2 border-plum/40 bg-panel/60 py-2 pl-4 pr-3 text-body">
                  {renderInline(b.text, key)}
                </blockquote>
              );
            case "code":
              return (
                <div key={key} className="overflow-hidden border border-border bg-panel">
                  {b.lang && (
                    <div className="border-b border-border px-3.5 py-1.5 font-mono text-[11px] uppercase tracking-wide text-secondary">
                      {b.lang}
                    </div>
                  )}
                  <HScroll label={b.lang ? `${b.lang} code block` : "Code block"}>
                    <pre className="px-3.5 py-3 font-mono text-[12.5px] leading-relaxed text-ink">
                      <code>{b.text}</code>
                    </pre>
                  </HScroll>
                </div>
              );
            case "table":
              return (
                <HScroll
                  key={key}
                  className="border border-border"
                  label={`Table: ${b.head.slice(0, 3).join(", ")}`}
                >
                  <table className="w-full border-collapse text-left text-[13px]">
                    <thead>
                      <tr className="bg-panel">
                        {b.head.map((h, c) => (
                          <th key={c} className="whitespace-nowrap px-3.5 py-2.5 text-[12px] font-semibold text-ink">
                            {renderInline(h, `${key}h${c}`)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {b.rows.map((row, r) => (
                        <tr key={r} className="border-t border-border">
                          {b.head.map((_, c) => (
                            <td key={c} className="px-3.5 py-2.5 align-top text-body">
                              {renderInline(row[c] ?? "", `${key}r${r}c${c}`)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </HScroll>
              );
            case "list": {
              const ListTag = b.ordered ? "ol" : "ul";
              return (
                <ListTag
                  key={key}
                  className={`space-y-1.5 pl-5 ${b.ordered ? "list-decimal marker:text-secondary" : "list-disc marker:text-plum-muted"}`}
                >
                  {b.items.map((it, n) => (
                    <li key={n} className="pl-1">
                      {renderInline(it.text, `${key}i${n}`)}
                      {it.children.length > 0 && (
                        <ul className="mt-1.5 list-[circle] space-y-1 pl-5 marker:text-secondary">
                          {it.children.map((child, m) => (
                            <li key={m} className="pl-1">
                              {renderInline(child, `${key}i${n}c${m}`)}
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  ))}
                </ListTag>
              );
            }
          }
        })}
      </div>
    </CitationNumbers.Provider>
  );
});
