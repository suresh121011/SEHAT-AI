"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { DemoBanner } from "@/components/DemoBanner";
import { ApiError, api } from "@/lib/api";

const SCENARIOS = [
  ["opd", "OPD triage"],
  ["maternal", "Maternal"],
  ["chronic_ncd", "Chronic NCD"],
  ["health_camp", "Health camp"],
  ["campus_fever", "Campus fever"],
  ["occupational", "Occupational"],
  ["referral", "Referral"],
] as const;

export default function IntakeHome() {
  const router = useRouter();
  const [scenario, setScenario] = useState("opd");
  const [facility, setFacility] = useState("PHC-KHURDA-01");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<{ case_id: string }>("cases", { scenario, facility_code: facility });
      router.push(`/intake/consent?case=${created.case_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message}${err.requestId ? ` (ref ${err.requestId})` : ""}` : "Could not start a case");
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto max-w-xl space-y-6">
      <DemoBanner />
      <div>
        <h1 className="text-2xl font-semibold">Start a new case</h1>
        <p className="mt-1 text-sm opacity-70">No name or ID number is collected. The case gets a random token.</p>
      </div>
      <form onSubmit={start} className="space-y-4">
        <label className="block text-sm">
          Scenario
          <select className="mt-1 block w-full rounded border border-black/20 bg-transparent p-2 dark:border-white/20" value={scenario} onChange={(e) => setScenario(e.target.value)}>
            {SCENARIOS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          Facility code
          <input
            className="mt-1 block w-full rounded border border-black/20 bg-transparent p-2 font-mono dark:border-white/20"
            value={facility}
            onChange={(e) => setFacility(e.target.value.toUpperCase())}
            pattern="[A-Z0-9-]{3,32}"
            required
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy} className="rounded bg-blue-600 px-4 py-2 font-medium text-white hover:bg-blue-700 disabled:opacity-60">
          {busy ? "Starting…" : "Start case and take consent"}
        </button>
      </form>
    </section>
  );
}
