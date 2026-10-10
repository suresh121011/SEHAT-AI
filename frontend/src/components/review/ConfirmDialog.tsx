"use client";

// Native modal <dialog> (WAI-ARIA APG modal dialog pattern): showModal() traps focus and makes the page inert;
// Escape closes; focus returns to the element that opened it. Focus starts on Cancel, the least destructive action.
import { useEffect, useRef, type ReactNode } from "react";

export function ConfirmDialog({ open, title, onClose, children, footer, labelledBy }: { open: boolean; title: ReactNode; onClose: () => void; children: ReactNode; footer: ReactNode; labelledBy: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      opener.current = document.activeElement;
      d.showModal();
      d.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    } else if (!open && d.open) {
      d.close();
    }
  }, [open]);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    const onDialogClose = () => {
      onClose();
      if (opener.current instanceof HTMLElement && opener.current.isConnected) opener.current.focus();
    };
    d.addEventListener("close", onDialogClose);
    return () => d.removeEventListener("close", onDialogClose);
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={labelledBy}
      className="m-auto w-[min(40rem,calc(100vw-2rem))] rounded-[16px] border border-white/60 bg-card p-0 text-ink shadow-[8px_8px_20px_rgba(0,0,0,0.05),-8px_-8px_20px_rgba(255,255,255,0.8)] backdrop:bg-page/40 backdrop:backdrop-blur-sm"
    >
      <div className="space-y-4 p-5">
        <h2 id={labelledBy} className="text-xl font-bold">
          {title}
        </h2>
        {children}
      </div>
      <div className="flex flex-wrap justify-end gap-2 border-t border-white/40 bg-surface-1 px-5 py-3 rounded-b-[16px]">{footer}</div>
    </dialog>
  );
}
