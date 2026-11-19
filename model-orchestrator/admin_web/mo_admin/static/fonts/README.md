# Vendored fonts

Served from this directory; nothing is fetched from a font service.

| Files | Typeface | Version | Licence | Changes |
|---|---|---|---|---|
| `heros-regular`, `heros-bold`, `heros-cn-regular`, `heros-cn-bold` (`.woff2`) | TeX Gyre Heros and TeX Gyre Heros Condensed, GUST e-foundry (B. Jackowski, J. M. Nowacki) | 2.004 | GUST Font License, see `GUST-FONT-LICENSE.txt` | Container converted from OTF to WOFF2. Outlines, glyph set and names are untouched. |
| `adwaita-mono-regular`, `adwaita-mono-bold` (`.woff2`) | Adwaita Mono (GNOME; built from Iosevka by Renzhi Li) | 34.1.0, from adwaita-fonts 50.0 | SIL Open Font License 1.1, see `ADWAITA-MONO-OFL.txt` | Subset to Basic Latin, Latin-1 and common punctuation, converted to WOFF2. |

Both were taken from the distribution packages (`texlive-tex-gyre`,
`adwaita-mono-fonts`) and converted with fontTools:

    fonttools ttLib.woff2 compress -o heros-regular.woff2 texgyreheros-regular.otf
    pyftsubset AdwaitaMono-Regular.ttf --flavor=woff2 --output-file=adwaita-mono-regular.woff2 \
        --unicodes="U+0020-007E,U+00A0-00FF,U+2010-2015,U+2018-201E,U+2022,U+2026,U+2030,U+2190-2193,U+2212,U+00D7"

The CSS family names are `Heros`, `Heros Cn` and `Adwaita Mono`
(`static/css/app.css`).
