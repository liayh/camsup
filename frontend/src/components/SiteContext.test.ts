import { describe, expect, it } from "vitest";
import type { Overview } from "../api/types";
import { siteState } from "./SiteContext";

const cam = (id: number, site: number, online: number | null) => ({ id, site_id: site, enabled: 1, online }) as unknown as Overview["cameras"][number];
const ov = (cameras: Overview["cameras"], extra: Partial<Overview> = {}) => ({ cameras, nvr: [], alerts: [], ...extra }) as unknown as Overview;

describe("état d'une pharmacie (colonne de gauche)", () => {
  it("sans caméra, en ligne, alerte, hors ligne", () => {
    expect(siteState(ov([]), 1)).toBe("vide");
    expect(siteState(ov([cam(1, 1, 1), cam(2, 1, null)]), 1)).toBe("ok");
    expect(siteState(ov([cam(1, 1, 1), cam(2, 1, 0)]), 1)).toBe("warn");          // une caméra perdue
    expect(siteState(ov([cam(1, 1, 0), cam(2, 1, 0)]), 1)).toBe("off");           // plus aucune caméra vue
    expect(siteState(ov([cam(1, 1, 1)], { nvr: [{ site_id: 1, error: "injoignable" }] } as never), 1)).toBe("off");
    expect(siteState(ov([cam(1, 1, 1)], { alerts: [{ site_id: 1 }] } as never), 1)).toBe("warn");
    expect(siteState(ov([cam(1, 2, 0)]), 1)).toBe("vide");                         // autre pharmacie
    expect(siteState(ov([cam(1, 1, null), cam(2, 1, null)]), 1)).toBe("attente");  // pas encore relevé
  });
});
