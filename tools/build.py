#!/usr/bin/env python3
"""
Build Monospace Yekan Farsi.

Latin  : Ubuntu Sans Mono (static instance of the variable font)
Persian: Far Yekan, re-spaced onto the same 560/1000 monospace grid

The Arabic glyphs are not squeezed to fit: each letter keeps its designed
proportions.  Only the baseline connector bar (the horizontal stroke at
y = 0..180 in Far Yekan) is lengthened or shortened so that it reaches the
edge of the cell.  That preserves stroke weight, contrast and the shape of
every letter while producing a genuinely monospaced grid.

Usage:
    python tools/build.py --weight 400 --out dist/MonospaceYekanFarsi-Regular.ttf
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import sys
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from fontTools.pens.recordingPen import RecordingPen, DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables, ttProgram
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphCoordinates
from fontTools.varLib.instancer import instantiateVariableFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

UPEM = 1000
CELL = 560.0            # Ubuntu Sans Mono advance width, per 1000 upem
SRC_UPEM = 2048.0       # Far Yekan upem
K = UPEM / SRC_UPEM     # 0.48828125
PAD = 20.0              # side bearing kept inside an Arabic cell
MAX_BODY = CELL - 2 * PAD

# Far Yekan's baseline connector occupies these y values (source units).
BAR_Y0, BAR_Y1 = -2.0, 184.0

# Glyphs that we deliberately take from Ubuntu Sans Mono.
SYMBOL_OVERRIDES = {
    0x061F: "question",      # will be mirrored below
}
# Persian / Arabic-Indic digits are rendered with the Latin design so that
# numbers keep the same size and weight as the rest of the monospace font.
DIGIT_OVERRIDES = {
    **{0x0660 + i: "zero one two three four five six seven eight nine".split()[i] for i in range(10)},
    **{0x06F0 + i: "zero one two three four five six seven eight nine".split()[i] for i in range(10)},
}

ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
                 (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def in_arabic_range(cp: int) -> bool:
    return any(a <= cp <= b for a, b in ARABIC_RANGES)


def glyph_points(glyph_set, name: str) -> List[Tuple[float, float]]:
    """All outline points (on- and off-curve) of a (decomposed) glyph."""
    pen = RecordingPen()
    glyph_set[name].draw(pen)
    pts: List[Tuple[float, float]] = []
    for op, args in pen.value:
        if op in ("moveTo", "lineTo"):
            pts.append(args[0])
        elif op == "qCurveTo":
            pts.extend(p for p in args if p is not None)
        elif op == "curveTo":
            pts.extend(args)
        elif op == "closePath":
            pass
    return pts


def bounds(pts: Iterable[Tuple[float, float]]):
    pts = list(pts)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _y_extent_at(pts: Sequence[Tuple[float, float]], x: float) -> float:
    """Vertical extent of the outline at a given x (segment intersections)."""
    ys = []
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (x1 - x) * (x2 - x) <= 0 and x1 != x2:
            t = (x - x1) / (x2 - x1)
            ys.append(y1 + t * (y2 - y1))
    if not ys:
        return 0.0
    return max(ys) - min(ys)


def body_bounds(pts: Sequence[Tuple[float, float]], role: str = "isol"):
    """Horizontal span of the letter body, excluding the connector bar.

    The connector is the part of the ink at the joining side that is only as
    tall as the baseline bar.  Walking inwards with a scanline finds where the
    real letter starts, which is robust even when the bar is welded onto the
    contour or when a tail also sits on the baseline.
    """
    b = bounds(pts)
    if not b or role == "isol":
        return b
    x0, _, x1, _ = b
    step = 6.0
    if role == "init":            # connector on the left
        x = x0 + step
        while x < x1 and _y_extent_at(pts, x) <= 230:
            x += step
        return (x, 0, x1, 0) if x < x1 else b
    if role == "fina":            # connector on the right
        x = x1 - step
        while x > x0 and _y_extent_at(pts, x) <= 230:
            x -= step
        return (x0, 0, x, 0) if x > x0 else b
    if role == "medi":            # connectors on both sides
        a = x0 + step
        while a < x1 and _y_extent_at(pts, a) <= 230:
            a += step
        c = x1 - step
        while c > x0 and _y_extent_at(pts, c) <= 230:
            c -= step
        return (a, 0, c, 0) if a < c else b
    return b


def rename_tree(obj, mapping: Dict[str, str]):
    """Recursively rename glyph-name strings inside an otTables object graph."""
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [rename_tree(x, mapping) for x in obj]
    if isinstance(obj, tuple):
        return tuple(rename_tree(x, mapping) for x in obj)
    if isinstance(obj, dict):
        return {rename_tree(k, mapping): rename_tree(v, mapping) for k, v in obj.items()}
    if hasattr(obj, "__dict__"):
        for key, value in list(vars(obj).items()):
            if key.startswith("_"):
                continue
            setattr(obj, key, rename_tree(value, mapping))
        return obj
    return obj


def build_gsub(yekan: TTFont, mapping: Dict[str, str]):
    """Deep-copy Far Yekan's GSUB and rename glyphs.

    Far Yekan's own layout tables are used as-is: they are correct for
    HarfBuzz (the FExx glyph *names* are permuted, the substitutions are not).
    """
    gsub = copy.deepcopy(yekan["GSUB"])
    table = gsub.table
    rename_tree(table.LookupList, mapping)
    rename_tree(table.FeatureList, mapping)
    rename_tree(table.ScriptList, mapping)
    if table.LookupList.Lookup:
        for lookup in table.LookupList.Lookup:
            for st in lookup.SubTable:
                if hasattr(st, "ExtSubTable") and st.ExtSubTable is not None:
                    rename_tree(st.ExtSubTable, mapping)
    return gsub


# --------------------------------------------------------------------------
# Arabic glyph fitting
# --------------------------------------------------------------------------

FORM_FEATURES = ("init", "medi", "fina", "isol")

# Lam-alef ligature glyphs are not reachable from the init/medi/fina lookups.
# FEF5/FEF7/FEF9/FEFB/FDF2 replace lam.init + alef -> no connectors at all.
# FEF6/FEF8/FEFA/FEFC replace lam.medi + alef -> right connector only.
LIGATURE_ROLES = {
    "uFEF5": "isol", "uFEF7": "isol", "uFEF9": "isol", "uFEFB": "isol",
    "uFDF2": "isol",
    "uFEF6": "fina", "uFEF8": "fina", "uFEFA": "fina", "uFEFC": "fina",
}


def classify_forms(yekan: TTFont) -> Dict[str, str]:
    """Return glyph name -> 'init'|'medi'|'fina'|'isol' using the GSUB rules."""
    gsub = yekan["GSUB"].table
    feats = {fr.FeatureTag: fr.Feature.LookupListIndex for fr in gsub.FeatureList.FeatureRecord}
    roles: Dict[str, str] = {}
    for tag, role in (("init", "init"), ("medi", "medi"), ("fina", "fina")):
        for li in feats.get(tag, []):
            for st in gsub.LookupList.Lookup[li].SubTable:
                if gsub.LookupList.Lookup[li].LookupType == 7:
                    st = st.ExtSubTable
                mapping = getattr(st, "mapping", None)
                if not mapping:
                    continue
                for src, dst in mapping.items():
                    if src != dst:          # ignore identity mappings (dal etc.)
                        roles.setdefault(dst, role)
    cmap = yekan.getBestCmap()
    for cp, gname in cmap.items():
        if 0x621 <= cp <= 0x64A or cp in (0x67E, 0x686, 0x698, 0x6A9, 0x6AF, 0x6CC, 0x6C0):
            roles.setdefault(gname, "isol")
    return roles


def fit_glyph(pts: Sequence[Tuple[float, float]], role: str):
    """Work out the affine transform and the bar extensions for one glyph.

    Returns (sx, sy, dx, dy, extend_left, extend_right) in target units.
    """
    b = bounds(pts)
    if b is None:
        return (K, K, 0.0, 0.0, False, False)
    if role == "mark":
        return (K, K, 0.0, 0.0, False, False)
    x0, y0, x1, y1 = b
    body = body_bounds(pts, role) if role != "isol" else None
    if body is None:
        body = b
    bx0, _, bx1, _ = body
    body_w = (bx1 - bx0) * K
    sx = min(1.0, MAX_BODY / body_w) if body_w > 0 else 1.0
    sx *= K

    bw = body_w * min(1.0, MAX_BODY / body_w) if body_w > 0 else 0.0

    if role == "init":
        # Body sits at the right of the cell, connector reaches the left edge.
        target_right = CELL - PAD
        dx = target_right - bx1 * sx
        ext_left, ext_right = True, False
    elif role == "fina":
        # Body sits at the left of the cell, connector reaches the right edge.
        target_left = PAD
        dx = target_left - bx0 * sx
        ext_left, ext_right = False, True
    elif role == "medi":
        target_left = (CELL - bw) / 2.0
        dx = target_left - bx0 * sx
        ext_left, ext_right = True, True
    else:  # isolated: centre the whole ink, no connectors
        full_w = (x1 - x0) * sx
        dx = (CELL - full_w) / 2.0 - x0 * sx
        ext_left, ext_right = False, False
    return (sx, K, dx, 0.0, ext_left, ext_right)


def smear_glyph(glyph: Glyph, d: float) -> Glyph:
    """Crude but robust emboldening: union of the outline with copies shifted
    by a small cross.  TrueType fills with the non-zero rule, so overlapping
    contours merge without seams and counters still shrink correctly."""
    if d <= 0 or glyph.numberOfContours <= 0:
        return glyph
    coords = GlyphCoordinates(glyph.coordinates)
    end_pts = list(glyph.endPtsOfContours)
    flags = list(glyph.flags)
    new_coords = GlyphCoordinates(list(coords))
    new_end = list(end_pts)
    new_flags = list(flags)
    start = 0
    contours = []
    for e in end_pts:
        contours.append((start, e + 1)); start = e + 1
    for dx, dy in ((d, 0), (-d, 0), (0, d), (0, -d)):
        for a, b in contours:
            base = len(new_coords)
            for i in range(a, b):
                x, y = coords[i]
                new_coords.append((x + dx, y + dy))
                new_flags.append(flags[i])
            new_end.append(base + (b - a) - 1)
    g = Glyph()
    g.numberOfContours = len(new_end)
    g.coordinates = new_coords
    g.endPtsOfContours = new_end
    g.flags = bytearray(new_flags)
    g.program = ttProgram.Program()
    g.program.fromBytecode(b"")
    return g


def build_glyph(glyph_set, name: str, role: str, bold_d: float = 0.0):
    pts = glyph_points(glyph_set, name)
    sx, sy, dx, dy, ext_left, ext_right = fit_glyph(pts, role)

    # Decompose composites (Far Yekan has a few composite marks) and apply
    # the monospace transform in one pass.
    rec = DecomposingRecordingPen(glyph_set)
    glyph_set[name].draw(rec)
    pen = TTGlyphPen(None)
    rec.replay(TransformPen(pen, (sx, 0, 0, sy, dx, dy)))
    glyph = pen.glyph()
    if bold_d > 0:
        glyph = smear_glyph(glyph, bold_d)

    if ext_left or ext_right:
        coords = glyph.coordinates
        # Coordinates are in target units; the bar now lives at y = 0..90.
        band = [(i, x, y) for i, (x, y) in enumerate(coords)
                if (BAR_Y0 - 1) * K <= y <= (BAR_Y1 + 1) * K]
        if band:
            if ext_left:
                # Connector on the left: pull the far (minimum-x) end to 0.
                x_far = min(x for _, x, _ in band)
                if x_far < 0:
                    for i, x, y in band:
                        if x < 0:
                            coords[i] = (0.0, y)
                else:
                    for i, x, y in band:
                        if x <= x_far + 8:
                            coords[i] = (0.0, y)
            if ext_right:
                # Connector on the right: pull the far (maximum-x) end to 560.
                x_far = max(x for _, x, _ in band)
                if x_far > CELL:
                    for i, x, y in band:
                        if x > CELL:
                            coords[i] = (CELL, y)
                else:
                    for i, x, y in band:
                        if x >= x_far - 8:
                            coords[i] = (CELL, y)
        glyph.coordinates = coords

    # Nothing may leave the cell: trim the few units of overshoot that the
    # bar extension or the synthetic bold can leave at the edges.
    coords = glyph.coordinates
    for i, (x, y) in enumerate(coords):
        if x < 0:
            coords[i] = (0.0, y)
        elif x > CELL:
            coords[i] = (CELL, y)
    # Let connectors overlap their neighbour by two units.  Without this the
    # two antialiased cell edges meet exactly and leave a hairline seam on
    # long tatweel runs and on joined letters.
    for i, (x, y) in enumerate(coords):
        if x == 0.0:
            coords[i] = (-2.0, y)
        elif x == CELL:
            coords[i] = (CELL + 2.0, y)
    glyph.coordinates = coords
    return glyph, sx, sy, dx, dy


# --------------------------------------------------------------------------
# main build
# --------------------------------------------------------------------------

def _glyph_pts(glyf_table, name: str) -> List[Tuple[float, float]]:
    g = glyf_table[name]
    if g.numberOfContours <= 0:
        return []
    return [(float(x), float(y)) for x, y in g.coordinates]


def _anchor_parts(pts: Sequence[Tuple[float, float]]):
    """(top centre, top y, bottom centre, bottom y) of the visible letter."""
    if not pts:
        return (CELL / 2, 0.0, CELL / 2, 0.0)
    ys = [y for _, y in pts]
    y0, y1 = min(ys), max(ys)
    h = max(y1 - y0, 1.0)
    # Marks belong over the letter's upper structure (tooth, loop, dots),
    # not over the tail at the baseline.
    top = [p for p in pts if p[1] >= y1 - 0.45 * h]
    tcx = sum(p[0] for p in top) / len(top) if top else CELL / 2
    return (tcx, y1, tcx, y0)


def add_arabic_gpos(font: TTFont, arabic_bases: Sequence[str],
                    mark_above: Sequence[str], mark_below: Sequence[str]) -> None:
    """Add a MarkBasePos lookup for the Arabic glyphs.

    Ubuntu's GPOS is kept (Latin mark/mkmk); the new lookup is appended and
    exposed under an 'arab' script with the 'mark' feature.
    """
    glyf = font["glyf"]
    base_names = [g for g in arabic_bases if g in glyf and glyf[g].numberOfContours > 0]
    base_names.sort(key=lambda g: font.getGlyphID(g))
    above = sorted(mark_above, key=lambda g: font.getGlyphID(g))
    below = sorted(mark_below, key=lambda g: font.getGlyphID(g))

    gpos = font["GPOS"].table
    new_lookups = []

    def make_lookup(mark_names, down: bool):
        marks = []
        for g in mark_names:
            pts = _glyph_pts(glyf, g)
            if not pts:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            cx = (min(xs) + max(xs)) / 2.0
            my = max(ys) if down else min(ys)
            marks.append((g, cx, my))
        if not marks:
            return None
        mstart = font.getGlyphID(marks[0][0])
        mnames = [m[0] for m in marks]

        sub = otTables.MarkBasePos()
        sub.Format = 1
        cov_m = otTables.Coverage(); cov_m.glyphs = mnames; sub.MarkCoverage = cov_m
        cov_b = otTables.Coverage(); cov_b.glyphs = base_names; sub.BaseCoverage = cov_b
        sub.ClassCount = 1
        sub.MarkArray = otTables.MarkArray(); sub.MarkArray.MarkRecord = []
        for name, cx, my in marks:
            rec = otTables.MarkRecord()
            rec.Class = 0
            rec.MarkAnchor = otTables.Anchor(); rec.MarkAnchor.Format = 1
            rec.MarkAnchor.XCoordinate = int(round(cx))
            rec.MarkAnchor.YCoordinate = int(round(my))
            sub.MarkArray.MarkRecord.append(rec)
        sub.MarkArray.MarkCount = len(sub.MarkArray.MarkRecord)
        sub.BaseArray = otTables.BaseArray(); sub.BaseArray.BaseRecord = []
        for name in base_names:
            pts = _glyph_pts(glyf, name)
            tcx, ty, bcx, by = _anchor_parts(pts)
            rec = otTables.BaseRecord()
            a = otTables.Anchor(); a.Format = 1
            a.XCoordinate = int(round(bcx if down else tcx))
            a.YCoordinate = int(round((by - 18) if down else (ty + 22)))
            rec.BaseAnchor = [a]
            sub.BaseArray.BaseRecord.append(rec)
        sub.BaseArray.BaseCount = len(sub.BaseArray.BaseRecord)

        lookup = otTables.Lookup()
        lookup.LookupType = 4
        lookup.LookupFlag = 0
        lookup.SubTable = [sub]
        lookup.SubTableCount = 1
        new_lookups.append(lookup)

    make_lookup(above, down=False)
    make_lookup(below, down=True)
    if not new_lookups:
        return
    first = gpos.LookupList.LookupCount
    gpos.LookupList.Lookup.extend(new_lookups)
    gpos.LookupList.LookupCount = len(gpos.LookupList.Lookup)

    feat = otTables.FeatureRecord()
    feat.FeatureTag = "mark"
    feat.Feature = otTables.Feature()
    feat.Feature.FeatureParams = None
    feat.Feature.LookupListIndex = list(range(first, first + len(new_lookups)))
    feat.Feature.LookupCount = len(feat.Feature.LookupListIndex)
    gpos.FeatureList.FeatureRecord.append(feat)
    gpos.FeatureList.FeatureCount = len(gpos.FeatureList.FeatureRecord)

    # OpenType requires FeatureRecords sorted by tag; keep every LangSys index
    # in sync while doing so.
    records = gpos.FeatureList.FeatureRecord
    order = sorted(range(len(records)), key=lambda i: records[i].FeatureTag)
    remap = {old: new for new, old in enumerate(order)}
    gpos.FeatureList.FeatureRecord = [records[i] for i in order]
    for sr in gpos.ScriptList.ScriptRecord:
        langsys = [sr.Script.DefaultLangSys] + [l.LangSys for l in sr.Script.LangSysRecord]
        for ls in langsys:
            if ls is None:
                continue
            ls.FeatureIndex = [remap[i] for i in ls.FeatureIndex]
            if ls.ReqFeatureIndex != 0xFFFF:
                ls.ReqFeatureIndex = remap[ls.ReqFeatureIndex]
    feature_index = remap[len(records) - 1]   # the record we just appended

    script = otTables.ScriptRecord()
    script.ScriptTag = "arab"
    script.Script = otTables.Script()
    script.Script.DefaultLangSys = otTables.DefaultLangSys()
    script.Script.DefaultLangSys.ReqFeatureIndex = 0xFFFF
    script.Script.DefaultLangSys.FeatureIndex = [feature_index]
    script.Script.DefaultLangSys.FeatureCount = 1
    script.Script.LangSysRecord = []
    script.Script.LangSysCount = 0
    gpos.ScriptList.ScriptRecord.append(script)
    gpos.ScriptList.ScriptRecord.sort(key=lambda s: s.ScriptTag)
    gpos.ScriptList.ScriptCount = len(gpos.ScriptList.ScriptRecord)


def update_name_table(font: TTFont, weight_name: str):
    family = "Monospace Yekan Farsi"
    sub = weight_name
    full = f"{family} {sub}"
    ps = f"MonospaceYekanFarsi-{sub.replace(' ', '')}"
    copyright_txt = (
        "Copyright 2026. Latin: Ubuntu Sans Mono, Copyright 2011-2023 Canonical Ltd. "
        "(Ubuntu Font Licence 1.0, relicensed here under SIL OFL 1.1). "
        "Persian: Far Yekan, Copyright 2011 Abolfazl Seilsepour. "
        "Monospace conversion: Monospace Yekan Farsi project."
    )
    licence = (
        "This Font Software is licensed under the SIL Open Font License, "
        "Version 1.1. This license is available with a FAQ at: "
        "https://openfontlicense.org"
    )
    name = font["name"]
    for rec in list(name.names):
        name.removeNames(nameID=rec.nameID)
    records = {
        0: copyright_txt,
        1: family,
        2: sub,
        3: f"{full};1.000",
        4: full,
        5: "Version 1.000",
        6: ps,
        7: "Monospace Yekan Farsi is a trademark of its authors.",
        8: "Monospace Yekan Farsi project",
        9: "Monospace Yekan Farsi project",
        11: "https://github.com/AmooEbrahim/monospace-yekan-farsi",
        12: "https://github.com/AmooEbrahim/monospace-yekan-farsi",
        13: licence,
        14: "https://openfontlicense.org",
        16: family,
        17: sub,
    }
    for nid, text in records.items():
        name.setName(text, nid, 3, 1, 0x409)
        name.setName(text, nid, 1, 0, 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weight", type=int, default=400)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ascender", type=int, default=950)
    ap.add_argument("--descender", type=int, default=-430)
    args = ap.parse_args()

    ubuntu_path = os.path.join(ROOT, "sources", "UbuntuSansMono[wght].ttf")
    yekan_path = os.path.join(ROOT, "sources", "Far_Yekan.ttf")

    base = TTFont(ubuntu_path)
    instantiateVariableFont(base, {"wght": args.weight}, inplace=True)
    for tag in ("fvar", "gvar", "HVAR", "avar", "STAT", "MVAR", "cvar"):
        if tag in base:
            del base[tag]
    base["OS/2"].usWeightClass = args.weight
    base["OS/2"].fsSelection = (base["OS/2"].fsSelection & ~0x61) | (0x20 if args.weight >= 700 else 0x40)
    base["head"].macStyle = (base["head"].macStyle & ~0x01) | (0x01 if args.weight >= 700 else 0x00)

    yekan = TTFont(yekan_path)
    yglyphs = yekan.getGlyphSet()
    yorder = yekan.getGlyphOrder()
    ycmap = yekan.getBestCmap()

    # Import every Yekan glyph under an "ar." prefix.  This keeps all GSUB
    # targets (including the .notdef.* mark fragments) available and avoids
    # name collisions with Ubuntu.
    mapping = {name: ("ar." + name.lstrip(".")) for name in yorder}
    roles = classify_forms(yekan)

    order = base.getGlyphOrder()
    new_names = [mapping[n] for n in yorder]
    base.setGlyphOrder(order + new_names)

    glyf = base["glyf"]
    hmtx = base["hmtx"]
    max_y = -10_000
    min_y = 10_000
    for name in yorder:
        new = mapping[name]
        role = roles.get(name, LIGATURE_ROLES.get(name, "isol"))
        if name == "u0640":       # tatweel must stretch the whole cell
            role = "medi"
        if yekan["hmtx"][name][0] == 0:
            role = "mark"
        glyph, sx, sy, dx, dy = build_glyph(yglyphs, name, role, 14.0 if args.weight >= 700 else 0.0)
        glyph.recalcBounds(glyf)
        glyf[new] = glyph
        advance = yekan["hmtx"][name][0] * K
        if advance == 0:
            hmtx[new] = (0, 0)
        else:
            hmtx[new] = (int(round(CELL)), glyph.xMin)
        if glyph.numberOfContours:
            min_y = min(min_y, glyph.yMin)
            max_y = max(max_y, glyph.yMax)

    # --- cmap ------------------------------------------------------------
    # Far Yekan's FExx glyph names are permuted relative to the shapes they
    # actually contain (the layout tables are right, the cmap names are not).
    # Rebuild the presentation-form cmap from the Unicode names + the GSUB
    # role mapping, so a literal U+FE90 really is an initial beh.
    gsub_table = yekan["GSUB"].table
    feat_lookups = {fr.FeatureTag: fr.Feature.LookupListIndex
                    for fr in gsub_table.FeatureList.FeatureRecord}
    base_roles: Dict[str, Dict[str, str]] = {}
    for tag, role in (("init", "init"), ("medi", "medi"), ("fina", "fina")):
        for li in feat_lookups.get(tag, []):
            for st in gsub_table.LookupList.Lookup[li].SubTable:
                if gsub_table.LookupList.Lookup[li].LookupType == 7:
                    st = st.ExtSubTable
                for src, dst in getattr(st, "mapping", {}).items():
                    if src != dst:
                        base_roles.setdefault(src, {})[role] = dst

    name2cp = {}
    for cp in range(0x600, 0x700):
        try:
            name2cp[unicodedata.name(chr(cp))] = cp
        except ValueError:
            pass

    form_cmap = {}
    for cp, gname in ycmap.items():
        if not (0xFB50 <= cp <= 0xFEFC):
            continue
        nm = unicodedata.name(chr(cp), "")
        m = re.match(r"(.*?) (ISOLATED|INITIAL|MEDIAL|FINAL) FORM$", nm)
        if not m:
            continue
        base_name, form = m.group(1), m.group(2).lower()
        base_cp = name2cp.get(base_name)
        if base_cp is None or base_cp not in ycmap:
            continue
        base_glyph = ycmap[base_cp]
        if form == "isolated":
            form_cmap[cp] = base_glyph
        elif base_glyph in base_roles and form in base_roles[base_glyph]:
            form_cmap[cp] = base_roles[base_glyph][form]

    cmap = base["cmap"]
    for table in cmap.tables:
        if table.isUnicode():
            for cp, gname in ycmap.items():
                if cp in DIGIT_OVERRIDES:
                    continue
                if in_arabic_range(cp) and cp not in form_cmap:
                    table.cmap[cp] = mapping[gname]
            for cp, gname in form_cmap.items():
                table.cmap[cp] = mapping[gname]
    for cp, gname in DIGIT_OVERRIDES.items():
        if gname in base.getGlyphOrder():
            for table in cmap.tables:
                if table.isUnicode():
                    table.cmap[cp] = gname

    # --- mirrored Persian question mark --------------------------------
    # U+061F is the RTL counterpart of "?"; mirror the Ubuntu glyph.
    qpen = TTGlyphPen(None)
    base.getGlyphSet()["question"].draw(TransformPen(qpen, (-1, 0, 0, 1, CELL, 0)))
    qglyph = qpen.glyph()
    qname = "uni061F"
    base.setGlyphOrder(base.getGlyphOrder() + [qname])
    qglyph.recalcBounds(glyf)
    glyf[qname] = qglyph
    hmtx[qname] = (int(CELL), qglyph.xMin)
    for table in cmap.tables:
        if table.isUnicode():
            table.cmap[0x061F] = qname

    # --- zero width joiners --------------------------------------------
    for cp, nm in ((0x200C, "uni200C"), (0x200D, "uni200D")):
        empty = Glyph()
        empty.numberOfContours = 0
        empty.xMin = empty.yMin = empty.xMax = empty.yMax = 0
        base.setGlyphOrder(base.getGlyphOrder() + [nm])
        glyf[nm] = empty
        hmtx[nm] = (0, 0)
        for table in cmap.tables:
            if table.isUnicode():
                table.cmap[cp] = nm

    # --- layout ----------------------------------------------------------
    base["GSUB"] = build_gsub(yekan, mapping)

    # Arabic marks: give every letter form a real mark-to-base anchor.
    arabic_bases = []
    mark_above, mark_below = [], []
    for name in yorder:
        new = mapping[name]
        if yekan["hmtx"][name][0] == 0:
            g = glyf[new]
            if g.numberOfContours and g.yMax <= 0:
                mark_below.append(new)
            else:
                mark_above.append(new)
        elif new in ycmap.values() or roles.get(name) in ("init", "medi", "fina"):
            arabic_bases.append(new)
    add_arabic_gpos(base, arabic_bases, mark_above, mark_below)

    # GDEF classes for the imported glyphs.
    if "GDEF" in base:
        gdef = base["GDEF"].table
        if gdef.GlyphClassDef is None:
            gdef.GlyphClassDef = otTables.GlyphClassDef()
            gdef.GlyphClassDef.classDefs = {}
        for name in mark_above + mark_below:
            gdef.GlyphClassDef.classDefs[name] = 3
        for name in arabic_bases:
            gdef.GlyphClassDef.classDefs.setdefault(name, 1)

    # --- vertical metrics ----------------------------------------------
    os2 = base["OS/2"]
    hhea = base["hhea"]
    hhea.ascent = args.ascender
    hhea.descent = args.descender
    hhea.lineGap = 0
    os2.sTypoAscender = args.ascender
    os2.sTypoDescender = args.descender
    os2.sTypoLineGap = 0
    os2.usWinAscent = max(args.ascender + 60, int(max_y) + 10)
    os2.usWinDescent = max(-args.descender + 90, int(-min_y) + 10)
    os2.sxHeight = 518
    os2.sCapHeight = 693
    os2.xAvgCharWidth = int(CELL)
    os2.ulUnicodeRange1 |= 1 << 8   # Arabic
    os2.ulUnicodeRange2 |= 1 << 5   # Arabic Presentation Forms-A
    os2.ulUnicodeRange2 |= 1 << 6   # Arabic Presentation Forms-B

    # --- misc tables -----------------------------------------------------
    for tag in ("hdmx", "VDMX", "LTSH", "PCLT", "DSIG"):
        if tag in base:
            del base[tag]
    base["head"].unitsPerEm = UPEM
    base["head"].fontRevision = 1.0
    base["post"].isFixedPitch = 1
    base["post"].underlinePosition = -100
    base["post"].underlineThickness = 50
    update_name_table(base, "Regular" if args.weight < 700 else "Bold")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    base.save(args.out)
    print(f"wrote {args.out}  ({os.path.getsize(args.out)/1024:.1f} KiB)  "
          f"glyphs={base['maxp'].numGlyphs}  y=[{min_y},{max_y}]")


if __name__ == "__main__":
    main()
