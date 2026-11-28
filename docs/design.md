# Design System — Ledgerline

Ledgerline's interface is a warm minimalist neutral, close to editorial monochrome: a warm off-white canvas (`#F7F6F2`), crisp white cards (`#FFFFFF`) separated by hairline borders (`#E6E4DF`) rather than shadows, stone-grey secondary text, and near-black (`#111111`) for primary text and every call to action. There is no bright brand colour; hierarchy comes from contrast between `#111111` and the stone greys.

---

## Color Tokens

Defined in [`frontend/src/app/globals.css`](../frontend/src/app/globals.css) as RGB channel triplets so Tailwind utilities support opacity modifiers cleanly across light and dark themes.

### Light Theme (`:root`)

| Token | Value | Hex | Usage |
|---|---|---|---|
| `--canvas` | `247 246 242` | `#F7F6F2` | Page background (warm off-white) |
| `--sidebar` | `242 241 236` | `#F2F1EC` | Persistent left navigation rail |
| `--panel` | `239 237 232` | `#EFEDE8` | Secondary panels, code blocks, table headers |
| `--surface` | `255 255 255` | `#FFFFFF` | Cards and inputs |
| `--bubble` | `236 234 228` | `#ECEAE4` | User question bubbles |
| `--popover` | `255 255 255` | `#FFFFFF` | Composer card, menus, charts tooltip |
| `--ink` | `17 17 17` | `#111111` | Primary text and headings |
| `--body` | `87 83 78` | `#57534E` | Answer prose, subtitles and table body text |
| `--muted` | `103 102 98` | `#676662` | Secondary metadata (`text-secondary`); the darkest stone grey that still clears WCAG AA 4.5:1 on the `#E5E4E0` control washes |
| `--border` | `230 228 223` | `#E6E4DF` | Hairline card and table dividers |
| `--border-strong` | `142 141 136` | `#8E8D88` | Input outlines, checkboxes, captions |
| `--pattern-dot` | `216 214 208` | `#D8D6D0` | Dot grid behind the Ask hero (`.bg-dot-grid`) |
| `--accent` / `--on-accent` | `17 17 17` / `255 255 255` | `#111111` / `#FFFFFF` | Primary buttons and other calls to action |
| `--brand` / `--brand-hover` | `17 17 17` / `54 53 50` | `#111111` / `#363532` | Send button and logo tile (same as the CTA) |
| `--focus` | `37 125 224` | `#257DE0` | Visible 2px keyboard focus ring |

### Dark Theme (`[data-theme="dark"]`)

The same warm stone family, inverted.

| Token | Value | Hex | Usage |
|---|---|---|---|
| `--canvas` | `25 24 22` | `#191816` | Primary background |
| `--sidebar` / `--surface` | `31 30 28` | `#1F1E1C` | Left rail and primary cards |
| `--panel` | `38 37 34` | `#262522` | Inset panels and table headers |
| `--bubble` / `--popover` | `48 46 43` | `#302E2B` | Question bubbles, menus, tooltips |
| `--ink` | `239 238 234` | `#EFEEEA` | Primary text |
| `--body` | `194 192 187` | `#C2C0BB` | Body text |
| `--muted` | `176 174 168` | `#B0AEA8` | Secondary text (clears WCAG AA 4.5:1) |
| `--border` / `--border-strong` | `44 42 39` / `113 111 106` | `#2C2A27` / `#716F6A` | Hairline dividers and input outlines |
| `--pattern-dot` | `54 52 48` | `#363430` | Dot grid |
| `--accent` / `--brand` | `239 238 234` | `#EFEEEA` | Primary buttons, send button, logo tile |

### Semantic Status & Data Palette

- **Success (`ok`)**: `#006C10` text on `#CAECC8` tint (`#90DA8E` on `#123B13` dark)
- **Warning (`warn`)**: `#794C02` text on `#F9DCA4` tint (`#FBB315` on `#442800` dark)
- **Error (`err`)**: `#A42528` text on `#FBD4D4` tint (`#F7A7A7` on `#601314` dark)
- **Chart Series**: `--data-1` (`#257DE0`), `--data-2` (`#C2652A`), `--data-3` (`#B0407A`)

---

## Typography

Loaded via `next/font/google` in [`frontend/src/app/layout.tsx`](../frontend/src/app/layout.tsx):

- **Sans (`--font-inter`)**: `Inter` — UI controls, dense tables, navigation, and Markdown body prose (`14px`–`15px` desktop, `16px` input font size below `640px` to prevent iOS Safari focus auto-zoom).
- **Display (`--font-newsreader`)**: `Newsreader` — Editorial serif for primary empty-state and page headings (`28px`–`32px`, light-to-medium weight `360`–`500`).
- **Mono**: `JetBrains Mono` / `ui-monospace` — SQL queries, snapshot IDs, metric expressions, and trace logs.
- Numeric tables, metrics, and axis ticks use `tabular-nums`.

---

## Layout & Component Geometry

- **Application Shell (`AppShell`)**:
  - Desktop left navigation rail (`md:w-[216px]` expanded, `md:w-[52px]` collapsed) with Logo, interactive **WorkspaceSwitcher** (`GET`/`POST /api/v1/workspaces`), Primary Navigation (`Ask`, `Data`, `Dashboard`, `Usage`, `Settings`), and `ThemeToggle`.
  - Mobile (`< 768px`): sticky compact top bar with Logo, WorkspaceSwitcher, and ThemeToggle, paired with a bottom tab bar.
- **Control Sizing & Radii**:
  - Default buttons and inputs (`btn`, `inputCls`, `RichSelect`): `min-h-[32px]`, `rounded-md` / `rounded-lg`.
  - Compact/dense controls (`btnSm`, `inputSmCls`): `28px` on `>= 640px` (`min-h-[32px]` on mobile inputs/buttons where needed).
  - Composer & feature cards: `rounded-xl` (`12px`), elevated subtly via `--shadow` tokens and `1px` `--border`.
- **Interactive Primitives**:
  - **Ask**: Multi-turn inline conversation thread with a single unified composer (centered top composer on new session; docked bottom follow-up composer in active threads), inline Limitations callout, `"Removed dataset"` fallback badge, slide-over Evidence drawer, and Export menu (`PDF`, `DOCX`, and semantic `HTML`).
  - **Data**: Tabbed upload (`Upload file` / `Paste CSV`) preserving separate drafts, editable & filterable proposed metric definitions (`PATCH /semantics/{id}`), and click-to-inspect cell viewer modals in `PreviewTab`.
  - **Dashboard**: In-place single-pin and batch `Re-run`, inline pin renaming, card export menu, alert threshold form, and clear distinction between new-snapshot auto-refresh and scheduled re-runs.
  - **Usage**: Independent chart metric vs `By model` bar metric switchers and gap-filled continuous hourly (`24h`) / daily (`7d`/`30d`/`90d`) timelines.
  - **Settings**: Visually separated immediate-save workspace defaults (`Preset`, `Fallback`, `Business time zone`) from draft `Cost limit & role overrides` (`Save routing`), plus confirmed workspace API key revocation (`Remove key`).
