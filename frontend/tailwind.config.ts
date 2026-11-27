import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    borderRadius: {
      none: "0px",
      sm: "0px",
      DEFAULT: "0px",
      md: "0px",
      lg: "0px",
      xl: "0px",
      "2xl": "0px",
      "3xl": "0px",
      card: "0px",
      composer: "0px",
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
        plum: {
          DEFAULT: token("accent"),
          muted: token("clay"),
          deep: token("accent-hover"),
          soft: token("accent-soft"),
        },
        copper: token("clay"),
        "on-accent": token("on-accent"),
        ok: { DEFAULT: token("ok"), soft: token("ok-soft"), dot: token("ok-dot") },
        warn: { DEFAULT: token("warn"), soft: token("warn-soft"), dot: token("warn-dot") },
        err: { DEFAULT: token("err"), soft: token("err-soft"), dot: token("err-dot") },
        "data-1": token("data-1"),
      },
      fontFamily: {
        sans: ["var(--font-inter)", "Inter", "ui-sans-serif", "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        display: ["var(--font-manrope)", "Manrope", "var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", '"JetBrains Mono"', '"Cascadia Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgb(var(--shadow) / 0.04)",
        composer: "0 1px 2px rgb(var(--shadow) / 0.04), 0 12px 32px rgb(var(--shadow) / 0.07)",
        pop: "0 16px 40px rgb(var(--shadow) / 0.14)",
      },
    },
  },
  plugins: [],
};

export default config;
