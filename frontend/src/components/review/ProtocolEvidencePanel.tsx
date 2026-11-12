"use client";

// Protocol evidence beside "Why the rules gave …" (docs/23; feature #40). For each fired rule, its cited source and the
// passage that supports it, shown as checked only after a named reviewer checked it against the source. Until then the
// source and the location as cited are shown. Display only: nothing here sets, changes or explains away urgency.
import { useEffect, useState } from "react";

import { SectionCard } from "@/components/review/Panel";
import { Notice } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { STATE_WORDS, idsParam, sourceHref, type EvidenceResponse } from "@/lib/protocolEvidence";

export function ProtocolEvidencePanel({ ruleIds }: { ruleIds: string[] }) {
  const ids = idsParam(ruleIds);
  const [data, setData] = useState<EvidenceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ids) return;
    const ctrl = new AbortController();
    api.get<EvidenceResponse>(`evidence/rules?ids=${encodeURIComponent(ids)}`, { signal: ctrl.signal }).then(
      (d) => {
        setData(d);
        setError(null);
      },
      (err) => {
        if (!ctrl.signal.aborted) setError(err instanceof ApiError ? err.message : "The server could not be reached. Try again.");
      },
    );
    return () => ctrl.abort();
  }, [ids]);

  if (!ids) return null;
  return (
    <SectionCard id="protocol-evidence-heading" title="Protocol evidence for these rules" icon="book">
      {error && (
        <Notice tone="error" role="alert">
          {error}
        </Notice>
      )}
      {!data && !error && <p className="text-sm text-muted">Loading the sources…</p>}
      {data && (
        <>
          <ul className="space-y-2">
            {data.rules.map((r) => {
              const href = r.source ? sourceHref(r.source.reference) : null;
              return (
                <li key={r.rule_id} className="space-y-1 rounded-lg border border-subtle p-2 text-sm">
                  <p className="font-mono text-xs text-muted">{r.rule_id}</p>
                  {r.source ? (
                    <p>
                      Source: {r.source.citation}
                      {href && (
                        <>
                          {" "}
                          (
                          <a className="underline" href={href} target="_blank" rel="noopener noreferrer">
                            open the source
                          </a>
                          )
                        </>
                      )}
                    </p>
                  ) : (
                    <p className="text-muted">No evidence entry for this rule.</p>
                  )}
                  {r.passages.map((p) => (
                    <div key={p.passage_id} className={`rounded-lg border-l-4 px-2 py-1 ${p.state === "VERIFIED" ? "border-primary" : "border-warning"}`}>
                      <p className="text-xs font-bold">{STATE_WORDS[p.state]}</p>
                      {p.text ? <blockquote className="my-1 italic">“{p.text}”</blockquote> : null}
                      {p.locator && <p className="text-xs">Location: {p.locator}</p>}
                      <p className="text-xs text-muted">{p.message}</p>
                    </div>
                  ))}
                </li>
              );
            })}
          </ul>
          <p className="text-xs text-muted">{data.note}</p>
        </>
      )}
    </SectionCard>
  );
}
