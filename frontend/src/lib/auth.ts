// Roles, demo accounts and route access rules shared by middleware, routes and pages.

export type Role = "patient" | "anm" | "medical_officer" | "supervisor" | "admin";

export const SESSION_COOKIE = "sehat_session";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

// Pre-seeded demo accounts (docs/09 §1.4). One role per account.
export const DEMO_ACCOUNTS: { username: string; role: Role; label: string }[] = [
  { username: "patient_demo", role: "patient", label: "Patient" },
  { username: "anm_demo", role: "anm", label: "ANM / Health Worker" },
  { username: "mo_demo", role: "medical_officer", label: "Medical Officer" },
  { username: "supervisor_demo", role: "supervisor", label: "Supervisor" },
];

// docs/09 §1.6: /intake/* → patient, anm; /dashboard/* → medical officer, supervisor.
// First match wins: the governance page (aggregates, GET /audit/governance) is supervisor/admin only (docs/17).
export const ROUTE_ACCESS: { prefix: string; roles: Role[] }[] = [
  { prefix: "/dashboard/governance", roles: ["supervisor", "admin"] },
  { prefix: "/intake", roles: ["patient", "anm"] },
  { prefix: "/dashboard", roles: ["medical_officer", "supervisor"] },
];

// docs/08 P1: Patient → intake, MO → dashboard, Supervisor → governance.
export function homeFor(role: Role): string {
  switch (role) {
    case "patient":
    case "anm":
      return "/intake";
    case "medical_officer":
      return "/dashboard";
    case "supervisor":
      return "/dashboard/governance";
    default:
      return "/login";
  }
}

export function allowedRoles(pathname: string): Role[] | null {
  const rule = ROUTE_ACCESS.find((r) => pathname === r.prefix || pathname.startsWith(`${r.prefix}/`));
  return rule ? rule.roles : null;
}
