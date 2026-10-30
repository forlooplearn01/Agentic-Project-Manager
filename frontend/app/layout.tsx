import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import AppShell from "@/components/AppShell";
import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const jetbrainsMono = JetBrains_Mono({ variable: "--font-jetbrains-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Agentic Project Manager",
  description: "Autonomous agents plan and update your project - an SOW creates the plan, meeting minutes update it.",
};

// Sets data-theme (and data-density, for compact tables) on <html> before first paint (stored choice, else system
// preference), so the page never flashes the wrong theme. Runs as a plain inline
// script rather than next/script so it's synchronous and blocks paint until done.
// suppressHydrationWarning on <html> is required because of this - the attribute
// it sets is real, expected client/server output divergence, not a bug.
const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem('pref-theme');if(t!=='light'&&t!=='dark'){t=window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';}document.documentElement.setAttribute('data-theme',t);if(localStorage.getItem('pref-density')==='compact'){document.documentElement.setAttribute('data-density','compact');}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}>
      <body className="min-h-full">
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        <div className="ambient-glow" />
        <div className="grain-overlay" />
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
