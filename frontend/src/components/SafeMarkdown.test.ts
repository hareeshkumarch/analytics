import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EvidenceContext, SafeMarkdown } from "./SafeMarkdown";

function renderMd(content: string, withHandler = false): string {
  const el = React.createElement(SafeMarkdown, { content });
  return renderToStaticMarkup(
    withHandler ? React.createElement(EvidenceContext.Provider, { value: () => {} }, el) : el,
  );
}

describe("SafeMarkdown", () => {
  it("renders formatting, safe links, evidence chips, and preserves snake_case", () => {
    const html = renderMd(
      "### Summary ###\n\nMetric `code_id` for total_revenue_usd was **100** and _margin_ grew [see docs](https://example.com/docs) [ev_0123456789ab, ev_ba9876543210]. Unsafe [bad](javascript:alert(1)).",
      true,
    );
    expect(html).toContain("<h4");
    expect(html).toContain(">Summary<");
    expect(html).toContain(">code_id</code>");
    expect(html).toContain("for total_revenue_usd was");
    expect(html).toContain("<strong");
    expect(html).toContain("<em");
    expect(html).toContain('href="https://example.com/docs"');
    expect(html).toContain('aria-label="Source 1: show the query and rows behind this figure"');
    expect(html).toContain('aria-label="Source 2: show the query and rows behind this figure"');
    expect(html).not.toContain("ev_0123456789ab");
    expect(html).toContain("Unsafe [bad](javascript:alert(1)).");
    expect(html).not.toContain('href="javascript:');
  });

  it("handles huge strings of unclosed delimiters in linear time", () => {
    const hostile = `${"[".repeat(20_000)} ${"*".repeat(20_000)} ${"`".repeat(20_000)}`;
    const start = performance.now();
    const html = renderMd(hostile);
    const elapsedMs = performance.now() - start;
    expect(html.length).toBeGreaterThan(60_000);
    expect(elapsedMs).toBeLessThan(1000);
  });
});
