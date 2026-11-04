const STAT_COLOR_VAR: Record<string, string> = {
  indigo: "var(--color-stat-indigo)",
  blue: "var(--color-stat-blue)",
  emerald: "var(--color-stat-emerald)",
  red: "var(--color-stat-red)",
  amber: "var(--color-stat-amber)",
  purple: "var(--color-stat-purple)",
};

export type StatColor = keyof typeof STAT_COLOR_VAR;

export default function StatCard({
  label,
  value,
  color,
  icon,
  detail,
  onClick,
}: {
  label: string;
  value: string | number;
  color: StatColor;
  icon: React.ReactNode;
  detail?: string; // who or where it is, under the label
  onClick?: () => void;
}) {
  const colorVar = STAT_COLOR_VAR[color];

  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      {...(onClick ? { type: "button" as const, onClick } : {})}
      className={`stat-card glass-panel animate-card-in rounded-2xl p-4 ${onClick ? "hover-tint w-full cursor-pointer text-left transition" : ""}`}
      style={{ "--stat-color": colorVar } as React.CSSProperties}
    >
      <div className="relative flex items-center gap-3">
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
          {detail && <div className="mt-0.5 line-clamp-2 text-[11.5px] leading-snug text-text-muted" title={detail}>{detail}</div>}
        </div>
      </div>
    </Tag>
  );
}
