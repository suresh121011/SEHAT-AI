"use client";

// App shell for every signed-in screen: a dark-teal role-aware sidebar (collapsible to an icon rail on desktop, a
// modal drawer on small screens), a light top bar with the page section, server reachability, role and log out, and
// the persistent research-prototype footer. The role comes from GET /auth/me; the server still enforces access.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/Icon";
import { LogoutButton } from "@/components/LogoutButton";
import { Wordmark } from "@/components/shell/Brand";
import { ROLE_LABEL, isActive, navFor, sectionTitle } from "@/components/shell/nav";
import { ServerStatus } from "@/components/shell/ServerStatus";
import { useMe } from "@/lib/useReview";

const COLLAPSE_KEY = "sehat.sidebar.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function SidebarBody({ role, pathname, collapsed, onNavigate, onToggle, onClose }: {
  role: string | null;
  pathname: string;
  collapsed: boolean;
  onNavigate?: () => void;
  onToggle?: () => void;
  onClose?: () => void;
}) {
  const items = navFor(role);
  return (
    <div className="on-shell flex h-full min-h-0 flex-col">
      <div className={`flex h-[var(--topbar-h)] shrink-0 items-center border-b border-white/10 ${collapsed ? "justify-center px-2" : "justify-between gap-2 px-4"}`}>
        <Wordmark onShell collapsed={collapsed} subtitle="Clinical triage · prototype" />
        {onClose && (
          <button type="button" onClick={onClose} className="inline-flex size-10 items-center justify-center rounded-lg hover:bg-white/10" aria-label="Close navigation">
            <Icon name="cross" size={20} />
          </button>
        )}
      </div>

      <nav aria-label="Main" className="min-h-0 flex-1 overflow-y-auto px-3 py-4">
        {!collapsed && <p className="px-3 pb-2 text-xs font-bold uppercase tracking-wider text-shell-muted/80">Workspace</p>}
        {role === null ? (
          <p className={`px-3 text-sm text-shell-muted ${collapsed ? "sr-only" : ""}`}>Loading menu…</p>
        ) : (
          <ul className="space-y-1">
            {items.map((item) => {
              const active = isActive(item, pathname);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    title={collapsed ? item.label : undefined}
                    className={`relative flex min-h-11 items-center gap-3 rounded-lg text-[0.9375rem] font-bold transition-colors duration-150 ${collapsed ? "justify-center px-0" : "px-3"} ${active ? "bg-white/[0.14] text-shell-ink" : "text-shell-muted hover:bg-white/[0.07] hover:text-shell-ink"}`}
                  >
                    {active && <span className="absolute inset-y-2 left-0 w-1 rounded-r bg-[#9fe0d6]" aria-hidden="true" />}
                    <Icon name={item.icon} size={20} />
                    <span className={collapsed ? "sr-only" : ""}>{item.label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </nav>

      <div className={`shrink-0 space-y-3 border-t border-white/10 py-4 ${collapsed ? "px-2" : "px-4"}`}>
        <div className={collapsed ? "flex justify-center" : "rounded-lg bg-white/[0.06] px-3 py-2.5"}>
          <ServerStatus onShell compact={collapsed} />
          {!collapsed && <p className="mt-1 text-xs leading-5 text-shell-muted">Research prototype · synthetic data only</p>}
        </div>
        {onToggle && (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={`flex min-h-10 w-full items-center gap-2 rounded-lg text-sm font-bold text-shell-muted hover:bg-white/[0.07] hover:text-shell-ink ${collapsed ? "justify-center" : "px-3"}`}
          >
            <Icon name={collapsed ? "chevronRight" : "chevronLeft"} size={18} />
            {!collapsed && "Collapse"}
          </button>
        )}
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { me, failed } = useMe();
  const role = me?.role ?? null;
  const [collapsed, setCollapsed] = useState(false);
  const drawer = useRef<HTMLDialogElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);

  useEffect(() => setCollapsed(readCollapsed()), []);
  useEffect(() => drawer.current?.close(), [pathname]);

  function toggle() {
    setCollapsed((c) => {
      try {
        window.localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {
        /* storage unavailable: the choice lasts for this page view only */
      }
      return !c;
    });
  }

  const roleText = me ? (ROLE_LABEL[me.role] ?? me.role) : failed ? "Role unknown" : "Checking role…";
  const initials = me?.role === "medical_officer" ? "MO" : me?.role === "anm" ? "HW" : me?.role === "supervisor" ? "SV" : me?.role === "patient" ? "P" : "?";

  return (
    <div className="min-h-screen lg:flex">
      <aside
        className="app-sidebar sticky top-0 hidden h-screen shrink-0 bg-shell text-shell-ink transition-[width] duration-200 ease-[var(--ease)] motion-reduce:transition-none lg:block"
        style={{ width: collapsed ? "var(--sidebar-w-collapsed)" : "var(--sidebar-w)" }}
      >
        <SidebarBody role={role} pathname={pathname} collapsed={collapsed} onToggle={toggle} />
      </aside>

      <dialog
        ref={drawer}
        aria-label="Navigation"
        onClose={() => menuButton.current?.focus()}
        onClick={(e) => e.target === e.currentTarget && drawer.current?.close()}
        className="app-sidebar fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-72 max-w-[85vw] bg-shell p-0 text-shell-ink backdrop:bg-[rgb(6_30_30/0.55)] lg:hidden"
      >
        <SidebarBody role={role} pathname={pathname} collapsed={false} onNavigate={() => drawer.current?.close()} onClose={() => drawer.current?.close()} />
      </dialog>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="app-topbar sticky top-0 z-30 flex h-[var(--topbar-h)] shrink-0 items-center justify-between border-b border-subtle bg-card/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-card/85 lg:px-6">
          <div className="flex items-center gap-4 flex-1">
            <button
              ref={menuButton}
              type="button"
              onClick={() => drawer.current?.showModal()}
              className="-ml-1 inline-flex size-11 items-center justify-center rounded-lg text-ink hover:bg-surface-2 lg:hidden"
              aria-label="Open navigation"
            >
              <Icon name="menu" size={22} />
            </button>
            <div className="hidden lg:flex items-center gap-3 w-full max-w-lg bg-surface-2 border border-subtle rounded-md px-3 py-1.5 focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary transition-all">
              <Icon name="search" size={18} className="text-muted shrink-0" />
              <input 
                type="text" 
                placeholder="Search patient, case ID or phone number..." 
                className="w-full bg-transparent border-none outline-none text-sm text-ink placeholder-muted"
              />
            </div>
          </div>
          
          <div className="ml-auto flex items-center gap-4 sm:gap-6">
            <button aria-label="Notifications" className="text-muted hover:text-ink transition-colors relative">
              <Icon name="bell" size={20} />
              <span className="absolute top-0.5 right-0.5 w-2 h-2 bg-urg-red-bg rounded-full border border-card"></span>
            </button>
            <span className="hidden h-6 w-px bg-subtle md:block" aria-hidden="true" />
            <div className="flex items-center gap-3">
              <div className="hidden sm:block text-right leading-tight">
                <span className="block text-sm font-bold text-ink">{roleText}</span>
                <span className="block text-xs text-primary font-medium">{me?.role === "medical_officer" ? "Medical Officer" : "Staff"}</span>
              </div>
              {/* Avatar placeholder - in a real app this would be next/image */}
              <div className="h-9 w-9 rounded-full bg-primary-tint flex items-center justify-center overflow-hidden border border-subtle">
                {initials === "MO" ? (
                  <img src="https://i.pravatar.cc/150?u=priya" alt="Dr. Priya Sharma" className="h-full w-full object-cover" />
                ) : (
                  <span className="text-sm font-bold text-primary">{initials}</span>
                )}
              </div>
            </div>
            <LogoutButton />
          </div>
        </header>

        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[var(--content-max)] flex-1 px-4 py-5 outline-none sm:px-5 lg:px-6 lg:py-6">
          {children}
        </main>

        <footer className="border-t border-subtle bg-card px-4 py-3 text-xs leading-5 text-muted lg:px-6">
          Research prototype, not a clinically validated device. Non-diagnostic: urgency comes from fixed rules, a health worker enters and checks every value, and a
          medical officer reviews and signs off each result.
        </footer>
      </div>
    </div>
  );
}
