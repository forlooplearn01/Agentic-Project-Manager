"use client";

export interface TabDef<T extends string = string> {
  id: T;
  label: string;
  // Shown as a small attention pill when > 0 (something in that tab needs a decision).
  badge?: number;
  badgeTitle?: string;
}

export default function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  ariaLabel,
}: {
  tabs: TabDef<T>[];
  active: T;
  onChange: (id: T) => void;
  ariaLabel: string;
}) {
  return (
    <div role="tablist" aria-label={ariaLabel} className="flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={selected}
            aria-controls={`tabpanel-${t.id}`}
            onClick={() => onChange(t.id)}
            className={`-mb-px inline-flex shrink-0 items-center gap-2 border-b-2 px-3.5 py-2 text-[13px] font-medium transition ${
              selected ? "border-accent-to text-text-primary" : "border-transparent text-text-muted hover:text-text-primary"
            }`}
          >
            {t.label}
            {t.badge ? (
              <span
                title={t.badgeTitle}
                className="rounded-full border border-warning-line bg-warning-bg px-1.5 py-0.5 text-[10.5px] font-semibold leading-none text-warning"
              >
                {t.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
