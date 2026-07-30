"""Generate README images for OpenFilings.

Everything drawn here comes from real output: the terminal card replays actual
CLI runs, and the coverage board reflects the live smoke suite's own result
tiers. Nothing is illustrative.
"""

from PIL import Image, ImageDraw, ImageFont

BG = (10, 15, 28)
PANEL = (16, 23, 39)
INK = (233, 240, 249)
MUTED = (148, 168, 194)
DIM = (104, 126, 154)
ACCENT = (86, 176, 255)

GREEN = (63, 185, 80)
AMBER = (210, 153, 34)
GREY = (110, 128, 150)
BLUE = (88, 143, 232)

B = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
R = "/System/Library/Fonts/Supplemental/Arial.ttf"
M = "/System/Library/Fonts/SFNSMono.ttf"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


# --------------------------------------------------------------------------
# 1. Terminal card - a verbatim replay of three real CLI runs.
# --------------------------------------------------------------------------
LINES = [
    ("prompt", '$ openfilings search "Nokia" --source esef'),
    ("out", "fi_lei_549300A0JPRWG1KI7U06   FI   Nokia Oyj            esef   listed"),
    ("out", "fi_lei_743700YQIO8Y4L4WKR40   FI   Nokian Renkaat Oyj   esef   listed"),
    ("gap", ""),
    (
        "prompt",
        "$ openfilings filings fi_lei_549300A0JPRWG1KI7U06 --source esef --limit 3",
    ),
    ("out", "fi_esef_23894   2026-03-10   financial_report   period ended 2025-12-31"),
    ("out", "fi_esef_17334   2025-03-18   financial_report   period ended 2024-12-31"),
    ("out", "fi_esef_12506   2024-03-05   financial_report   period ended 2023-12-31"),
    ("gap", ""),
    ("prompt", "$ openfilings financials fi_esef_23894 --statement balance-sheet"),
    ("json", "{"),
    ("json", '  "statement_type": "balance_sheet",'),
    ("json", '  "currency": "EUR",'),
    ("json", '  "line_items": [{'),
    ("json", '    "code": "total_assets",'),
    ("json", '    "concept": "ifrs-full:Assets",'),
    ("json", '    "values": [{ "value": "37597000000",'),
    (
        "json",
        '                 "provenance": "tagged_xbrl", "confidence": 100 }]',
    ),
    ("json", "  }]"),
    ("json", "}"),
]

W, H = 1280, 800
img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, W - 40, H - 40], radius=14, fill=PANEL)

for i, colour in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
    d.ellipse([72 + i * 26, 68, 86 + i * 26, 82], fill=colour)
d.text((W // 2 - 60, 66), "openfilings", font=font(R, 20), fill=DIM)

mono = font(M, 22)
y = 118
for kind, text in LINES:
    if kind == "gap":
        y += 16
        continue
    colour = ACCENT if kind == "prompt" else INK
    if kind == "json":
        colour = (150, 200, 160) if '"' in text and ":" in text else MUTED
    d.text((76, y), text, font=mono, fill=colour)
    y += 34

img.save("/Users/ryanrodrigues/Documents/OpenFilings/docs/cli-demo.png")
print("cli-demo.png")

# --------------------------------------------------------------------------
# 2. Coverage board - tiers mirror the live smoke suite's own outcomes.
# --------------------------------------------------------------------------
VERIFIED = [
    "Italy",
    "Denmark",
    "Finland",
    "Norway",
    "Poland",
    "Belgium",
    "Luxembourg",
    "Portugal",
    "Mexico",
    "India",
    "Peru",
    "Colombia",
    "Turkey",
]
EXTRACTED = [
    "United Kingdom",
    "Netherlands",
    "France",
    "Spain",
    "Sweden",
    "Austria",
    "Brazil",
    "Singapore",
]
KEYED = ["Japan", "South Korea"]
DISCOVERY = ["Canada", "Australia"]

W, H = 1280, 800
img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
d.rectangle([0, 0, 10, H], fill=ACCENT)

d.text((64, 52), "Market coverage", font=font(B, 52), fill=INK)
d.text(
    (64, 118),
    "25 jurisdictions, tiered by what the last live run against the real "
    "regulators actually proved",
    font=font(R, 25),
    fill=MUTED,
)

GROUPS = [
    (
        GREEN,
        "Statements verified",
        "balance-sheet identity holds on source-tagged totals",
        VERIFIED,
    ),
    (
        AMBER,
        "Statements extracted",
        "a total is derived, so the identity cannot self-check",
        EXTRACTED,
    ),
    (
        BLUE,
        "Key required",
        "company search is keyless; filings need a free API key",
        KEYED,
    ),
    (GREY, "Discovery only", "no keyless filing retrieval exists", DISCOVERY),
]

y = 190
name_font = font(R, 24)
for colour, title, note, markets in GROUPS:
    d.rounded_rectangle([64, y, 1216, y + 30], radius=4, fill=BG)
    d.ellipse([64, y + 8, 80, y + 24], fill=colour)
    d.text((94, y + 3), title, font=font(B, 27), fill=INK)
    tw = d.textlength(title, font=font(B, 27))
    d.text((94 + tw + 14, y + 8), f"— {note}", font=font(R, 22), fill=DIM)
    y += 48

    x = 94
    for market in markets:
        w = d.textlength(market, font=name_font)
        if x + w + 40 > 1216:
            x = 94
            y += 46
        d.rounded_rectangle([x, y, x + w + 30, y + 38], radius=19, fill=PANEL)
        d.line([x + 1, y + 8, x + 1, y + 30], fill=colour, width=3)
        d.text((x + 15, y + 6), market, font=name_font, fill=(198, 214, 233))
        x += w + 30 + 12
    y += 76

d.text(
    (64, H - 62),
    "Checked weekly in CI against live regulator endpoints — not a static table.",
    font=font(R, 22),
    fill=DIM,
)

img.save("/Users/ryanrodrigues/Documents/OpenFilings/docs/coverage.png")
print("coverage.png")
