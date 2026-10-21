// SPDX-License-Identifier: MIT
import { describe, expect, it } from "vitest";
import en from "../i18n/en.json";
import { dictionaries, isDraft, translator } from "../i18n";

const reasonCodes = [
  "tilt_rate_high", "crack_opening", "ext_stretch", "vibration_burst", "iforest_anomaly", "neighbour_consensus",
  "ttf_short", "node_silent", "sensor_fault_suspected", "ground_heating", "level_change",
];

describe("i18n", () => {
  it("Hindi and Bengali translate every English key", () => {
    for (const lang of ["hi", "bn"] as const) {
      const missing = Object.keys(en).filter((k) => !(k in dictionaries[lang]));
      expect(missing, lang).toEqual([]);
    }
  });

  it("every reason code in docs/mqtt-topics.md has a label", () => {
    for (const r of reasonCodes) expect(en).toHaveProperty(`reason.${r}`);
  });

  it("Santali falls back to Hindi, then English, and is flagged as a draft", () => {
    const t = translator("sat");
    expect(t("level.critical")).toBe("ᱵᱤᱯᱚᱫ");
    expect(t("ttf")).toBe(dictionaries.hi.ttf);
    expect(isDraft("sat")).toBe(true);
    expect(isDraft("hi")).toBe(false);
  });

  it("unknown keys come back as the key itself", () => {
    expect(translator("bn")("reason.something_new")).toBe("reason.something_new");
  });
});
