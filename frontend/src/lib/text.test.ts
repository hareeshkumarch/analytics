import { describe, expect, it } from "vitest";
import { parseEvidenceCitation, stripEvidenceIds } from "./text";

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
