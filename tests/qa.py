#!/usr/bin/env python3
"""Quality checks for Monospace Yekan Farsi."""
from __future__ import annotations

import sys
import os
import unicodedata

import uharfbuzz as hb
from fontTools.ttLib import TTFont

CELL = 560

PERSIAN = "آابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی"
ARABIC = "ءأإؤئآبتثجحخدذرزسشصضطظعغفقكلمنهويىة"
DIGITS = "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩"
PUNCT = "،؛؟«»…ـ"
SAMPLE = (
    "سلام دنیا، چطور هستید؟ این یک فونت مونواسپیس فارسی است. "
    "کتاب‌ها و می‌روم و نمی‌دانم. ۱۲۳۴۵۶۷۸۹. ﷲ لا اله الا الله. "
    "أهلاً وسهلاً، اللغة العربية. Hello, world! 0123 {}[]()."
)


def shape(path: str, text: str, direction: str = "rtl"):
    blob = hb.Blob.from_file_path(path)
    font = hb.Font(hb.Face(blob))
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = direction
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return buf, font


def main(path: str) -> int:
    font = TTFont(path)
    order = font.getGlyphOrder()
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    errors = []

    # 1. every glyph is on the grid
    for name in order:
        adv = hmtx[name][0]
        if adv not in (0, CELL):
            errors.append(f"advance {adv}: {name}")

    # 2. coverage
    for ch in PERSIAN + ARABIC + DIGITS + PUNCT + "؟":
        if ord(ch) not in cmap:
            errors.append(f"missing U+{ord(ch):04X} {ch}")

    # 3. shaping: no .notdef, correct advances
    buf, _ = shape(path, SAMPLE)
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = order[info.codepoint]
        if name == ".notdef":
            errors.append("shaped .notdef in sample")
        if pos.x_advance not in (0, CELL):
            errors.append(f"shaped advance {pos.x_advance} for {name}")

    # 4. joining: adjacent Arabic glyphs must touch at the cell edge when both
    #    sides are joining forms.
    glyf = font["glyf"]

    def edge_ink(name, side):
        g = glyf[name]
        if g.numberOfContours <= 0:
            return False
        pts = list(g.coordinates)
        lim = 4 if side == "right" else CELL - 4
        if side == "right":
            return any(x <= lim for x, y in pts)
        return any(x >= lim for x, y in pts)

    for word in ("سلام", "دنیا", "چطور", "متن", "کتاب", "است", "فارسی"):
        buf, _ = shape(path, word)
        names = [order[i.codepoint] for i in buf.glyph_infos]
        for i in range(len(names) - 1):
            left, right = names[i + 1], names[i]
            if font["hmtx"][left][0] == 0 or font["hmtx"][right][0] == 0:
                continue
            # If the right-hand glyph has a left connector, the left-hand
            # glyph must have a right connector (and vice versa).
            if edge_ink(right, "left") != edge_ink(left, "right"):
                errors.append(f"broken join {right} | {left} in {word}")

    # 5. required OpenType tables
    for tag in ("GSUB", "GPOS", "GDEF", "cmap", "glyf", "hmtx", "OS/2"):
        if tag not in font:
            errors.append(f"missing table {tag}")

    # 6. name/licence
    names = {n.nameID: n.toUnicode() for n in font["name"].names if n.platformID == 3}
    if "SIL Open Font License" not in names.get(13, ""):
        errors.append("licence string missing")
    if "Ubuntu" in names.get(1, ""):
        errors.append("reserved name 'Ubuntu' used in family name")

    if errors:
        print(f"FAIL ({len(errors)} issues)")
        for e in errors[:40]:
            print("  -", e)
        return 1
    print(f"OK  {path}: {len(order)} glyphs, {len(cmap)} cmap entries")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist/MonospaceYekanFarsi-Regular.ttf"))
