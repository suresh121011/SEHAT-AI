"use client";

import { usePathname, useRouter } from "next/navigation";

import { logout } from "@/lib/api";
import { clearAllLocalNotes } from "@/lib/intakeStore";

export function LogoutButton() {
  const router = useRouter();
  const pathname = usePathname();
  if (pathname === "/login") return null;

  return (
    <button
      type="button"
      className="min-h-11 rounded border border-line px-4 text-sm font-bold text-ink hover:bg-primary-tint"
      onClick={async () => {
        clearAllLocalNotes(); // body-map and follow-up notes never outlive the session on a shared device
        await logout();
        router.push("/login");
        router.refresh();
      }}
    >
      Log out
    </button>
  );
}
