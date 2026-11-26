"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { shouldRetry } from "@/lib/api/client";
import { useUiStore } from "@/lib/store";
import { HScroll } from "@/components/HScroll";
import { ThemeToggle } from "@/components/ThemeToggle";

// Order follows the user journey: ask, bring in data, keep answers, watch spend, configure.
const NAV = [
  { href: "/ask", label: "Ask", icon: "chat" },
  { href: "/data", label: "Data", icon: "db" },
  { href: "/dashboard", label: "Dashboard", icon: "grid" },
  { href: "/usage", label: "Usage", icon: "gauge" },
  { href: "/settings", label: "Settings", icon: "sliders" },
] as const;

const COLLAPSE_KEY = "ledgerline.sidebarCollapsed";

function NavIcon({ name, className = "h-[18px] w-[18px]" }: { name: string; className?: string }) {
  const props = {
    className: `${className} shrink-0`,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "grid":
      return (
        <svg {...props}>
          <rect x="3" y="3" width="7" height="7" rx="1.5" />
          <rect x="14" y="3" width="7" height="7" rx="1.5" />
          <rect x="3" y="14" width="7" height="7" rx="1.5" />
          <rect x="14" y="14" width="7" height="7" rx="1.5" />
        </svg>
      );
    case "chat":
      return (
        <svg {...props}>
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      );
    case "db":
      return (
        <svg {...props}>
          <ellipse cx="12" cy="5" rx="9" ry="3" />
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
          <path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3" />
        </svg>
      );
    case "gauge":
      return (
        <svg {...props}>
          <path d="m12 14 4-4" />
          <path d="M3.34 19a10 10 0 1 1 17.32 0" />
        </svg>
      );
    default:
      return (
        <svg {...props}>
          <path d="M4 9h16M4 15h16" />
          <circle cx="8" cy="9" r="2" />
          <circle cx="16" cy="15" r="2" />
        </svg>
      );
  }
}

function BrandMark({ size = "h-8 w-8 text-[17px]" }: { size?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`${size} flex shrink-0 items-center justify-center bg-plum font-display font-bold leading-none text-on-accent`}
    >
      L
    </div>
  );
}

