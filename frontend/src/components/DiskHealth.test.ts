import { describe, expect, it } from "vitest";
import { badSectors, counterTone, diskVerdict } from "./DiskHealth";
import type { Disk } from "../api/types";

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

const disk = (o: Partial<Disk>): Disk => ({ name: "HDD 1", model: "WD Purple", state: "Normal", level: "ok", temp: 38, hours: 9000, issues: [], ...o });
const ctr = (id: number, raw: number, base: number | null = raw) => ({ id, label: "", raw, base, since: 1 });

describe("verdict du bandeau des disques", () => {
  it("bon état : aucun secteur défectueux", () => {
    const d = disk({ counters: [ctr(5, 0), ctr(197, 0), ctr(198, 0), ctr(199, 0)] });
    expect(badSectors(d)).toBe(0);
    expect(diskVerdict(d)).toMatchObject({ tone: "ok", text: "aucun secteur défectueux" });
  });
  it("compte les secteurs mais pas les erreurs de câble", () => {
    expect(badSectors(disk({ counters: [ctr(5, 8), ctr(197, 2), ctr(199, 40)] }))).toBe(10);
  });
  it("SMART absent : santé d'après le NVR, secteurs inconnus", () => {
    const d = disk({});
    expect(badSectors(d)).toBeNull();
    expect(diskVerdict(d).text).toContain("non fourni");
  });
  it("à remplacer : la raison est affichée", () => {
    const d = disk({ level: "bad", issues: ["secteurs réalloués en hausse : 8 → 12"] });
    expect(diskVerdict(d)).toEqual({ tone: "bad", title: "À remplacer", text: "secteurs réalloués en hausse : 8 → 12" });
  });
});
