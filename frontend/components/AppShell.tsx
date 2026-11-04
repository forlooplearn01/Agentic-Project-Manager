"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { DEFAULT_DISPLAY_NAME, PREFS_CHANGED_EVENT, getDisplayName } from "@/lib/preferences";
import {
  IconBookOpen,
  IconFileText,
  IconChecklist,
  IconClock,
  IconGauge,
  IconMessageCircle,
  IconPlug,
  IconSearch,
  IconSettings,
  IconTable,
} from "./icons";

type NavItem = { href: string; label: string; icon: React.ReactNode };

const PRODUCT_ITEMS: NavItem[] = [
  { href: "/products/dashboard", label: "Dashboard", icon: <IconGauge className="h-4 w-4" /> },
  { href: "/products/extractor", label: "Extractor", icon: <IconFileText className="h-4 w-4" /> },
  { href: "/products/task-manager", label: "Workspace", icon: <IconChecklist className="h-4 w-4" /> },
  { href: "/products/registers", label: "Registers", icon: <IconTable className="h-4 w-4" /> },
  { href: "/products/hours", label: "Hours", icon: <IconClock className="h-4 w-4" /> },
];

const ACCOUNT_ITEMS: NavItem[] = [
  { href: "/account/settings", label: "Settings", icon: <IconSettings className="h-4 w-4" /> },
  { href: "/account/integrations", label: "Integrations", icon: <IconPlug className="h-4 w-4" /> },
];

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  return (
    <Link
      href={item.href}
      className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition ${
        active
          ? // No hover:* classes at all here, deliberately - the selected tab must look
            // identical whether or not the mouse happens to be over it.
            "bg-gradient-to-br from-accent-from to-accent-to text-white"
          : // A fixed, theme-tuned hover background (not tied to currentColor, unlike
            // the generic .hover-tint) - contrast against the hovered text is then
            // guaranteed regardless of theme, instead of depending on a color-mix of
            // whatever text color happened to be active.
            "text-text-muted hover:bg-line hover:text-text-primary"
      }`}
    >
      {item.icon}
      {item.label}
    </Link>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [query, setQuery] = useState("");
  // Read after mount (localStorage does not exist on the server) and again whenever Settings changes it.
  const [displayName, setDisplayNameState] = useState("");
  useEffect(() => {
    const read = () => setDisplayNameState(getDisplayName());
    read();
    window.addEventListener(PREFS_CHANGED_EVENT, read);
    return () => window.removeEventListener(PREFS_CHANGED_EVENT, read);
  }, []);

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    router.push(`/search?q=${encodeURIComponent(query.trim())}`);
  }

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-52 shrink-0 flex-col gap-5 self-start border-r border-line bg-surface-solid/60 px-3 py-5">
        <Link href="/products/dashboard" className="flex items-center gap-2.5 px-1.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-accent-from to-accent-to text-sm font-bold text-white shadow-[0_4px_16px_-4px_var(--color-accent-to)]">
            {(displayName || DEFAULT_DISPLAY_NAME).charAt(0).toUpperCase()}
          </div>
          <span className="min-w-0 truncate text-[13.5px] font-semibold tracking-tight text-text-primary">{displayName || DEFAULT_DISPLAY_NAME}</span>
        </Link>

        <form onSubmit={handleSearchSubmit} className="px-1.5">
          <div className="flex items-center gap-2 rounded-lg border border-line bg-surface-solid px-2.5 py-1.5 focus-within:border-accent-to">
            <IconSearch className="h-3.5 w-3.5 shrink-0 text-text-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search"
              className="w-full bg-transparent text-[13px] text-text-primary outline-none placeholder:text-text-faint"
            />
          </div>
        </form>

        <nav className="flex flex-1 flex-col gap-4 overflow-y-auto">
          <div className="flex flex-col gap-0.5">
            <NavLink item={{ href: "/chat", label: "AI Chat", icon: <IconMessageCircle className="h-4 w-4" /> }} active={pathname === "/chat"} />
          </div>

          <div>
            <p className="mb-1 px-3 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Products</p>
            <div className="flex flex-col gap-0.5">
              {PRODUCT_ITEMS.map((item) => (
                <NavLink key={item.href} item={item} active={pathname.startsWith(item.href)} />
              ))}
            </div>
          </div>

          <div>
            <p className="mb-1 px-3 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Account</p>
            <div className="flex flex-col gap-0.5">
              {ACCOUNT_ITEMS.map((item) => (
                <NavLink key={item.href} item={item} active={pathname.startsWith(item.href)} />
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-0.5">
            <p className="mb-1 px-3 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">Resources</p>
            <NavLink item={{ href: "/resources", label: "Guide", icon: <IconBookOpen className="h-4 w-4" /> }} active={pathname.startsWith("/resources")} />
          </div>
        </nav>
      </aside>

      <div className="min-w-0 flex-1">
        <div className="flex h-11 items-center justify-end px-6">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/forloop-logo.png" alt="Forloop" width={640} height={96} className="h-auto w-32" />
        </div>
        {children}
      </div>
    </div>
  );
}