function RailTip({ show, children }: { show: boolean; children: React.ReactNode }) {
  if (!show) return null;
  return (
    <span
      role="tooltip"
      className="pointer-events-none absolute left-full top-1/2 z-50 ml-3 -translate-y-1/2 whitespace-nowrap bg-ink px-2.5 py-1.5 text-xs font-medium text-canvas opacity-0 shadow-pop transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100"
    >
      {children}
    </span>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          // Retry network and server errors only; a 404 or 409 won't change on retry.
          queries: { retry: shouldRetry, staleTime: 15_000 },
          mutations: { retry: false },
        },
      })
  );
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUiStore((s) => s.setSidebarCollapsed);
  const [animate, setAnimate] = useState(false);
  const [isMac, setIsMac] = useState(false);
  const isActive = (href: string) => pathname === href || Boolean(pathname?.startsWith(`${href}/`));

  useEffect(() => {
    setIsMac(/Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent));
    let saved: string | null = null;
    try {
      saved = window.localStorage.getItem(COLLAPSE_KEY);
    } catch {}
    setSidebarCollapsed(saved === null ? window.innerWidth < 1100 : saved === "1");
    const id = window.requestAnimationFrame(() => setAnimate(true));
    return () => window.cancelAnimationFrame(id);
  }, [setSidebarCollapsed]);

  const toggle = () => {
    const next = !useUiStore.getState().sidebarCollapsed;
    setSidebarCollapsed(next);
    try {
      window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
    } catch {}
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "b" || e.shiftKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
      e.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <a
        href="#main"
        className="sr-only z-[60] bg-ink px-4 py-2 text-[13px] font-medium text-canvas focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>
      <div className="flex min-h-screen flex-col bg-canvas text-ink md:flex-row">
        <header className="sticky top-0 z-30 border-b border-border bg-canvas/95 px-4 pb-2.5 pt-3 backdrop-blur-md md:hidden">
          <div className="flex items-center gap-2.5">
            <BrandMark size="h-7 w-7 text-[15px]" />
            <span className="flex-1 font-display text-[17px] font-bold tracking-[-0.01em]">Ledgerline</span>
            <ThemeToggle compact />
          </div>
          <HScroll className="-mx-1 mt-2.5" fadeFrom="from-canvas">
          <nav aria-label="Primary Navigation" className="flex w-max gap-1 px-1 pb-0.5">
            {NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`inline-flex min-h-[40px] shrink-0 items-center px-3.5 text-[13px] transition-colors ${
                    active ? "bg-ink font-medium text-canvas" : "text-body hover:bg-sidebar"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          </HScroll>
        </header>

        <aside
          aria-label="Workspace Navigation"
          className={`${
            collapsed ? "md:w-[68px]" : "md:w-[248px]"
          } sticky top-0 z-20 hidden h-screen shrink-0 flex-col border-r border-border bg-sidebar py-4 md:flex ${
            animate ? "transition-[width] duration-200 ease-out" : ""
          } ${
            collapsed ? "px-2.5" : "px-3.5"
          }`}
        >
          <Link
            href="/ask"
            aria-label="Ledgerline home"
            className={`group relative mb-6 flex items-center gap-2.5 ${collapsed ? "justify-center py-1" : "px-1.5 py-1"}`}
          >
            <BrandMark />
            {!collapsed && (
              <span className="min-w-0">
                <span className="block font-display text-[18px] font-bold leading-none tracking-[-0.01em]">Ledgerline</span>
                <span className="mt-1 block text-[11px] text-secondary">Local analytics</span>
              </span>
            )}
          </Link>

          <nav aria-label="Primary Navigation" className="flex flex-col gap-1">
            {NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-label={collapsed ? item.label : undefined}
                  aria-current={active ? "page" : undefined}
                  className={`group relative flex min-h-[42px] items-center gap-3 border text-[13.5px] transition-colors focus:outline-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong ${
                    collapsed ? "justify-center" : "px-2.5"
                  } ${
                    active
                      ? "border-border bg-surface font-medium text-ink"
                      : "border-transparent text-body hover:bg-surface/70 hover:text-ink"
                  }`}
                >
                  <span className={active ? "text-plum" : ""}>
                    <NavIcon name={item.icon} />
                  </span>
                  {!collapsed && <span className="truncate">{item.label}</span>}
                  <RailTip show={collapsed}>{item.label}</RailTip>
                </Link>
              );
            })}
          </nav>

          <div className="mt-auto space-y-2">
            {collapsed ? (
              <div className="flex justify-center">
                <ThemeToggle compact />
              </div>
            ) : (
              <ThemeToggle />
            )}

            <button
              type="button"
              onClick={toggle}
              aria-expanded={!collapsed}
              aria-label={collapsed ? `Expand sidebar (${isMac ? "⌘ B" : "Ctrl+B"})` : `Collapse sidebar (${isMac ? "⌘ B" : "Ctrl+B"})`}
              className={`group relative flex min-h-[40px] w-full items-center gap-2.5 text-[13px] text-secondary transition-colors hover:bg-surface hover:text-ink ${
                collapsed ? "justify-center" : "px-2.5"
              }`}
            >
              <svg
                className={`h-[18px] w-[18px] shrink-0 transition-transform duration-200 ${collapsed ? "rotate-180" : ""}`}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <rect x="3" y="4" width="18" height="16" rx="2.5" />
                <path d="M9 4v16M15.5 10 13.5 12l2 2" />
              </svg>
              {!collapsed && (
                <>
                  <span className="flex-1 text-left">Collapse</span>
                  <kbd className="border border-border bg-surface px-1.5 py-0.5 font-sans text-[10.5px] text-secondary">
                    {isMac ? "⌘ B" : "Ctrl B"}
                  </kbd>
                </>
              )}
              <RailTip show={collapsed}>Expand · {isMac ? "⌘ B" : "Ctrl B"}</RailTip>
            </button>
          </div>
        </aside>

        <main id="main" tabIndex={-1} className="min-w-0 flex-1 px-4 py-6 focus:outline-none sm:px-5 md:px-6 md:py-8 lg:px-8">
          <div className="mx-auto w-full max-w-[1480px]">{children}</div>
        </main>
      </div>
    </QueryClientProvider>
  );
}
