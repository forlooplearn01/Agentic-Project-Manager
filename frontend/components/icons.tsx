// Minimal hand-rolled icon set - kept tiny and dependency-free rather than pulling
// in a full icon library for a handful of glyphs.

type IconProps = { className?: string };

export function IconFolder({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" className={className}>
      <path d="M2 5a1 1 0 011-1h4.5l1.5 1.5H17a1 1 0 011 1V15a1 1 0 01-1 1H3a1 1 0 01-1-1V5z" />
    </svg>
  );
}

export function IconChecklist({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" className={className}>
      <path d="M4 6h1M4 10h1M4 14h1" />
      <path d="M8 6h8M8 10h8M8 14h5" />
    </svg>
  );
}

export function IconAlertTriangle({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" className={className}>
      <path d="M10 3.5l7.5 13h-15l7.5-13z" />
      <path d="M10 8.5v3.2" strokeLinecap="round" />
      <circle cx="10" cy="14" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconCheckCircle({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="10" cy="10" r="7.2" />
      <path d="M7 10.3l2 2 4-4.4" />
    </svg>
  );
}

export function IconFlame({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" className={className}>
      <path d="M10 2c1 2.5-2.5 3.5-2.5 6.5a2.5 2.5 0 005 0c0-1-1-1.5-1-2.8 1.8 1 3 3 3 5.3a4.5 4.5 0 01-9 0C5.5 7.5 8.5 6 10 2z" />
    </svg>
  );
}

export function IconUsers({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" className={className}>
      <path d="M7 9a3 3 0 100-6 3 3 0 000 6zm-6 7a6 6 0 0112 0H1zM14.5 9.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM13 11.2a5.5 5.5 0 014.9 4.8h-3.4a6.9 6.9 0 00-1.5-4.8z" />
    </svg>
  );
}

export function IconClock({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="10" cy="10" r="7.2" />
      <path d="M10 6v4l2.6 2" />
    </svg>
  );
}

export function IconLink({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" className={className}>
      <path d="M8 12l4-4M7 13a3 3 0 010-4.2l1.5-1.5a3 3 0 014.2 0M13 7a3 3 0 010 4.2l-1.5 1.5a3 3 0 01-4.2 0" />
    </svg>
  );
}

export function IconDownload({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M10 3v9m0 0l-3.2-3.2M10 12l3.2-3.2" />
      <path d="M4 14v1.5a1.5 1.5 0 001.5 1.5h9a1.5 1.5 0 001.5-1.5V14" />
    </svg>
  );
}

export function IconSearch({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" className={className}>
      <circle cx="9" cy="9" r="5.5" />
      <path d="M17 17l-3.8-3.8" />
    </svg>
  );
}

export function IconMessageCircle({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M17 10.2c0 3.4-3.1 6.1-7 6.1-1 0-1.9-.15-2.7-.43L3 17l1.2-3.1A5.8 5.8 0 013 10.2C3 6.8 6.1 4 10 4s7 2.8 7 6.2z" />
    </svg>
  );
}

export function IconLayers({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M10 3l7 3.5L10 10 3 6.5 10 3z" />
      <path d="M3 10.5L10 14l7-3.5" />
      <path d="M3 14.3L10 17.8l7-3.5" />
    </svg>
  );
}

export function IconSettings({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="10" cy="10" r="2.6" />
      <path d="M10 2.7v1.9M10 15.4v1.9M17.3 10h-1.9M4.6 10H2.7M15.1 4.9l-1.35 1.35M6.25 13.75L4.9 15.1M15.1 15.1l-1.35-1.35M6.25 6.25L4.9 4.9" />
    </svg>
  );
}

export function IconPlug({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M7 3v3.5M13 3v3.5" />
      <path d="M5.5 6.5h9v2.3a4.5 4.5 0 01-4.5 4.5v0a4.5 4.5 0 01-4.5-4.5V6.5z" />
      <path d="M10 13.3V17" />
    </svg>
  );
}

export function IconBookOpen({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M10 5.8c-1.1-1-2.9-1.5-5-1.5v9.8c2.1 0 3.9.5 5 1.5 1.1-1 2.9-1.5 5-1.5V4.3c-2.1 0-3.9.5-5 1.5z" />
      <path d="M10 5.8v9.8" />
    </svg>
  );
}

export function IconGauge({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M3.5 14.5a6.5 6.5 0 1113 0" />
      <path d="M10 14.5l3-4.3" />
      <circle cx="10" cy="14.5" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconFileText({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M6 2.8h5.5L15 6.3V17a.7.7 0 01-.7.7H6a.7.7 0 01-.7-.7V3.5a.7.7 0 01.7-.7z" />
      <path d="M11.3 2.8V6h3.4M7.5 10h5M7.5 12.6h5M7.5 15.2h3" />
    </svg>
  );
}

export function IconCalendar({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="3" y="4.5" width="14" height="12.5" rx="1.5" />
      <path d="M3 8.3h14M6.5 2.8v3M13.5 2.8v3" />
    </svg>
  );
}

export function IconTrash({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M4 6h12M8 6V4.5a1 1 0 011-1h2a1 1 0 011 1V6M8.5 9.5v5M11.5 9.5v5" />
      <path d="M5.5 6l.6 9.5a1.5 1.5 0 001.5 1.4h4.8a1.5 1.5 0 001.5-1.4l.6-9.5" />
    </svg>
  );
}

export function IconPlus({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" className={className}>
      <path d="M10 4.5v11M4.5 10h11" />
    </svg>
  );
}

export function IconX({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" className={className}>
      <path d="M5 5l10 10M15 5L5 15" />
    </svg>
  );
}

export function IconInbox({ className }: IconProps) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d="M3 11l2-6.5A1 1 0 016 4h8a1 1 0 01.95.7L17 11" />
      <path d="M3 11h4.2a1 1 0 01.9.55v0a2 2 0 001.8 1.1h.2a2 2 0 001.8-1.1v0a1 1 0 01.9-.55H17v4a1 1 0 01-1 1H4a1 1 0 01-1-1v-4z" />
    </svg>
  );
}
