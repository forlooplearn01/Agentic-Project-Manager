import { getDateFormat, type DateFormat } from "./preferences";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n: number) => String(n).padStart(2, "0");

// A plain "2026-10-29" is a calendar day, not a moment: build it in local time so it never shows as the day before or after.
function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDateWith(format: DateFormat, d: Date): string {
  switch (format) {
    case "dmy":
      return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
    case "mdy":
      return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}/${d.getFullYear()}`;
    case "ymd":
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    case "text":
      return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
    default:
      return d.toLocaleDateString();
  }
}

// Every date the app SHOWS goes through these two, so the Settings choice applies everywhere.
export function formatDate(value: string | Date | null | undefined): string {
  const d = toDate(value);
  return d ? formatDateWith(getDateFormat(), d) : "";
}

export function formatDateTime(value: string | Date | null | undefined): string {
  const d = toDate(value);
  return d ? `${formatDateWith(getDateFormat(), d)}, ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : "";
}

// The first-letter-of-the-day name, used by meeting times ("Mon 29 Oct 2026" style).
export function formatWeekday(value: string | Date): string {
  const d = toDate(value);
  return d ? d.toLocaleDateString(undefined, { weekday: "short" }) : "";
}
