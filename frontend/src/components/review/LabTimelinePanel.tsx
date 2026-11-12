"use client";

// Lab timeline in the case workspace (docs/22; brief item 2). Checked document values of this visit and the patient's
// linked visits, by date, with the gaps named. View only: values never change triage or urgency, and differences are
// stated as higher or lower than the previous result, never as better or worse.
import Link from "next/link";
import { useEffect, useState } from "react";

import { SectionCard } from "@/components/review/Panel";
import { Notice } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateLine, resultLine, visitWords, visitsLine, type LabTimeline } from "@/lib/labTimeline";

export function LabTimelinePanel({ caseId }: { caseId: string }) {
  const [data, setData] = useState<LabTimeline | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    api.get<LabTimeline>(`cases/${caseId}/lab-timeline`, { signal: ctrl.signal }).then(
      (d) => {
        setData(d);
        setError(null);
      },
      (err) => {
        if (!ctrl.signal.aborted) setError(err instanceof ApiError ? err.message : "The server could not be reached. Try again.");
      },
    );
    return () => ctrl.abort();
  }, [caseId]);

  return (
    <SectionCard id={`lab-timeline-${caseId}`} title="Lab timeline" icon="chart">
      {error && (
        <Notice tone="error" role="alert">
          {error}
        </Notice>
      )}
      {!data && !error && <p className="text-sm text-muted">Loading lab results…</p>}
      {data && (
        <>
          {data.other_visits_text && <p className="text-sm text-muted">{data.other_visits_text}</p>}
          {data.other_visits === "shown" && <p className="text-sm text-muted">{visitsLine(data.visits.length)}</p>}
          {data.gaps.length > 0 && (
            <Notice tone="warning" title="Gaps">
              <ul className="list-disc pl-5">
                {data.gaps.map((g) => (
                  <li key={g.code}>{g.text}</li>
                ))}
              </ul>
            </Notice>
          )}
          {data.tests.length === 0 ? (
            <p className="text-sm">No checked lab values yet. Values appear here after a person confirms them against the report.</p>
          ) : (
            <ul className="space-y-3" aria-label="Lab tests">
              {data.tests.map((t) => (
                <li key={t.analyte} className="space-y-1 rounded-lg border border-subtle p-3">
                  <p className="font-bold">
                    {t.label}
                    {t.flags.map((f) => (
                      <span key={f.code} className="ml-2 rounded-lg border border-warning/40 px-2 py-0.5 text-xs font-normal">
                        {f.text}
                      </span>
                    ))}
                  </p>
                  <ol className="space-y-1 border-l-2 border-subtle pl-3 text-sm">
                    {t.results.map((r) => (
                      <li key={r.source.field_id}>
                        <span className="font-bold">{dateLine(r)}</span>
                        <span className="text-muted"> · {visitWords(r.current_visit)}</span>
                        <br />
                        {resultLine(r)}
                        <br />
                        <span className="text-xs text-muted">
                          Source: report page {r.source.page_index + 1} ·{" "}
                          <Link className="underline" href={`/dashboard/verify?case=${encodeURIComponent(r.case_id)}`}>
                            open the report checks
                          </Link>
                        </span>
                      </li>
                    ))}
                  </ol>
                </li>
              ))}
            </ul>
          )}
          <p className="text-xs text-muted">{data.note}</p>
        </>
      )}
    </SectionCard>
  );
}
