import { describe, expect, it } from "vitest";
import { citationNumbers, excerptMarkdown, headlineFigure, parseEvidenceCitation, stripEvidenceIds } from "./text";

describe("stripEvidenceIds", () => {
  it("removes single and grouped evidence citations", () => {
    expect(stripEvidenceIds("Revenue was 10 [ev_0123456789ab]. Costs fell [ev_0123456789ab, ev_ba9876543210].")).toBe(
      "Revenue was 10. Costs fell.",
    );
  });
});

describe("parseEvidenceCitation", () => {
  it("parses valid single and grouped evidence citations", () => {
    const text = "See [ev_0123456789ab, ev_ba9876543210] for details.";
    expect(parseEvidenceCitation(text, 4)).toEqual({
      ids: ["ev_0123456789ab", "ev_ba9876543210"],
      end: 38,
    });
    expect(text.slice(4, 38)).toBe("[ev_0123456789ab, ev_ba9876543210]");
  });

  it("rejects malformed or partial evidence citations", () => {
    expect(parseEvidenceCitation("[ev_short]", 0)).toBeNull();
    expect(parseEvidenceCitation("[ev_0123456789ab, nope]", 0)).toBeNull();
    expect(parseEvidenceCitation("[ev_0123456789ab", 0)).toBeNull();
  });
});

describe("citationNumbers", () => {
  it("numbers evidence ids in order of first use", () => {
    const numbers = citationNumbers("A [ev_0123456789ab]. B [ev_ba9876543210, ev_0123456789ab].");
    expect([...numbers.entries()]).toEqual([
      ["ev_0123456789ab", 1],
      ["ev_ba9876543210", 2],
    ]);
  });
});

describe("headlineFigure", () => {
  it("takes the first bold figure", () => {
    expect(headlineFigure("Orders grew. Net sales were **₹3,000.00** in March. Fees were **₹90**.")).toBe("₹3,000.00");
  });

  it("pulls the figure out of a bold sentence", () => {
    expect(headlineFigure("**Paid orders lead at ₹8,500.00** [ev_0123456789ab].")).toBe("₹8,500.00");
    expect(headlineFigure("Churn rose to **4.2%** this quarter.")).toBe("4.2%");
  });

  it("ignores bold text without a number", () => {
    expect(headlineFigure("**Note:** no figures here.")).toBeNull();
  });

  it("ignores dates, years, ordinals, leading-zero IDs, and structural counts", () => {
    expect(headlineFigure("**Top 5** products made **$12,400**.")).toBe("$12,400");
    expect(headlineFigure("**In 2026**, net sales were **₹9,000**.")).toBe("₹9,000");
    expect(headlineFigure("**Q3 2025** revenue hit **4.5%**.")).toBe("4.5%");
    expect(headlineFigure("**Order #0042** on **2026-11-27** totaled **1st** place **₹500**.")).toBe("₹500");
    expect(headlineFigure("**Top 5** items only.")).toBeNull();
  });
});

describe("excerptMarkdown", () => {
  it("skips a leading heading so it doesn't run into the first sentence", () => {
    expect(excerptMarkdown("## Summary\n\nRevenue grew **14.2%** to $1.24M. North led.\n\n- North: $512K")).toBe(
      "Revenue grew 14.2% to $1.24M.",
    );
  });

  it("drops evidence ids and list markers", () => {
    expect(excerptMarkdown("- Sales were 10 [ev_0123456789ab]. More follows.")).toBe("Sales were 10.");
  });

  it("falls back to the heading when the answer is only a heading", () => {
    expect(excerptMarkdown("### Revenue is flat")).toBe("Revenue is flat");
  });

  it("returns an empty string for a missing answer and trims long text", () => {
    expect(excerptMarkdown(undefined)).toBe("");
    expect(excerptMarkdown("a".repeat(300), 10)).toBe("aaaaaaaaaa…");
  });
});
