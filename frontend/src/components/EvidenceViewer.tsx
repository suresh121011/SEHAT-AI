"use client";

import { type BBox, regionStyle, type Region } from "@/lib/evidence";

// Shows a stored page image with the selected field's source regions drawn on top (docs/14 §5B).
// Boxes are positioned in percent of the page, so they stay on the printed text at any width.
// A region without valid geometry is never drawn; the text says so instead.

type Page = { page_index: number; width: number; height: number };

const ROLE_LABEL: Record<string, string> = { name: "test", value: "result", unit: "unit", range: "range", flag: "flag", line: "line", date: "date" };

export function EvidenceViewer({ imageUrl, page, regions, label }: { imageUrl: string; page: Page; regions: Region[]; label: string }) {
  const onPage = regions.filter((r) => r.page_index === page.page_index);
  const drawable = onPage.map((r) => ({ r, s: regionStyle(r.bbox as BBox, page) })).filter((x) => x.s);
  return (
    <figure className="space-y-1">
      <div className="relative w-full overflow-hidden rounded border border-black/20 dark:border-white/20" style={{ aspectRatio: `${page.width} / ${page.height}` }}>
        {/* eslint-disable-next-line @next/next/no-img-element -- authenticated, no-store page image via the same-origin proxy */}
        <img src={imageUrl} alt={`Page ${page.page_index + 1} of the uploaded document`} className="absolute inset-0 h-full w-full" />
        {drawable.map(({ r, s }, i) => (
          <span key={i} aria-hidden="true" className="absolute border-2 border-fuchsia-600 bg-fuchsia-400/20" style={s!}>
            <span className="absolute -top-4 left-0 whitespace-nowrap bg-fuchsia-600 px-1 text-[10px] leading-4 text-white">{ROLE_LABEL[r.role] ?? r.role}</span>
          </span>
        ))}
      </div>
      <figcaption className="text-xs" aria-live="polite">
        {drawable.length > 0
          ? `Highlighted on page ${page.page_index + 1}: where "${label}" was read (${drawable.map((d) => ROLE_LABEL[d.r.role] ?? d.r.role).join(", ")}). A box shows where text was read, not that it was read correctly.`
          : onPage.length === 0 && regions.length > 0
            ? `"${label}" is on another page.`
            : `Can't point to "${label}" on the image — check the paper copy.`}
        {drawable.some((d) => d.r.granularity === "line" || d.r.granularity === "block") && " Highlight shows the whole line."}
      </figcaption>
    </figure>
  );
}
