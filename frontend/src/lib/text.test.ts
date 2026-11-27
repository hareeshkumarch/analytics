import { describe, expect, it } from "vitest";
import { citationNumbers, headlineFigure, parseEvidenceCitation, stripEvidenceIds } from "./text";

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
});
