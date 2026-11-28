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
        "pattern-dot": token("pattern-dot"),
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
      /*
       * Motion: short and quiet. Entrances ease out over 140-240ms, exits are faster. Keyframes use the
       * individual `scale` and `translate` properties so they compose with an element's own transform (centred
       * dialogs are positioned with translate classes). globals.css turns all of it off for reduced motion.
       */
      transitionTimingFunction: {
        out: "cubic-bezier(0.2, 0, 0, 1)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "fade-out": { from: { opacity: "1" }, to: { opacity: "0" } },
        "fade-up": { from: { opacity: "0", translate: "0 6px" }, to: { opacity: "1", translate: "0 0" } },
        "pop-in": { from: { opacity: "0", scale: "0.97", translate: "0 -2px" }, to: { opacity: "1", scale: "1", translate: "0 0" } },
        "dialog-in": { from: { opacity: "0", scale: "0.97" }, to: { opacity: "1", scale: "1" } },
        "dialog-out": { from: { opacity: "1", scale: "1" }, to: { opacity: "0", scale: "0.98" } },
        "drawer-in": { from: { translate: "100% 0" }, to: { translate: "0 0" } },
        "drawer-out": { from: { translate: "0 0" }, to: { translate: "100% 0" } },
        "toast-in": { from: { opacity: "0", scale: "0.98", translate: "0 -8px" }, to: { opacity: "1", scale: "1", translate: "0 0" } },
        "toast-out": { from: { opacity: "1", scale: "1" }, to: { opacity: "0", scale: "0.98", translate: "0 -4px" } },
        "check-in": { from: { opacity: "0", scale: "0.5" }, to: { opacity: "1", scale: "1" } },
        "nav-progress": { from: { scale: "0 1" }, to: { scale: "0.9 1" } },
        shimmer: { from: { backgroundPosition: "150% 0" }, to: { backgroundPosition: "-50% 0" } },
      },
      animation: {
        "fade-in": "fade-in 180ms cubic-bezier(0.2, 0, 0, 1) backwards",
        "fade-out": "fade-out 140ms ease-in both",
        "fade-up": "fade-up 240ms cubic-bezier(0.2, 0, 0, 1) backwards",
        "pop-in": "pop-in 140ms cubic-bezier(0.2, 0, 0, 1) backwards",
        "dialog-in": "dialog-in 180ms cubic-bezier(0.2, 0, 0, 1) backwards",
        "dialog-out": "dialog-out 120ms ease-in both",
        "drawer-in": "drawer-in 260ms cubic-bezier(0.2, 0, 0, 1) backwards",
        "drawer-out": "drawer-out 180ms ease-in both",
        "toast-in": "toast-in 200ms cubic-bezier(0.2, 0, 0, 1) backwards",
        "toast-out": "toast-out 150ms ease-in both",
        "check-in": "check-in 220ms cubic-bezier(0.2, 0, 0, 1) backwards",
        // Fast start, long ease toward 90%: reads as progress without claiming to know when the page arrives.
        "nav-progress": "nav-progress 8s cubic-bezier(0.1, 0.7, 0.2, 1) both",
        shimmer: "shimmer 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
