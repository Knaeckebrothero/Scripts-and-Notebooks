# Vendored JavaScript

Nothing is loaded from a CDN at runtime. Files here are copied in unchanged.

| File | Project | Version | Licence | Source | SHA-256 |
|---|---|---|---|---|---|
| `htmx.min.js` | [htmx](https://htmx.org) | 2.0.4 | Zero-Clause BSD (0BSD) | `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |

To update: download the new `dist/htmx.min.js`, replace the file, update this
table and the version asserted in `tests/test_pages.py`. The console sets
`allowEval: false` and `includeIndicatorStyles: false` (see `base.html`), so
check the release notes for changes to those options.
