// Loading, error, permission, empty and stale states shared by the reviewer screens. An error never clears data
// already on screen: the last good data stays, marked stale, so a RED case cannot vanish because one refresh failed.
// Permission and session problems are not shown in red: red is reserved for urgency and danger.
import Link from "next/link";

import { Icon, type IconName } from "@/components/Icon";
import { Button, Spinner } from "@/components/ui";

export function LoadingState({ label }: { label: string }) {
  return (
    <div role="status" className="flex items-center justify-center p-6">
      <Spinner label={label} />
    </div>
  );
}

/** Skeleton rows for the queue's first load (Carbon data-table guidance: skeletons, not a spinner, for tables).
 * Static under reduced motion. The visible label keeps it from being a silent grey block. */
export function QueueSkeleton({ rows = 5, label }: { rows?: number; label: string }) {
  return (
    <div role="status" className="space-y-0 divide-y divide-subtle rounded-lg border border-subtle bg-card">
      <p className="px-3 py-2 text-sm text-muted">{label}</p>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} aria-hidden="true" className="space-y-2 px-3 py-3">
          <div className="flex items-center gap-2">
            <span className="h-5 w-14 animate-pulse rounded bg-skeleton motion-reduce:animate-none" />
            <span className="h-4 w-24 animate-pulse rounded bg-skeleton motion-reduce:animate-none" />
          </div>
          <span className="block h-3 w-3/4 animate-pulse rounded bg-skeleton motion-reduce:animate-none" />
        </div>
      ))}
    </div>
  );
}

/** `status` 403 → permission denied (lock, neutral); 401 → session ended (sign-in link); otherwise an error. */
export function ErrorState({ title, message, onRetry, status = null }: { title: string; message: string; onRetry?: () => void; status?: number | null }) {
  if (status === 403 || status === 401) {
    const session = status === 401;
    return (
      <div role="alert" className="space-y-2 rounded-lg border border-esc-ack-line/50 bg-esc-ack p-4 text-esc-ack-ink">
        <p className="flex items-center gap-2 font-bold">
          <Icon name="lock" size={20} />
          {session ? "Your session has ended" : "This account cannot open this view"}
        </p>
        <p className="text-sm">
          {session
            ? "Nothing was changed. Sign in again to continue."
            : "Access is decided by the server for each account. Nothing was changed. Use an account with the right role, or go back."}
        </p>
        {session && (
          <Link href="/login" className="inline-flex min-h-10 items-center gap-1 font-bold text-primary underline underline-offset-4">
            <Icon name="arrowRight" size={16} /> Sign in again
          </Link>
        )}
      </div>
    );
  }
  return (
    <div role="alert" className="space-y-2 rounded-lg border border-error/40 bg-error-bg p-4">
      <p className="flex items-center gap-2 font-bold text-error">
        <Icon name="alert" size={20} />
        {title}
      </p>
      <p className="text-sm text-ink">{message} Nothing on this screen was changed.</p>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry} className="min-h-10 px-3 py-1 text-sm">
          <Icon name="refresh" size={16} /> Try again
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ title, icon = "info", children }: { title: string; icon?: IconName; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line p-5 text-center">
      <p className="flex items-center justify-center gap-2 font-bold">
        <Icon name={icon} size={16} className="text-muted" />
        {title}
      </p>
      {children && <div className="mx-auto mt-1 max-w-prose text-sm text-muted">{children}</div>}
    </div>
  );
}

export function StaleNotice({ error, onRetry, updatedAt }: { error: string | null; onRetry: () => void; updatedAt: string | null }) {
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 rounded border border-warning/50 bg-warning-bg px-3 py-2 text-sm">
      <Icon name="clock" size={16} className="text-warning" />
      <span className="text-ink">
        <strong>Data may be out of date.</strong> {error ?? "The last refresh is older than expected."}
        {updatedAt && <span className="tabular-nums"> Last server update: {new Date(updatedAt).toLocaleTimeString()}.</span>}
      </span>
      <button type="button" onClick={onRetry} className="ml-auto inline-flex min-h-10 items-center gap-1 rounded px-2 font-bold text-primary underline underline-offset-4">
        <Icon name="refresh" size={16} /> Refresh now
      </button>
    </div>
  );
}
