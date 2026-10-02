# White-labelling

Everything client-specific is configured in **`brand.json`** at the repo root (copy `brand.example.json`).
Missing keys fall back to the neutral defaults in `studio/brand.py`; nested keys (like `colors`) are merged.
Restart both services after editing.

| Key | Used for |
|---|---|
| `product_name`, `org_name`, `logo_initials` | Dashboard sidebar, login page, browser title, prompts |
| `public_url` | Shown to the team; set to the dashboard's HTTPS address |
| `colors.accent` | The single highlight colour in graphics (key word, eyebrows, step circles) |
| `colors.accent_glow` | Active caption word on footage and the dashboard accent |
| `colors.*` (others) | Graphics card ink, dark instrument tiles, check badges, strike-throughs |
| `brief` | Default brand brief (Claude's context for teardowns, ideas, scripts, captions). Editable in Settings. |
| `pillars` | Default content pillars. Editable in Settings. |
| `banned_terms` | `[term, reason]` pairs flagged in every script and caption (blocks scheduling) |
| `offer_words` | Dollar amounts near these words are treated as offer pricing and flagged |
| `cta` | What a TOFU / MOFU / BOFU call-to-action should be |
| `graphics_style` | One-line description of the graphics look, given to Claude's motion designer and reviewer |

## Fonts
Graphics and captions ship with Space Grotesk (display), Inter (body), Kode Mono (eyebrows), plus Barlow and
Anton for the alternate caption styles — all SIL Open Font License. To use other fonts, add the `.ttf` files to
`assets/fonts/`, update the `@font-face` rules at the top of `studio/editor/motion/graphics.html`, and the
caption `STYLES` in `studio/editor/captions.py`.

## The brief matters most
Claude writes everything from the brief. Include: what's sold and to whom; the audience's pains in their own
words; verified proof points the brand is happy to use publicly (Claude uses these and never invents others);
voice and tone; and hard rules. Put the hard rules in `banned_terms` / `offer_words` too so they're enforced
in code, not just requested.
