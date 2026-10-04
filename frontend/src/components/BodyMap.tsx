"use client";

import { useState } from "react";

import { Icon } from "@/components/Icon";
import { REGIONS, type View, labelOf, toggle } from "@/lib/bodyMap";

// A simple outline body (front/back) with selectable regions. The checkbox list beside the figure is the source of
// truth and the single keyboard / screen-reader path; the SVG is a hidden pointer/touch shortcut to the same toggles,
// so keyboard users do not meet every area twice.

function Outline() {
  return (
    <g fill="none" stroke="var(--border-input)" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
      <ellipse cx="100" cy="34" rx="22" ry="27" />
      <path d="M90 60h20v14l24 6c8 2 12 8 13 16l10 92c1 6-3 9-7 9h-6l-10-82-6 24v40l4 182c0 6-4 10-10 10h-14l-6-150h-4l-6 150H76c-6 0-10-4-10-10l4-182v-40l-6-24-10 82h-6c-4 0-8-3-7-9l10-92c1-8 5-14 13-16l24-6V60Z" />
    </g>
  );
}

export function BodyMap({ selected, onChange }: { selected: string[]; onChange: (next: string[]) => void }) {
  const [view, setView] = useState<View>("front");
  const regions = REGIONS.filter((r) => r.view === view);

  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="font-bold">Side of the body</legend>
        <div className="mt-2 flex gap-2">
          {(["front", "back"] as View[]).map((v) => (
            <label key={v} className={`flex min-h-11 cursor-pointer items-center gap-2 rounded border-2 px-4 ${view === v ? "border-primary bg-primary-tint font-bold" : "border-line bg-card"}`}>
              <input type="radio" name="bodyview" className="size-4" checked={view === v} onChange={() => setView(v)} />
              {v === "front" ? "Front" : "Back"}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-6 md:grid-cols-[minmax(0,260px)_1fr]">
        <figure className="mx-auto w-full max-w-[260px]">
          {/* Pointer/touch shortcut only: the checkbox list beside it is the keyboard and screen-reader path. */}
          <svg viewBox="0 0 200 420" aria-hidden="true" focusable="false" className="h-auto w-full">
            <Outline />
            {regions.map((r) => {
              const on = selected.includes(r.id);
              const common = {
                onClick: () => onChange(toggle(selected, r.id)),
                className: "cursor-pointer",
                fill: on ? "var(--primary)" : "transparent",
                fillOpacity: on ? 0.32 : 1,
                stroke: on ? "var(--primary)" : "transparent",
                strokeWidth: 2,
                pointerEvents: "all" as const,
              };
              return r.shape.kind === "ellipse" ? (
                <ellipse key={r.id} cx={r.shape.cx} cy={r.shape.cy} rx={r.shape.rx} ry={r.shape.ry} {...common}>
                  <title>{r.label}</title>
                </ellipse>
              ) : (
                <rect key={r.id} x={r.shape.x} y={r.shape.y} width={r.shape.w} height={r.shape.h} rx={r.shape.r} {...common}>
                  <title>{r.label}</title>
                </rect>
              );
            })}
            <text x="6" y="414" fontSize="11" fill="var(--text-muted)">
              {view === "front" ? "Patient's right" : "Patient's left"}
            </text>
            <text x="194" y="414" fontSize="11" fill="var(--text-muted)" textAnchor="end">
              {view === "front" ? "Patient's left" : "Patient's right"}
            </text>
          </svg>
          <figcaption className="mt-1 text-center text-sm text-muted">Tap an area on the picture, or tick it in the list. Patient&apos;s left is on the {view === "front" ? "right" : "left"} of the picture.</figcaption>
        </figure>

        <fieldset>
          <legend className="font-bold">Areas ({view === "front" ? "front" : "back"})</legend>
          <div className="mt-2 grid gap-1 sm:grid-cols-2">
            {regions.map((r) => (
              <label key={r.id} className="flex min-h-11 cursor-pointer items-center gap-3 rounded px-2 hover:bg-primary-tint">
                <input type="checkbox" className="size-5" checked={selected.includes(r.id)} onChange={() => onChange(toggle(selected, r.id))} />
                {r.label}
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      <p className="sr-only" aria-live="polite">
        {selected.length} {selected.length === 1 ? "area" : "areas"} selected
      </p>
      <div>
        <h2 className="font-bold">Selected areas</h2>
        {selected.length === 0 ? (
          <p className="text-muted">None selected.</p>
        ) : (
          <ul className="mt-2 flex flex-wrap gap-2">
            {selected.map((id) => (
              <li key={id}>
                <button type="button" onClick={() => onChange(toggle(selected, id))} className="inline-flex min-h-11 items-center gap-2 rounded-full border-2 border-primary bg-primary-tint px-4 font-bold text-primary">
                  {labelOf(id)} <Icon name="cross" size={16} />
                  <span className="sr-only">(remove)</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
