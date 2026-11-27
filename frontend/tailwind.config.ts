import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    borderRadius: {
      none: "0px",
      sm: "4px",
      DEFAULT: "6px",
      md: "8px",
      lg: "10px",
      xl: "14px",
      "2xl": "14px",
      full: "9999px",
    },
    extend: {
      colors: {
        canvas: token("canvas"),
        sidebar: token("sidebar"),
        panel: token("panel"),
        surface: token("surface"),
        ink: token("ink"),
        body: token("body"),
        secondary: token("muted"),
        border: token("border"),
        "border-strong": token("border-strong"),
        bubble: token("bubble"),
        accent: {
          DEFAULT: token("accent"),
          hover: token("accent-hover"),
          soft: token("accent-soft"),
        },
        "on-accent": token("on-accent"),
        popover: token("popover"),
        brand: { DEFAULT: token("brand"), hover: token("brand-hover") },
        focus: token("focus"),
        ok: { DEFAULT: token("ok"), soft: token("ok-soft"), dot: token("ok-dot") },
        warn: { DEFAULT: token("warn"), soft: token("warn-soft"), dot: token("warn-dot") },
        err: { DEFAULT: token("err"), soft: token("err-soft"), dot: token("err-dot") },
        "data-1": token("data-1"),
      },
      // Six text steps. Headings use the default xl/2xl sizes or explicit display sizes.
      fontSize: {
        "2xs": "11px",
        xs: "12px",
        sm: "13px",
        base: "14px",
        md: "15px",
      },
      // Claude's interface weights sit between the usual steps (435 for controls, 520 for labels).
      fontWeight: {
        medium: "450",
        semibold: "540",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "Inter", "ui-sans-serif", "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        display: ["var(--font-display)", "Newsreader", "Georgia", "ui-serif", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", '"Cascadia Mono"', "Menlo", "Consolas", "monospace"],
      },
      boxShadow: {
        card: "none",
        composer: "0 4px 20px rgb(0 0 0 / 0.075), inset 0 0 0 1px rgb(var(--ink) / 0.1)",
        pop: "0 8px 24px rgb(0 0 0 / 0.12), 0 2px 6px rgb(0 0 0 / 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
