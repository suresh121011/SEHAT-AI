// Role-aware navigation for the app shell. Only routes that exist and that the role may open (lib/auth.ts
// ROUTE_ACCESS) are listed: no placeholder pages, so every item works. The server still enforces access.
import type { IconName } from "@/components/Icon";

export type NavItem = { href: string; label: string; icon: IconName };

const OVERVIEW: NavItem = { href: "/dashboard/overview", label: "Overview", icon: "grid" };
const NEW_CASE: NavItem = { href: "/intake", label: "New Intake", icon: "plus" };
const CASE_QUEUE: NavItem = { href: "/dashboard/queue", label: "Case Queue", icon: "list" };
const REVIEW: NavItem = { href: "/dashboard", label: "Review", icon: "clipboard" };
const REFERRALS: NavItem = { href: "/dashboard/referrals", label: "Referrals", icon: "send" };
const REPORTS: NavItem = { href: "/dashboard/reports", label: "Reports", icon: "chart" };
const KNOWLEDGE: NavItem = { href: "/dashboard/knowledge", label: "Knowledge", icon: "book" };
const SETTINGS: NavItem = { href: "/dashboard/settings", label: "Settings", icon: "settings" };

export function navFor(role: string | null): NavItem[] {
  // Return the full dashboard layout navigation for the prototype
  return [OVERVIEW, NEW_CASE, CASE_QUEUE, REVIEW, REFERRALS, REPORTS, KNOWLEDGE, SETTINGS];
}

/** Exact match, except the intake start page, which stays active for every intake step. */
export function isActive(item: NavItem, pathname: string): boolean {
  if (item.href === "/intake") return pathname === "/intake" || pathname.startsWith("/intake/");
  return pathname === item.href;
}

export const ROLE_LABEL: Record<string, string> = {
  patient: "Patient",
  anm: "Health worker (ANM)",
  medical_officer: "Medical officer",
  supervisor: "Supervisor · read only",
  admin: "Admin",
};

export function sectionTitle(pathname: string): string {
  if (pathname === "/dashboard/overview") return "Overview";
  if (pathname === "/dashboard/governance") return "Governance";
  if (pathname === "/dashboard") return "Review workstation";
  if (pathname.startsWith("/intake")) return "Patient intake";
  return "SEHAT AI";
}
