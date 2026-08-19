import { describe, expect, it } from "vitest";
import { formatStreamElapsedSeconds } from "../utils/streamElapsed";

describe("stream elapsed display", () => {
  it("renders wall-clock duration as stable whole seconds", () => {
    expect(formatStreamElapsedSeconds(0)).toBe("0s");
    expect(formatStreamElapsedSeconds(999)).toBe("0s");
    expect(formatStreamElapsedSeconds(1_000)).toBe("1s");
    expect(formatStreamElapsedSeconds(8_750)).toBe("8s");
  });

  it("never renders negative or non-finite elapsed time", () => {
    expect(formatStreamElapsedSeconds(-1)).toBe("0s");
    expect(formatStreamElapsedSeconds(Number.NaN)).toBe("0s");
  });
});
