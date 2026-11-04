import { Fragment } from "react";

const STAT_COLOR_VAR: Record<string, string> = {
  indigo: "var(--color-stat-indigo)",
  blue: "var(--color-stat-blue)",
  emerald: "var(--color-stat-emerald)",
  red: "var(--color-stat-red)",
  amber: "var(--color-stat-amber)",
  purple: "var(--color-stat-purple)",
};

export type StatColor = keyof typeof STAT_COLOR_VAR;

export type StatPart = { text: string; onClick?: () => void };

export default function StatCard({
  label,
  value,
  color,
  icon,
  detail,
  parts,
  onClick,
}: {
  label: string;
  value: string | number;
  color: StatColor;
  icon: React.ReactNode;
  detail?: string; // plain text under the label
  parts?: StatPart[]; // who or where it is, each one its own link when it has an onClick
  onClick?: () => void; // the card itself
}) {
  const colorVar = STAT_COLOR_VAR[color];
  const full = parts?.map((p) => p.text).join(" · ") ?? detail;

  return (
    <div
      className={`stat-card glass-panel animate-card-in relative rounded-2xl p-4 ${onClick ? "hover-tint transition" : ""}`}
      style={{ "--stat-color": colorVar } as React.CSSProperties}
    >
      {/* The card is one button behind its content; the project names inside are separate buttons above it (a button cannot hold a button). */}
      {onClick && <button type="button" onClick={onClick} aria-label={label} className="absolute inset-0 cursor-pointer rounded-2xl" />}
      <div className="pointer-events-none relative flex items-center gap-3">
        <div
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
          style={{
            background: `linear-gradient(155deg, color-mix(in oklab, ${colorVar} 30%, transparent), color-mix(in oklab, ${colorVar} 10%, transparent))`,
            color: colorVar,
            boxShadow: `0 0 0 1px color-mix(in oklab, ${colorVar} 25%, transparent) inset`,
          }}
        >
          {icon}
        </div>
        <div className="min-w-0">
          <div className="animate-count text-2xl font-semibold tracking-tight text-text-primary">{value}</div>
          <div className="text-[11px] font-medium uppercase leading-tight tracking-wide text-text-faint">{label}</div>
          {full && (
            <div className="mt-0.5 line-clamp-2 text-[11.5px] leading-snug text-text-muted" title={full}>
              {parts
                ? parts.map((p, i) => (
                    <Fragment key={p.text}>
                      {i > 0 && " · "}
                      {p.onClick ? (
                        <button type="button" onClick={p.onClick} className="pointer-events-auto cursor-pointer rounded-sm hover:text-text-primary hover:underline">
                          {p.text}
                        </button>
                      ) : (
                        p.text
                      )}
                    </Fragment>
                  ))
                : detail}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
