import { describe, expect, it } from "vitest";
import { nextStreamRevealLength, STREAM_REVEAL_TARGET_MS } from "../utils/streamReveal";

describe("adaptive stream reveal", () => {
  it("reveals at least one character on the next frame without committing an entire large chunk", () => {
    const count = nextStreamRevealLength(360, 16, 0);
    expect(count).toBeGreaterThan(0);
    expect(count).toBeLessThan(360);
  });

  it("clears a pending burst within the target display window", () => {
    let pending = 360;
    for (let age = 0; age < STREAM_REVEAL_TARGET_MS && pending > 0; age += 16) {
      pending -= nextStreamRevealLength(pending, 16, age);
    }
    expect(pending).toBe(0);
  });

  it("flushes immediately for terminal frames and reduced-motion users", () => {
    expect(nextStreamRevealLength(120, 16, 0, { force: true })).toBe(120);
    expect(nextStreamRevealLength(120, 16, 0, { reducedMotion: true })).toBe(120);
  });
});
