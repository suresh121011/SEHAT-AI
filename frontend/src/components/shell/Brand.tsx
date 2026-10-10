// SEHAT AI wordmark. The cross mark sits on a light tile so it reads on both the dark sidebar and white surfaces.
export function BrandMark({ size = 32, onShell = false }: { size?: number; onShell?: boolean }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" focusable="false" className="shrink-0">
      <rect x="1" y="1" width="30" height="30" rx="8" fill={onShell ? "#ffffff" : "var(--primary)"} />
      <path d="M13 8h6v5h5v6h-5v5h-6v-5H8v-6h5V8Z" fill={onShell ? "var(--shell-bg)" : "#ffffff"} />
    </svg>
  );
}

export function Wordmark({ onShell = false, collapsed = false, subtitle }: { onShell?: boolean; collapsed?: boolean; subtitle?: string }) {
  return (
    <span className="flex min-w-0 items-center gap-2.5">
      <BrandMark onShell={onShell} />
      <span className={collapsed ? "sr-only" : "min-w-0 leading-tight"}>
        <span className={`block text-lg font-bold tracking-tight ${onShell ? "text-shell-ink" : "text-ink"}`}>
          SEHAT <span className={onShell ? "text-[#9fe0d6]" : "text-primary"}>AI</span>
        </span>
        {subtitle && <span className={`block truncate text-xs ${onShell ? "text-shell-muted" : "text-muted"}`}>{subtitle}</span>}
      </span>
    </span>
  );
}
