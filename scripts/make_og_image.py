"""Draw the link-preview image (Open Graph / Twitter card): app/static/og.png, 1200x630.

    python scripts/make_og_image.py

Re-run after changing the wording; commit the PNG. Uses the Inter font when installed, else DejaVu Sans.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "og.png"
W, H = 1200, 630
BG, INK, MUTED, ACCENT, SOFT, LINE = "#fafafa", "#18181b", "#52525b", "#4f46e5", "#eef2ff", "#e4e4e7"

FONTS = {
    "bold": ["/usr/share/fonts/opentype/inter/Inter-Bold.otf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "semi": [
        "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "regular": ["/usr/share/fonts/opentype/inter/Inter-Regular.otf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
}


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for path in FONTS[weight]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit(f"No {weight} font found; install Inter or DejaVu")


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill=ACCENT)

    # The app's magnifier mark, then the name.
    d.ellipse([80, 82, 136, 138], outline=ACCENT, width=9)
    d.line([128, 130, 156, 158], fill=ACCENT, width=10)
    d.text((180, 78), "Contexta", font=font("bold", 56), fill=INK)

    d.text((80, 200), "Cited answers to ML and NLP", font=font("bold", 64), fill=INK)
    d.text((80, 278), "research questions", font=font("bold", 64), fill=INK)

    # A sample answer line with citation chips, the product's signature.
    y = 400
    d.rounded_rectangle([80, y - 18, W - 80, y + 70], radius=18, fill="#ffffff", outline=LINE, width=2)
    body = font("regular", 30)
    x = 110
    for part in ("LoRA trains small low-rank matrices ", "[1]", ", so GPU memory drops ", "[2]"):
        if part.startswith("["):
            w = d.textlength(part.strip("[]"), font=font("semi", 24)) + 22
            d.rounded_rectangle([x, y + 6, x + w, y + 42], radius=8, fill=SOFT)
            d.text((x + 11, y + 9), part.strip("[]"), font=font("semi", 24), fill=ACCENT)
            x += w + 6
        else:
            d.text((x, y + 4), part, font=body, fill=INK)
            x += d.textlength(part, font=body)

    if x > W - 100:
        raise SystemExit(f"sample line overflows its box ({x:.0f}px > {W - 100}px): shorten it")
    d.text((80, 540), "25 landmark papers  ·  live arXiv  ·  your own PDFs", font=font("regular", 30), fill=MUTED)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
