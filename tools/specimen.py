#!/usr/bin/env python3
"""Render the specimen images used in the README."""
from __future__ import annotations

import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(ROOT, "dist", "MonospaceYekanFarsi-Regular.ttf")
BOLD = os.path.join(ROOT, "dist", "MonospaceYekanFarsi-Bold.ttf")
OUT = os.path.join(ROOT, "specimens")


def font(path, size):
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)


def overview():
    W, H = 1400, 640
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    label = ImageFont.load_default(size=15)
    persian = (
        "سلام دنیا. این یک فونت مونواسپیس کامل است: حروف لاتین از "
        "Ubuntu Sans Mono و حروف فارسی از فونت یکان گرفته شده‌اند. "
        "هر نویسه دقیقاً یک خانه را پر می‌کند، پس ستون‌ها در ترمینال و "
        "ویرایشگر کد هم‌تراز می‌مانند."
    )
    y = 16
    d.text((24, y), "Regular 400 / Persian", font=label, fill=(150, 30, 30))
    y += 24
    for line in textwrap.wrap(persian, 60):
        d.text((24, y), line, font=font(REG, 28), fill="black", direction="rtl")
        y += 44

    y += 10
    d.text((24, y), "Regular 400 / Code", font=label, fill=(150, 30, 30))
    y += 24
    code = (
        "def main():\n"
        "    print('سلام دنیا')  # ۱۲۳\n"
        "    return 42"
    )
    d.multiline_text((24, y), code, font=font(REG, 23), fill="black", spacing=4)

    y += 118
    d.text((24, y), "Bold 700", font=label, fill=(150, 30, 30))
    y += 26
    for line in textwrap.wrap("کتاب‌ها و می‌روم و نمی‌دانم؛ چرا؟ چون زبان فارسی شیرین است.", 56):
        d.text((24, y), line, font=font(BOLD, 28), fill="black", direction="rtl")
        y += 44
    d.text((24, y + 6), "for i in range(10): print(i)", font=font(BOLD, 23), fill="black")
    img.save(os.path.join(OUT, "overview.png"))


def alphabet():
    reg = font(REG, 34)
    bold = font(BOLD, 34)
    words = [" ".join("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی"), "آ ء أ ؤ ئ ۀ ۱۲۳۴۵۶۷۸۹"]
    img = Image.new("RGB", (1400, 220), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(words):
        d.text((20, 20 + i * 62), line, font=reg, fill="black", direction="rtl")
        d.text((760, 20 + i * 62), line, font=bold, fill="black", direction="rtl")
    d.text((20, 160), "regular 400", font=ImageFont.load_default(size=14), fill=(120, 120, 120))
    d.text((760, 160), "bold 700", font=ImageFont.load_default(size=14), fill=(120, 120, 120))
    img.save(os.path.join(OUT, "alphabet.png"))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    overview()
    alphabet()
    print("specimens written to", OUT)
