"use client";

import { usePathname, useRouter } from "next/navigation";

import { logout } from "@/lib/api";

export function LogoutButton() {
  const router = useRouter();
  const pathname = usePathname();
  if (pathname === "/login") return null;

  return (
    <button
      type="button"
      className="rounded border border-black/15 px-3 py-1 text-sm hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
      onClick={async () => {
        await logout();
        router.push("/login");
        router.refresh();
      }}
    >
      Log out
    </button>
  );
}
