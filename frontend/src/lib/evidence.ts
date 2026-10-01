// Source-evidence geometry for the document review screen (docs/14 §5B). Pure functions, no DOM:
// tested with `node --test` (src/lib/evidence.test.ts).
//
// Boxes are integer pixels [x0, y0, x1, y1] on the stored page image (origin top-left, x1/y1 exclusive).
// They are drawn as percentages of the page, so a highlight stays on the same printed text at any
// display size. A box that is missing, empty or outside the page is never drawn.

export type BBox = [number, number, number, number];
export type Region = { role: string; page_index: number; bbox: BBox; line_id?: string; granularity?: string };
export type PageDims = { width: number; height: number };
export type BoxStyle = { left: string; top: string; width: string; height: string };

export function validBox(b: unknown, page: PageDims): b is BBox {
  if (!Array.isArray(b) || b.length !== 4 || !b.every((n) => typeof n === "number" && Number.isFinite(n))) return false;
  const [x0, y0, x1, y1] = b as number[];
  return x0 >= 0 && y0 >= 0 && x1 <= page.width && y1 <= page.height && x1 > x0 && y1 > y0;
}

export function regionStyle(b: unknown, page: PageDims): BoxStyle | null {
  if (!validBox(b, page)) return null;
  const [x0, y0, x1, y1] = b;
  const pct = (v: number, of: number) => `${((v / of) * 100).toFixed(4)}%`;
  return { left: pct(x0, page.width), top: pct(y0, page.height), width: pct(x1 - x0, page.width), height: pct(y1 - y0, page.height) };
}

/** Where a box lands, in CSS pixels, when the page is displayed at `displayWidth` (aspect kept). */
export function toDisplay(b: BBox, page: PageDims, displayWidth: number): BBox {
  const s = displayWidth / page.width;
  return [b[0] * s, b[1] * s, b[2] * s, b[3] * s];
}

/** Which field's box (on this page) contains a click at display point (x, y)? Smallest box wins. */
export function fieldAt<T extends { id: string; regions: Region[] }>(
  fields: T[], pageIndex: number, page: PageDims, displayWidth: number, x: number, y: number,
): string | null {
  let best: { id: string; area: number } | null = null;
  for (const f of fields) {
    for (const r of f.regions) {
      if (r.page_index !== pageIndex || !validBox(r.bbox, page)) continue;
      const [x0, y0, x1, y1] = toDisplay(r.bbox, page, displayWidth);
      if (x >= x0 && x < x1 && y >= y0 && y < y1) {
        const area = (x1 - x0) * (y1 - y0);
        if (!best || area < best.area) best = { id: f.id, area };
      }
    }
  }
  return best ? best.id : null;
}

export type Instruction = { text: string; symbol: string };

/** One plain instruction per row (council R3.6 / Outsider). Never says "verified". */
export function instruction(f: { band: string; disputed: boolean; can_confirm: boolean; checks: { check: string; status: string; reason: string }[] }): Instruction {
  if (f.band === "human_entry") return { symbol: "✎", text: "Could not read — type it from the paper or reject" };
  if (f.disputed) return { symbol: "⇄", text: "Two different readings — choose the correct value" };
  if (f.checks.some((c) => c.check === "rxnorm" && c.status === "warn"))
    return { symbol: "?", text: "Drug name not confirmed against RxNorm — check the paper" };
  if (f.band === "amber") return { symbol: "!", text: "Read with some doubt — check the paper before confirming" };
  return { symbol: "○", text: "Ready to check — compare with the paper, then confirm" };
}

export const RANGE_TEXT: Record<string, { text: string; symbol: string }> = {
  below_range: { symbol: "▼", text: "Below the range" },
  within_range: { symbol: "●", text: "Within the range" },
  above_range: { symbol: "▲", text: "Above the range" },
  range_unavailable: { symbol: "—", text: "No range" },
  not_comparable: { symbol: "≠", text: "Can't compare (unit missing or different, sex-specific or unclear range)" },
  not_below_cutoff: { symbol: "△", text: "Not below the cut-off (the source gives no upper limit)" },
  below_upper_cutoff: { symbol: "▽", text: "Below the upper cut-off (the source gives no lower limit)" },
};

export const REVIEW_TEXT: Record<string, string> = {
  machine_read: "Machine-read — not yet checked",
  confirmed: "Confirmed by reviewer",
  corrected: "Corrected by reviewer",
  unsure: "Not sure — stays unresolved",
  rejected: "Rejected — not used",
};
