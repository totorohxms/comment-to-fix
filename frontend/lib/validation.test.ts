// Mirrors backend/tests/test_validation.py for the shared limits.

import { describe, expect, it } from "vitest";
import { COMMENT_MAX, validateComment } from "./validation";

describe("validateComment", () => {
  it("accepts normal text", () => {
    expect(validateComment("this button style is not right")).toBeNull();
  });

  it("rejects empty and whitespace-only text", () => {
    expect(validateComment("")).toMatch(/empty/);
    expect(validateComment("   \n\t ")).toMatch(/empty/);
  });

  it("accepts text at the limit, rejects over it", () => {
    expect(validateComment("a".repeat(COMMENT_MAX))).toBeNull();
    expect(validateComment("a".repeat(COMMENT_MAX + 1))).toMatch(/too long/);
  });

  it("trims before measuring", () => {
    expect(validateComment("  " + "a".repeat(COMMENT_MAX) + "  ")).toBeNull();
  });
});
