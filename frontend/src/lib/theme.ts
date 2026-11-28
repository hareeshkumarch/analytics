"use client";

import { useCallback, useEffect, useLayoutEffect, useState } from "react";

const useIsomorphicLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

export type ThemePreference = "system" | "light" | "dark";

export const THEME_KEY = "ledgerline.theme";

export const THEME_INIT_SCRIPT = `(function(){var t=null;try{t=localStorage.getItem("${THEME_KEY}");}catch(e){}if(t!=="light"&&t!=="dark"){t=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}document.documentElement.setAttribute("data-theme",t);})();`;

function readPreference(): ThemePreference {
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {}
  return "system";
}

function applyPreference(pref: ThemePreference): void {
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const next = pref === "system" ? (systemDark ? "dark" : "light") : pref;
  if (document.documentElement.getAttribute("data-theme") === next) return;
  // Switch every colour at once: without this, controls with colour transitions fade for 150ms while the rest of
  // the page has already changed. Transitions come back two frames later, once the new colours are painted.
  const freeze = document.createElement("style");
  freeze.textContent = "*,*::before,*::after{transition:none!important}";
  document.head.appendChild(freeze);
  document.documentElement.setAttribute("data-theme", next);
  void window.getComputedStyle(document.body).color;
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => freeze.remove()));
}

export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>("system");
  const [systemDark, setSystemDark] = useState(false);

  useEffect(() => {
    setPreference(readPreference());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setSystemDark(mq.matches);
    const onChange = (e: MediaQueryListEvent) => {
      setSystemDark(e.matches);
      // Following the system: re-resolve the explicit attribute the stylesheet keys on.
      if (readPreference() === "system") applyPreference("system");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const choose = useCallback((pref: ThemePreference) => {
    setPreference(pref);
    applyPreference(pref);
    try {
      if (pref === "system") window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, pref);
    } catch {}
  }, []);

  const resolved: "light" | "dark" = preference === "system" ? (systemDark ? "dark" : "light") : preference;
  return { preference, resolved, choose };
}

export function useThemeColor(token: string, fallback: string): string {
  const [color, setColor] = useState(fallback);

  useIsomorphicLayoutEffect(() => {
    const read = () => {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${token}`).trim();
      if (raw) setColor(`rgb(${raw.split(/\s+/).join(", ")})`);
    };
    read();
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", read);
    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      mq.removeEventListener("change", read);
      observer.disconnect();
    };
  }, [token]);

  return color;
}
