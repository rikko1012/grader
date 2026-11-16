# UI resources (free, open source)

The owner's reference toolbox for Scout's interface work (2026-11-16). These are **design references, not dependencies**.

## How they apply to Scout

Scout's front end is hand-written HTML, CSS and vanilla JS modules served by `server.py` (Python stdlib). There is no React,
Svelte, Tailwind, npm or bundler. So:

- **React/Svelte component libraries** (shadcn/ui, Radix, Mantine, Headless UI, Base UI, Ark, Ant Design, Chakra, Fluent, Base Web,
  Blueprint, React Bootstrap, React Spectrum/Aria, Bits UI, Melt UI) **cannot be installed** here. Read them for patterns:
  accessible dialog/menu/popover behaviour (focus trap, Esc, aria attributes), spacing and type scales, component states. Then
  re-implement the pattern in plain HTML/CSS/JS using the app's existing tokens.
- **CSS frameworks** (Bootstrap, Bulma): don't add them; they would fight the existing stylesheets. Borrow layout/utility ideas only.
- **Charts**: Scout draws charts as inline SVG (e.g. `web/record.js`). Prefer that. Chart.js / ECharts can run as one standalone
  script, but each adds bandwidth to every visitor (bandwidth is a tracked cost) - only with a clear need.
- **AI design skills** (UI UX Pro Max, Vercel agent-skills, Anthropic skills, UI Skills, Skill UI): use as written guidance for
  hierarchy, spacing, contrast, motion and review checklists before writing UI code.
- Always keep the app's **current look**: charcoal / white / one blue (`web/upgrade-hub.css` `.scout-hub`, `web/upgrade.css`),
  Outfit + DM Mono. The black-and-gold theme was retired - do not bring it back.

## $0 rule

No paid libraries, premium components, trials, paid APIs or sign-ups. Check a repo's licence before copying any code; if a project
has paid tiers, use only its free open-source part. Prefer self-hosted assets (no new third-party hosts on member or landing pages).

## The list

Licences below are the projects' stated licences as commonly published; **re-check the repo's LICENSE file before copying code**.
"Check" = not verified here.

| # | Repository | Use for Scout | Licence |
|---|---|---|---|
| 1 | https://github.com/shadcn-ui/ui | Component anatomy, states, spacing | MIT |
| 2 | https://github.com/radix-ui/primitives | Accessible dialog/menu/popover/tabs behaviour | MIT |
| 3 | https://github.com/mantinedev/mantine | Dashboard layout patterns | MIT |
| 4 | https://github.com/tailwindlabs/headlessui | Keyboard/aria behaviour of menus, listboxes | MIT |
| 5 | https://github.com/mui/base-ui | Unstyled accessible component behaviour | MIT |
| 6 | https://github.com/chakra-ui/ark | Interaction state machines | MIT |
| 7 | https://github.com/ant-design/ant-design | Data-dense table/form patterns | MIT |
| 8 | https://github.com/chakra-ui/chakra-ui | Theming/token structure | MIT |
| 9 | https://github.com/carbon-design-system/carbon | Data tables, density, guidelines | Apache-2.0 |
| 10 | https://github.com/microsoft/fluentui | Focus and motion guidance | MIT |
| 11 | https://github.com/uber/baseweb | Complex app controls | MIT |
| 12 | https://github.com/palantir/blueprint | Data-heavy dashboard patterns | Apache-2.0 |
| 13 | https://github.com/twbs/bootstrap | Responsive grid ideas (don't install) | MIT |
| 14 | https://github.com/jgthms/bulma | CSS layout ideas (don't install) | MIT |
| 15 | https://github.com/react-bootstrap/react-bootstrap | Reference only | MIT |
| 16 | https://github.com/adobe/react-spectrum | Accessibility patterns (React Spectrum) | Apache-2.0 |
| 17 | https://github.com/adobe/react-spectrum | React Aria hooks: keyboard/screen-reader behaviour | Apache-2.0 |
| 18 | https://github.com/chakra-ui/zag | Framework-agnostic state machines (closest fit for vanilla JS) | MIT |
| 19 | https://github.com/huntabyte/bits-ui | Reference only (Svelte) | MIT |
| 20 | https://github.com/melt-ui/melt-ui | Reference only (Svelte) | MIT |
| 21 | https://github.com/recharts/recharts | Chart design reference (React) | MIT |
| 22 | https://github.com/plouc/nivo | Chart design reference (React) | MIT |
| 23 | https://github.com/airbnb/visx | Low-level SVG chart ideas | MIT |
| 24 | https://github.com/chartjs/Chart.js | Standalone canvas charts if inline SVG is not enough | MIT |
| 25 | https://github.com/apache/echarts | Advanced charts (heavy; avoid on public pages) | Apache-2.0 |
| 26 | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill | Design rules: palettes, type, layout, UX checks | Check |
| 27 | https://github.com/vercel-labs/agent-skills | Frontend/design review guidelines | Check |
| 28 | https://github.com/anthropics/skills | Frontend-design skill and others | Check (varies per skill) |
| 29 | https://github.com/ibelick/ui-skills | Interface quality rules for agents | Check |
| 30 | https://github.com/isreeharim/uiskill | AI-native design skills | Check |

## Already used

- 2026-11-16 landing refresh (`web/landing.html`): hero/proof/feature-block structure and hierarchy guidance from the design-skill
  references; no code copied, no dependency added.
