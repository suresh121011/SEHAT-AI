// Persistent demo-only warning (docs/11): authentication uses shared, password-less demo accounts.
export function DemoBanner() {
  return (
    <div role="note" className="rounded border border-amber-500 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-100">
      <strong>Demo accounts — research prototype, not for real patients.</strong> Accounts are shared and have no passwords;
      do not enter real names, phone numbers or ID numbers.
    </div>
  );
}
