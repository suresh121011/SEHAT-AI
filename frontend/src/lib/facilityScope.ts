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

// Facility choice on the "start a case" form, from the server's own scope for this account (GET /auth/me). The server
// still decides (a code outside the scope is refused with 403); this only offers what it would accept.
export type FacilityChoice =
  | { kind: "fixed"; code: string } // exactly one facility: pre-filled, nothing to choose
  | { kind: "choose"; codes: string[] } // several: pick one of them
  | { kind: "free" } // no list from the server (isolation off, "*", or an older server): type the code
  | { kind: "none" }; // isolation on and no facility at all: cases cannot be started

export function facilityChoice(me: AuthMe | null): FacilityChoice {
  if (!me || me.facility_isolation !== "enforced" || me.facility_scope === null || me.facility_scope === undefined) return { kind: "free" };
  const scope = [...new Set(me.facility_scope.map((s) => s.trim().toUpperCase()).filter(Boolean))].sort();
  if (scope.includes("*")) return { kind: "free" };
  if (scope.length === 0) return { kind: "none" };
  return scope.length === 1 ? { kind: "fixed", code: scope[0] } : { kind: "choose", codes: scope };
}

/** Plain words for a refused facility (403 from POST /cases): which codes this account may use, if the server said. */
export function facilityRefusal(choice: FacilityChoice): string {
  if (choice.kind === "fixed") return `This account can start cases only in ${choice.code}.`;
  if (choice.kind === "choose") return `This account can start cases only in: ${choice.codes.join(", ")}.`;
  if (choice.kind === "none") return "This account is not assigned to any facility, so it cannot start cases. Ask your administrator.";
  return "This account is not allowed to start cases in that facility. Check the facility code.";
}
