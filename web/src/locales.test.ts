import { describe, expect, it } from "vitest";

import en from "./locales/en.json";
import es from "./locales/es.json";

function keys(value: unknown, prefix = ""): string[] {
  if (Array.isArray(value)) return [`${prefix}[${value.length}]`];
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([k, v]) => keys(v, prefix ? `${prefix}.${k}` : k));
  }
  return [prefix];
}

describe("locales", () => {
  it("English and Spanish have exactly the same keys", () => {
    expect(keys(es).sort()).toEqual(keys(en).sort());
  });
});
