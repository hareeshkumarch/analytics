"use client";

import React, { createContext, useContext, useMemo } from "react";
import { HScroll } from "@/components/HScroll";

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

function EvidenceChip({ id }: { id: string }) {
  const open = useContext(EvidenceContext);
  if (!open) return <span className="font-mono text-[0.84em] text-secondary">[{id}]</span>;
  return (
    <button
      type="button"
      onClick={() => open(id)}
      title="Show the query and rows behind this figure"
      className="mx-0.5 inline-flex items-center rounded-md bg-plum-soft px-1.5 py-px align-baseline font-mono text-[0.78em] text-plum transition-colors hover:bg-plum hover:text-on-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-plum"
    >
      {id}
    </button>
  );
}

const INLINE =
  /(\[ev_[0-9a-f]{12}(?:,\s*ev_[0-9a-f]{12})*\]|\*\*(?!\s)[^*\n]+?(?<!\s)\*\*|(?<![\w])__(?!\s)[^_\n]+?(?<!\s)__(?![\w])|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\)|\*(?![\s*])[^*\n]+?(?<!\s)\*|(?<![\w])_(?![\s_])[^_\n]+?(?<!\s)_(?![\w]))/g;

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const match of text.matchAll(INLINE)) {
    const token = match[0];
    const start = match.index ?? 0;
    if (start > last) out.push(text.slice(last, start));
    const key = `${keyPrefix}-${i++}`;
    if (token.startsWith("**") || token.startsWith("__")) {
      out.push(
        <strong key={key} className="font-semibold text-ink">
          {renderInline(token.slice(2, -2), key)}
        </strong>
      );
    } else if (token.startsWith("`")) {
      out.push(
        <code key={key} className="rounded-md bg-panel px-1.5 py-0.5 font-mono text-[0.86em] text-ink">
          {token.slice(1, -1)}
        </code>
      );
    } else if (/^\[ev_[0-9a-f]{12}(?:,\s*ev_[0-9a-f]{12})*\]$/.test(token)) {
      token
        .slice(1, -1)
        .split(",")
        .forEach((id, n) => out.push(<EvidenceChip key={`${key}-${n}`} id={id.trim()} />));
    } else if (token.startsWith("[")) {
      const m = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token);
      const href = m ? safeHref(m[2]) : null;
      out.push(
        href ? (
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="font-medium text-plum underline decoration-plum/30 underline-offset-2 hover:decoration-plum"
          >
            {m![1]}
            <span className="ml-1 text-[0.8em] font-normal text-secondary">({new URL(href).host || "email"})</span>
          </a>
        ) : (
          token
        )
      );
    } else {
      out.push(
        <em key={key} className="italic">
          {renderInline(token.slice(1, -1), key)}
        </em>
      );
    }
    last = start + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
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
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2].replace(/\s+#+$/, "") });
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

export function SafeMarkdown({ content }: { content: string }) {
  const blocks = useMemo(() => parse(content), [content]);
  return (
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
              <blockquote key={key} className="rounded-r-lg border-l-2 border-plum/40 bg-panel/60 py-2 pl-4 pr-3 text-body">
                {renderInline(b.text, key)}
              </blockquote>
            );
          case "code":
            return (
              <div key={key} className="overflow-hidden rounded-xl border border-border bg-panel">
                {b.lang && (
                  <div className="border-b border-border px-3.5 py-1.5 font-mono text-[11px] uppercase tracking-wide text-secondary">
                    {b.lang}
                  </div>
                )}
                <pre className="overflow-x-auto px-3.5 py-3 font-mono text-[12.5px] leading-relaxed text-ink">
                  <code>{b.text}</code>
                </pre>
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
  );
}
