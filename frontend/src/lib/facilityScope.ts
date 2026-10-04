// Facility-scope wording for the reviewer workstation and governance bars (GET /auth/me). Pure: no React, no DOM.
// The server decides scope and isolation; this only words what it said. Older servers omit the fields, so the
// "unknown" case says so rather than guessing either way.

export type FacilityIsolation = "enforced" | "off";

export type AuthMe = {
  role: string;
  facility_scope?: string[] | null;
  facility_isolation?: FacilityIsolation | string | null;
};

export type FacilityNotice = {
  /** `open` = no isolation (every account sees every facility); `scoped` = server-limited; `unknown` = not reported. */
  kind: "open" | "scoped" | "unknown";
  text: string;
};

export function facilityNotice(me: AuthMe | null): FacilityNotice {
  if (!me || (me.facility_isolation !== "enforced" && me.facility_isolation !== "off")) {
    return { kind: "unknown", text: "Facility access: not reported by the server. Do not assume a facility privacy wall is active." };
  }
  if (me.facility_isolation === "off") {
    return { kind: "open", text: "Warning: this demo shows every clinic's patients to every account. No facility privacy wall is active." };
  }
  // Enforced isolation with `facility_scope: null` means the server granted every facility (e.g. a "*" supervisor).
  if (me.facility_scope === null || me.facility_scope === undefined) return { kind: "scoped", text: "This account can see all facilities (set by the server)." };
  const scope = me.facility_scope.map((s) => s.trim()).filter(Boolean);
  if (scope.includes("*")) return { kind: "scoped", text: "This account can see all facilities (set by the server)." };
  if (scope.length === 0) return { kind: "scoped", text: "Patient lists are limited to: no facility (set per demo account by the server)." };
  return { kind: "scoped", text: `Patient lists are limited to: ${scope.join(", ")} (set per demo account by the server).` };
}
