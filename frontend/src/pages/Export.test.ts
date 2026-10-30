import { describe, expect, it } from "vitest";
import { period } from "./Export";

describe("période d'un extrait", () => {
  it("convertit le jour et les heures saisis", () => {
    const [a, b] = period("2026-10-30", "14:50", "15:05")!;
    expect(b - a).toBe(15 * 60);
    expect(new Date(a * 1000).getHours()).toBe(14);
  });
  it("passe au lendemain quand la fin est avant le début", () => {
    const [a, b] = period("2026-10-30", "23:50", "00:10")!;
    expect(b - a).toBe(20 * 60);
  });
  it("refuse une saisie incomplète", () => {
    expect(period("", "10:00", "11:00")).toBeNull();
  });
});

describe("tranche horaire", () => {
  const r = { from: 1030, to: 2000 };      // début à 17 min 10 s, base ramenée à la minute (1020)
  it("place début et fin sur la barre de la tranche, rien en dehors", async () => {
    const { markPos } = await import("./Export");
    expect(markPos(1030, r)).toBe(0);
    expect(markPos(2000, r)).toBe(100);
    expect(markPos(1515, r)).toBe(50);
    expect(markPos(2001, r)).toBeNull();
    expect(markPos(undefined, r)).toBeNull();
  });
  it("trouve le morceau de 5 min et la position dedans", async () => {
    const { locate } = await import("./Export");
    expect(locate(1030, r)).toEqual([0, 10]);
    expect(locate(1320, r)).toEqual([1, 0]);
    expect(locate(1999, r)).toEqual([3, 79]);
    expect(locate(5000, r)).toEqual([3, 80]);      // au-delà : ramené à la fin de la tranche
    expect(locate(0, r)).toEqual([0, 10]);           // avant : ramené au début
  });
});

describe("nom du fichier enregistré", () => {
  it("pharmacie, jour et heure du début, sans caractère gênant", async () => {
    const { fileName } = await import("./Export");
    const ts = new Date(2026, 9, 30, 14, 5, 20).getTime() / 1000;
    expect(fileName("Pharmacie de l'Église", ts)).toBe("Video_Pharmacie_de_l__glise_2026-10-30_14h05.zip");
  });
});
