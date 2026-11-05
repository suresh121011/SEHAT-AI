// Document review types (backend app/ocr/service.document_view). Shared by the ANM documents step and the medical
// officer's verification page, which use the same review components (components/review/DocumentChecks.tsx).
import type { DocType } from "@/components/DocumentUpload";
import type { Region } from "@/lib/evidence";

export type Check = { check: string; status: string; reason: string };
export type Reading = { engine: string; text: string; score: number | null };
export type Reference = { status: string; citation: string; population: string; band: { label: string } | null; note: string } | null;
export type Field = {
  field_id: string; kind: "lab" | "medication"; page_index: number; regions: Region[]; regions_sha256: string; page_png_sha256: string;
  readings: Reading[]; checks: Check[]; band: string; field_confidence: number | null; disputed: boolean; can_confirm: boolean;
  review_status: string; review: { event_id: string; outcome: string; actor_role: string } | null;
  reviewed_value: Record<string, unknown> | null;
  reviewed_ranges?: { printed_range_status: string; reference: Reference };
  // lab
  name_raw?: string; analyte_key?: string | null; value?: { raw: string; comparator: string | null }; unit?: { raw: string; key: string | null }; range?: { raw: string };
  flag_raw?: string; printed_range_status?: string; reference?: Reference;
  // medication
  line_raw?: string; drug_raw?: string; strength_raw?: string | null; dosage_pattern?: string | null; frequency_raw?: string | null;
  duration_raw?: string | null; rxnorm?: { status: string; name: string | null; reason: string } | null;
  disputed_parts?: string[]; strength_status?: "readings_differ" | "read" | "not_read"; strength_correctable?: boolean;
};
export type Doc = {
  document_id: string; status: string; document_type: DocType; engines: Record<string, string>; overall_confidence: number | null;
  pages: { page_index: number; width: number; height: number; png_sha256: string }[];
  dates: { collected_date: string | null; report_date: string | null } | null;
  attestation: { event_id: string; answer: string; actor_role: string } | null; fields: Field[]; status_note: string; created_at: string;
  deleted?: { reason: string; at: string; by_role: string };
  quality?: { ok: boolean; reasons: string[] }[] | null;
};
