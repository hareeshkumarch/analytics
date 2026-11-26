# UI design

**Look: "Graphite" (minimal).** Neutral zinc grays with a charcoal accent (near-white in dark mode).
Color appears only where it means something: small status dots, and red for errors. Manrope for headings,
Inter for UI text, JetBrains Mono for ids, SQL and traces. **Sharp corners everywhere** (no rounded
buttons, cards or inputs); only status dots and spinners are round. Pages use the full width up to
1480px with tight gutters (16px on phones, 32px on desktop).

**Theme:** System (default, follows the OS), Light or Dark. The switch is at the bottom of the sidebar,
an icon button when the rail is collapsed or on phones. The choice is stored in `localStorage` (`ledgerline.theme`)
and applied before first paint by an inline script, so there is no flash.

**Design canvas (Claude Design):** https://claude.ai/artifact/EJxwEePBJzNBVh9JvhkVgJ
(private; share it from the page's Share menu). Boards: Ask, Data, Settings,
Design tokens & components, Ask on phone.

## Where it lives in code

| What | File |
|---|---|
| Color tokens (light + dark, RGB channels; `data-theme` overrides the OS) | `frontend/src/app/globals.css` |
| Tailwind names for tokens, fonts, radii, shadows | `frontend/tailwind.config.ts` |
| Fonts (`next/font`, self-hosted at build time, system fallbacks) | `frontend/src/app/layout.tsx` |
| Sidebar: collapsible icon rail (Ctrl/⌘ B, remembered), phone top bar | `frontend/src/components/AppShell.tsx` |
| Selects (keyboard + screen-reader accessible, viewport-aware) | `frontend/src/components/RichSelect.tsx` |
| Horizontal scroller with edge fades (scrollbars are hidden) | `frontend/src/components/HScroll.tsx` |
| Safe Markdown for answers (tables, code, lists, links) | `frontend/src/components/SafeMarkdown.tsx` |
| Connector catalog (fields per source) | `frontend/src/lib/connectors.ts` |
| Theme preference (`useTheme`), no-flash init script, chart colors that follow the theme (`useThemeColor`) | `frontend/src/lib/theme.ts` |
| Theme switch (System / Light / Dark; compact icon variant) | `frontend/src/components/ThemeToggle.tsx` |

## Tokens (light)

| Token | Hex | Use |
|---|---|---|
| canvas | `#FAFAFA` | page |
| sidebar / panel | `#F4F4F5` | sidebar, segmented track, table heads |
| surface | `#FFFFFF` | cards, inputs, menus |
| bubble | `#EFEFF1` | your question in Ask |
| ink | `#18181B` | headings, primary text |
| body | `#3F3F46` | long text |
| secondary (muted) | `#696972` | meta text: 5.2:1 on canvas |
| border / border-strong | `#E4E4E7` / `#8C8C96` | dividers / input outlines (3:1+) |
| plum (accent) | `#18181B` | primary buttons, active states, focus ring |
| plum-soft | `#EEEEF0` | focus halo, selected rows |
| plum-muted | `#71717A` | logo marks, chart bars |
| ok dot | `#16A34A` | success status dots |
| warn | `#92400E` on `#FFF7E6`, dot `#D97706` | "needs your attention" (awaiting approval, reconnecting, newer data). Amber so it never looks like "done" |
| err | `#B91C1C` on `#FEF2F2` | errors |

Connector logos (`components/ConnectorLogo.tsx`) are the one place fixed brand colours are allowed: each logo is a self-contained tile with its own background and foreground, so it reads the same in both themes.

- **Official marks:** 16 connector brands and 4 LLM providers use their official mark from [Simple Icons](https://simpleicons.org) 16.35.0 (CC0), vendored as path data in `components/brandPaths.ts`, so the app makes no runtime request.
- **LLM providers:** `ProviderLogo` (same file) shows vendored brand marks for all seven providers in Settings (Anthropic, Google Gemini, Kimi and NVIDIA from Simple Icons 16.35.0; OpenAI, Groq and xAI vendored separately).
- **Drawn marks:** AWS (S3, RDS) and Microsoft Azure aren't in Simple Icons, and their official icon packages only allow architecture diagrams, training and documentation (AWS Trademark Guidelines §3(d); Azure icon terms). Those connectors therefore keep a drawn mark in brand colours until written permission is obtained. Generic sources (file upload, link, REST) use a neutral glyph.
- **Adding a logo:** download `icons/<slug>.svg` from the same Simple Icons version, check it is a single `<path>`, add its `d` to `brandPaths.ts`, and map it in `MARKS`.
- **Brand rules:** logos are trademarks of their owners and only identify the connector; don't recolour them beyond the brand colour or alter their shape.

`plum` is just the accent's legacy class name; the color is charcoal. To re-tint the whole app,
change the variables in `globals.css`; no component needs editing.

## Rules of thumb

- Use token classes (`bg-surface`, `text-ink`, `text-body`, `text-secondary`, `border-border`,
  `bg-plum` + `text-on-accent`, `bg-ok-soft text-ok` + `bg-ok-dot` / `warn` / `err`), never hex values, `bg-white` or
  Tailwind's palette colors, or dark mode breaks.
- Corners: the Tailwind radius scale is 0 (`rounded`, `rounded-lg`, `rounded-card` … all render square).
  Don't add arbitrary radii like `rounded-[10px]`. `rounded-full` is only for dots and spinners.
- Cards: `rounded-card border border-border bg-surface shadow-card`, titles `font-display text-lg font-semibold`.
- Any choice from a list uses `RichSelect`. Two or three mutually exclusive modes (Quick/Deep) use a
  segmented control instead, because both options stay visible.
- Wide content goes in `HScroll` (edge fades, no bar). Vertical scroll areas show a thin themed scrollbar.
- Controls are at least 40px tall (44px for inputs and selects), and every icon-only button has an `aria-label`.
- Shared pieces live in `components/ui.tsx`: `btn.*`, `Notice`, `Badge`, `SearchInput`, `FilterChips`, `EmptyState`, `Details`, `Skeleton`, `Spinner`. Use them instead of new class strings.
- Lists with more than a handful of items get a `SearchInput` and, where useful, `FilterChips` with counts.
- Show people what they need to act on; put ids, hashes, raw traces and raw errors behind a collapsed `Details` ("Technical details").
- Fonts are self-hosted with `next/font` (no runtime request to Google).
