import { Icon } from "@/components/Icon";

// Persistent demo-only warning (docs/11): authentication uses shared, password-less demo accounts.
export function DemoBanner() {
  return (
    <div role="note" className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning-bg px-3.5 py-2.5 text-sm text-warning">
      <Icon name="alert" size={18} className="mt-0.5" />
      <p className="text-ink">
        <strong className="text-warning">Research prototype with demo accounts: not for real patients.</strong> Do not enter real names, phone numbers or ID
        numbers.
      </p>
    </div>
  );
}
