import { describe, expect, it } from "vitest";
import { counterTone } from "./DiskHealth";

describe("teinte des secteurs défectueux", () => {
  it("0 = bon, stable = à surveiller, en hausse = à remplacer", () => {
    expect(counterTone(0, 0, 5)).toBe("ok");
    expect(counterTone(8, 8, 5)).toBe("warn");
    expect(counterTone(9, 8, 197)).toBe("bad");
    expect(counterTone(60, null, 5)).toBe("bad");
  });
  it("erreurs de câble : jamais « à remplacer » (ce n'est pas le disque)", () => {
    expect(counterTone(500, 2, 199)).toBe("warn");
  });
});
