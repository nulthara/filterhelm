# FilterHelm — Brand Guidelines

**Publisher:** Nulthara  
**Repository:** `nulthara/filterhelm` (intended namespace)  
**Tagline:** Multiple instances. One control plane.  
**Positioning:** An independent, self-hosted control plane for managing multiple DNS filtering instances.

## Brand character

FilterHelm should feel **capable, calm, precise, trustworthy, and approachable**. Prefer clear descriptions of what the product does over exaggerated cybersecurity claims. The word *Helm* communicates control and coordination; *Filter* communicates DNS-level filtering.

### Voice and messaging

- Use plain technical language: *instances*, *DNS filtering*, *configuration*, *status*, *blocklists*, *management*.
- Emphasize centralized administration and user ownership.
- Avoid suggesting official Pi-hole affiliation or compatibility with platforms not yet implemented.
- Do not describe FilterHelm as a security guarantee or imply that DNS filtering blocks every threat.
- On the README: **FilterHelm — Multiple instances. One control plane.**
- Supporting description: **A self-hosted management platform for multiple DNS filtering instances.**
- For an initial Pi-hole-focused release: **An independent, third-party management platform for Pi-hole instances. Not affiliated with or endorsed by Pi-hole.** Check the upstream trademark guidance before publishing.

## Logo system

The mark combines three components: a **hexagon** (cohesive product identity), a **ship's helm** (control), and a **server stack** (managed instances). The icon has been redrawn as a simplified SVG suitable for implementation. It is a vector interpretation of the approved concept board, not a pixel-perfect extraction.

Use `logos/filterhelm-icon.svg` for primary standalone branding. Use `logos/filterhelm-icon-compact.svg` for small-scale use and `logos/filterhelm-icon-monochrome.svg` for single-color contexts. Horizontal wordmarks are supplied for light and dark surfaces. Prefer the standalone icon for GitHub avatars and app navigation.

- Preserve the mark's aspect ratio and its internal spacing.
- Keep clear space of at least 12.5% of icon width around the standalone icon.
- Never stretch, rotate, skew, add drop shadows, or recolor parts arbitrarily.
- Avoid gradients, bloom, glassmorphism, or noisy textures in production artwork.
- Do not embed the tagline inside tiny icons.
- At 16–32 px, use the compact icon and visually verify the result.

## Color system

| Token | Hex | Intended use |
| --- | --- | --- |
| Navy | `#0B1F3B` | Dark backgrounds, headings, high-contrast UI |
| Blue | `#1E88FF` | Primary actions, selected navigation, active controls |
| Cyan | `#00D6FF` | Accent details, indicators, subtle emphasis |
| Ice | `#E6F4FF` | Light surfaces, pale highlights, dark-mode text |
| White | `#FFFFFF` | Light surfaces and text on dark backgrounds |

**Accessibility:** These are brand colors, not an automatically accessible component palette. Verify WCAG contrast for actual text/background pairs. Do not use cyan or blue alone to communicate success, errors, or health. Use text and icons as well.

## Typography

Preferred: **Inter**, with `system-ui, Segoe UI, Arial, sans-serif` fallbacks. Do not bundle proprietary font files. Use sentence case for UI labels and headings; reserve all caps for occasional short eyebrow text. Aim for readable line lengths and generous whitespace.

Suggested type scale: 12 px caption, 14 px secondary, 16 px body, 20 px section heading, 28 px page heading, 36 px marketing headline. These are starting points, not rigid rules.

## Product interface principles

1. **Operational clarity first.** Make each managed instance's name, status, and relevant actions immediately visible.
2. **Dark and light mode.** Navy works as a dark-mode foundation; use white or near-white for light surfaces.
3. **Status is not color alone.** Pair color with explicit status labels, icons, and timestamps.
4. **Destructive actions need confirmation.** Especially changes affecting multiple DNS instances.
5. **Multi-instance context stays visible.** Clearly show which instance or group is being modified.
6. **Progressive disclosure.** Show fleet overview first, then drill into individual configuration details.
7. **Consistent density.** Compact data tables are acceptable, but preserve legible labels and focus states.
8. **No unverified claims.** Avoid saying changes have synchronized until the backend confirms completion.

### Suggested navigation (adapt to implemented features)

- Overview
- Instances
- Blocklists
- Configuration
- Activity
- Settings

Do not advertise these as implemented features until they exist.

## Suggested design tokens

See `tokens/brand-tokens.css` and `tokens/brand-tokens.json`. Use the tokens as a single source of truth in the frontend. Extend with semantic roles (`surface`, `text`, `muted`, `danger`, `success`, `border`, `focus`) after testing contrast in both themes.

## Component guidance

- **Cards:** Quiet borders, restrained rounding (10–16 px), minimal shadow.
- **Buttons:** Blue primary actions; neutral secondary actions; explicit destructive styling.
- **Tables:** Readable density, stable columns, clear sorting and filtering.
- **Badges:** Use for genuine operational state, not decorative labels.
- **Charts:** High legibility; never rely solely on cyan versus blue distinctions.
- **Icons:** Simple stroke icons; use the FilterHelm mark only for brand identity.
- **Motion:** Short, subtle transitions; honor reduced-motion settings.

## Asset inventory

- `logos/filterhelm-icon.svg` — primary standalone mark
- `logos/filterhelm-icon-compact.svg` — small-size variant
- `logos/filterhelm-icon-monochrome.svg` — one-color variant
- `logos/filterhelm-horizontal-light.svg` / `.png` — horizontal lockup on light background
- `logos/filterhelm-horizontal-dark.svg` / `.png` — horizontal lockup on navy background
- `icons/filterhelm-{16,32,48,64,128,256,512,1024}.png` — raster exports
- `tokens/brand-tokens.css` — CSS variables
- `tokens/brand-tokens.json` — machine-readable design tokens

## Developer / AI-agent implementation brief

> Apply the FilterHelm brand to the application without changing its functional behavior. Use the brand tokens in `tokens/brand-tokens.css` as the starting point. Implement consistent light and dark themes, accessible contrast, semantic status indicators, and clear multi-instance context. Use the supplied SVG mark rather than recreating it in CSS. Keep UI copy direct and avoid implying official affiliation with Pi-hole. Do not introduce unimplemented features or claim synchronization success without backend confirmation. Prefer reusable components over page-specific styling.

## Before public release

- Validate final icons at 16, 32, 64, and 512 px in actual target environments.
- Test light/dark contrast and keyboard focus states.
- Confirm the logo and wordmark are legible in the GitHub README and app sidebar.
- Review the name and upstream trademark/attribution language.
- Verify GitHub namespace and container registry naming.
