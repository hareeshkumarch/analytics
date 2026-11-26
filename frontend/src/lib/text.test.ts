import { describe, expect, it } from "vitest";
import { stripEvidenceIds } from "./text";

describe("stripEvidenceIds", () => {
  it("removes single and grouped evidence citations", () => {
    expect(stripEvidenceIds("Revenue was 10 [ev_0123456789ab]. Costs fell [ev_0123456789ab, ev_ba9876543210].")).toBe(
      "Revenue was 10. Costs fell.",
    );
  });
});
