"use client";

import React, { useEffect, useRef, useState } from "react";
import { Icon, IconName } from "@/components/Icon";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { shouldRetry } from "@/lib/api/client";
import { useUiStore } from "@/lib/store";
import { HScroll } from "@/components/HScroll";
import { LogoMark } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher";
import { Toaster } from "@/components/Toaster";
import { Notice, Spinner, btn } from "@/components/ui";
import { useHealth, useReady, useServerDown } from "@/lib/workspace";

// Order follows the user journey: ask, bring in data, keep answers, watch spend, configure.
const NAV = [
  { href: "/ask", label: "Ask", icon: "chat" },
  { href: "/data", label: "Data", icon: "db" },
  { href: "/dashboard", label: "Dashboard", icon: "grid" },
  { href: "/usage", label: "Usage", icon: "gauge" },
  { href: "/settings", label: "Settings", icon: "sliders" },
] as const;

const COLLAPSE_KEY = "ledgerline.sidebarCollapsed";

const NAV_ICONS: Record<string, IconName> = { chat: "chat", db: "database", grid: "grid", gauge: "gauge", sliders: "sliders" };

function NavIcon({ name, className = "h-4 w-4" }: { name: string; className?: string }) {
  return <Icon name={NAV_ICONS[name] ?? "sliders"} className={className} />;
}

function RailTip({ show, children }: { show: boolean; children: React.ReactNode }) {
  if (!show) return null;
  return (
    <span
      role="tooltip"
      className="pointer-events-none absolute left-full top-1/2 z-50 ml-3 -translate-y-1/2 whitespace-nowrap rounded-md bg-ink px-2.5 py-1.5 text-xs font-medium text-canvas opacity-0 shadow-pop transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100"
    >
      {children}
    </span>
  );
}

/** One explanation for the whole app when the API is unreachable, instead of an error per request. */
function ServerDownNotice() {
  const health = useHealth();
  const ready = useReady();
  const qc = useQueryClient();
  const down = useServerDown();
  const wasDown = useRef(false);

  useEffect(() => {
    if (wasDown.current && !down) void qc.invalidateQueries();
    wasDown.current = down;
  }, [down, qc]);

  if (!down) return null;
  const checking = health.isFetching || ready.isFetching;
  return (
    <Notice
      tone="err"
      title="Can't reach the Ledgerline server"
      className="mb-6"
      action={
        <button type="button" className={btn.secondary} disabled={checking}
          onClick={() => {
            void health.refetch();
            void ready.refetch();
          }}
        >
          {checking && <Spinner />}
          Try again
        </button>
      }
    >
      Your data and answers will appear once the server is running again. This page checks every few seconds.
    </Notice>
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
    setSidebarCollapsed(saved === null ? window.innerWidth < 900 : saved === "1");
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
        className="sr-only z-[60] rounded-full bg-ink px-4 py-2 text-sm font-medium text-canvas focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>
      <div className="flex min-h-screen flex-col bg-canvas text-ink md:flex-row">
        <header className="sticky top-0 z-30 border-b border-border bg-canvas/95 px-4 pb-2.5 pt-3 backdrop-blur-md md:hidden">
          <div className="flex items-center gap-2">
            <LogoMark className="h-7 w-7 shrink-0" />
            <span className="min-w-0 truncate font-display text-[20px] font-semibold tracking-[-0.01em]">Ledgerline</span>
            <div className="ml-auto flex items-center gap-2">
              <div className="max-w-[164px]">
                <WorkspaceSwitcher collapsed={false} />
              </div>
              <ThemeToggle compact />
            </div>
          </div>
          <HScroll className="-mx-1 mt-2.5 hidden sm:block" fadeFrom="from-canvas">
          <nav aria-label="Primary Navigation" className="flex w-max gap-1 px-1 pb-0.5">
            {NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`inline-flex min-h-[32px] shrink-0 items-center rounded-md px-3 text-base transition-colors ${
                    active ? "bg-ink/[0.05] font-medium text-ink dark:bg-ink/[0.15]" : "text-ink hover:bg-ink/[0.05]"
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
            collapsed ? "md:w-[52px]" : "md:w-[216px]"
          } sticky top-0 z-20 hidden h-screen shrink-0 flex-col border-r border-border bg-sidebar py-4 md:flex ${
            animate ? "transition-[width] duration-200 ease-out" : ""
          } ${
            collapsed ? "px-2" : "px-2"
          }`}
        >
          <Link
            href="/ask"
            aria-label="Ledgerline home"
            className={`group relative mb-3 flex h-10 items-center gap-2 ${collapsed ? "justify-center" : "px-2"}`}
          >
            <LogoMark className="h-6 w-6" />
            {!collapsed && (
              <span className="min-w-0">
                <span className="block font-display text-[19px] font-medium leading-none tracking-[-0.01em]">Ledgerline</span>
              </span>
            )}
          </Link>

          <div className="mb-3">
            <WorkspaceSwitcher collapsed={collapsed} />
          </div>

          <nav aria-label="Primary Navigation" className="flex flex-col gap-0.5">
            {NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-label={collapsed ? item.label : undefined}
                  aria-current={active ? "page" : undefined}
                  className={`group relative flex h-8 items-center gap-2.5 rounded-md text-base font-medium transition-colors ${
                    collapsed ? "justify-center" : "px-2"
                  } ${
                    active ? "bg-ink/[0.05] text-ink dark:bg-ink/[0.15]" : "text-ink hover:bg-ink/[0.05] dark:hover:bg-ink/[0.08]"
                  }`}
                >
                  <span className="text-ink">
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
              className={`group relative flex h-8 w-full items-center gap-2.5 rounded-md text-base text-secondary transition-colors hover:bg-ink/[0.05] hover:text-ink dark:hover:bg-ink/[0.08] ${
                collapsed ? "justify-center" : "px-2"
              }`}
            >
              <Icon name="panel-left" className={`h-4 w-4 shrink-0 transition-transform duration-200 ${collapsed ? "rotate-180" : ""}`} />
              {!collapsed && (
                <>
                  <span className="flex-1 text-left">Collapse</span>
                  <kbd className="rounded-sm border border-border bg-canvas px-1.5 py-0.5 font-sans text-2xs text-secondary">
                    {isMac ? "⌘ B" : "Ctrl B"}
                  </kbd>
                </>
              )}
              <RailTip show={collapsed}>Expand · {isMac ? "⌘ B" : "Ctrl B"}</RailTip>
            </button>
          </div>
        </aside>

        <main
          id="main"
          tabIndex={-1}
          className="min-w-0 flex-1 px-4 pb-[calc(5.5rem+env(safe-area-inset-bottom))] pt-6 focus:outline-none sm:px-5 sm:pb-6 md:px-6 md:py-8 lg:px-8"
        >
          <div className="mx-auto w-full max-w-[1480px]">
            <ServerDownNotice />
            {children}
          </div>
        </main>

        <nav
          aria-label="Primary Navigation"
          className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-border bg-canvas/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md sm:hidden"
        >
          {NAV.map((item) => {
            const active = isActive(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`flex min-h-[56px] flex-col items-center justify-center gap-1 text-2xs transition-colors ${
                  active ? "font-semibold text-ink" : "text-secondary hover:text-ink"
                }`}
              >
                <span className={`flex h-6 w-10 items-center justify-center rounded-full ${active ? "bg-ink/[0.08] text-ink" : ""}`}>
                  <NavIcon name={item.icon} className="h-[17px] w-[17px]" />
                </span>
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <Toaster />
    </QueryClientProvider>
  );
}
