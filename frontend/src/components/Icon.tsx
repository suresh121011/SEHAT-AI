// Inline SVG icons (no icon dependency). 24×24 viewBox, 1.75 stroke, currentColor. Decorative by default
// (aria-hidden); pass `label` when the icon is the only thing conveying meaning.

const PATHS: Record<string, React.ReactNode> = {
  shield: <path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6L12 3Zm-3 9 2.2 2.2L15.5 10" />,
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" />
    </>
  ),
  speaker: <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4v-5Zm11 .2a3.5 3.5 0 0 1 0 4.6m2.6-7.2a7 7 0 0 1 0 9.8" />,
  document: (
    <>
      <path d="M7 3h7l4 4v14H7V3Z" />
      <path d="M14 3v4h4M9.5 12h6M9.5 15.5h6M9.5 9h2.5" />
    </>
  ),
  body: (
    <>
      <circle cx="12" cy="4.5" r="2" />
      <path d="M8 8.5h8l-1 6h-1.5L13 21h-2l-.5-6.5H9l-1-6ZM8 8.5l-2.5 5M16 8.5l2.5 5" />
    </>
  ),
  question: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.6 9.2a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.6M12 16.8v.2" />
    </>
  ),
  clipboard: (
    <>
      <rect x="5" y="4.5" width="14" height="16.5" rx="1.5" />
      <path d="M9 4.5V3h6v1.5M8.5 10h7M8.5 13.5h7M8.5 17h4" />
    </>
  ),
  building: <path d="M4 21V7l8-4 8 4v14M4 21h16M9 21v-5h6v5M8 10h2M14 10h2M8 13h2M14 13h2" />,
  people: (
    <>
      <circle cx="9" cy="7.5" r="3" />
      <path d="M3.5 20c.4-3.4 2.7-5.5 5.5-5.5s5.1 2.1 5.5 5.5" />
      <circle cx="17" cy="9" r="2.3" />
      <path d="M15.6 14.4c2.6.1 4.4 2 4.9 5" />
    </>
  ),
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  cross: <path d="m6.5 6.5 11 11m0-11-11 11" />,
  alert: (
    <>
      <path d="M12 3.5 2.8 19.5h18.4L12 3.5Z" />
      <path d="M12 10v4.5M12 17.2v.3" />
    </>
  ),
  octagon: (
    <>
      <path d="M8.3 3h7.4L21 8.3v7.4L15.7 21H8.3L3 15.7V8.3L8.3 3Z" />
      <path d="M12 7.5v6M12 16.3v.3" />
    </>
  ),
  circle: <circle cx="12" cy="12" r="8.5" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5.5M12 7.8v.3" />
    </>
  ),
  arrowRight: <path d="M4.5 12h15m-6-6 6 6-6 6" />,
  arrowLeft: <path d="M19.5 12h-15m6-6-6 6 6 6" />,
  sparkle: <path d="M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4M7 7l1.8 1.8M15.2 15.2 17 17M17 7l-1.8 1.8M8.8 15.2 7 17" />,
  pencil: <path d="m15 4.5 4.5 4.5L9 19.5H4.5V15L15 4.5Z" />,
  upload: <path d="M12 15V4m-4.5 4.5L12 4l4.5 4.5M4.5 15v4.5h15V15" />,
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="10" rx="1.5" />
      <path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  refresh: <path d="M19.5 6v4.5H15M4.5 18v-4.5H9M18.6 10.5A7 7 0 0 0 6 8M5.4 13.5A7 7 0 0 0 18 16" />,
  eye: (
    <>
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="2.8" />
    </>
  ),
  flag: <path d="M5.5 21V4M5.5 4.5h11l-2.5 4 2.5 4h-11" />,
  history: (
    <>
      <path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5M4 4v4.5h4.5" />
      <path d="M12 8v4l2.5 2" />
    </>
  ),
  scale: <path d="M12 4v16M7 20h10M5 7h14M5 7l-2.5 6a2.5 2.5 0 0 0 5 0L5 7Zm14 0-2.5 6a2.5 2.5 0 0 0 5 0L19 7Z" />,
  chart: <path d="M4 20h16M7 20v-6M12 20V8M17 20v-9" />,
  list: <path d="M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01" />,
  print: <path d="M7 9V3.5h10V9M7 17H4.5V9h15v8H17M7 14h10v6.5H7V14Z" />,
  // Medical image types (MedGemma visual findings).
  xray: (
    <>
      <rect x="3.5" y="3.5" width="17" height="17" rx="1.5" />
      <path d="M12 6v12M8 8.5c1.5 0 2.5.5 4 1.5 1.5-1 2.5-1.5 4-1.5M7.5 11.5c2 0 3 .5 4.5 1.5 1.5-1 2.5-1.5 4.5-1.5M8 14.5c1.5 0 2.5.5 4 1.5 1.5-1 2.5-1.5 4-1.5" />
    </>
  ),
  ecg: <path d="M2.5 12h4l2-5 3 10 2.5-7 1.5 2h6" />,
  scan: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3.5v3M12 17.5v3" />
    </>
  ),
  bandage: (
    <>
      <rect x="2.8" y="8.5" width="18.4" height="7" rx="3.5" transform="rotate(-45 12 12)" />
      <path d="M10.6 10.6h.01M13.4 13.4h.01M13.4 10.6h.01M10.6 13.4h.01" />
    </>
  ),
  skin: (
    <>
      <path d="M3.5 7.5c3-2 5.5-2 8.5 0s5.5 2 8.5 0M3.5 7.5V20h17V7.5" />
      <circle cx="12" cy="14" r="2.5" />
    </>
  ),
  image: (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.5" />
      <circle cx="9" cy="9.5" r="1.8" />
      <path d="m3.5 17 5-4.5 4 3.5 3-2.5 5 4" />
    </>
  ),
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20, label, className = "" }: { name: IconName; size?: number; label?: string; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`inline-block shrink-0 ${className}`}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
