"use client";

import { usePathname, useRouter } from "next/navigation";

import { Icon } from "@/components/Icon";
import { logout } from "@/lib/api";
import { clearAllLocalNotes } from "@/lib/intakeStore";

export function LogoutButton({ className = "" }: { className?: string }) {
  const router = useRouter();
  const pathname = usePathname();
  if (pathname === "/login") return null;

  return (
    <button
      type="button"
      className={`inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-subtle bg-card px-3 text-sm font-bold text-ink transition-colors duration-150 hover:border-line hover:bg-surface-2 ${className}`}
      onClick={async () => {
        clearAllLocalNotes(); // body-map and follow-up notes never outlive the session on a shared device
        await logout();
        router.push("/login");
        router.refresh();
      }}
    >
      <Icon name="logout" size={16} />
      <span className="max-sm:sr-only">Log out</span>
    </button>
  );
}
