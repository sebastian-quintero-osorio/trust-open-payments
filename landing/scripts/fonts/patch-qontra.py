"""Add Spanish glyphs to Qontra Regular (Tomaz Hrastar) without altering existing outlines.

Adds: á é í ó ú Á É Í Ó Ú ñ Ñ ¿ ¡ ‘ ’ ” ' " ·
Accents reuse the font's own acute (from cacute / Cacute). The tilde is drawn in the
font's accent construction: vertical cuts and 67-unit vertical thickness (same as the
acute and caron). Kerning of each base glyph is copied to its accented variants.
"""
import math
import sys

from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

src, out_otf, out_woff2 = sys.argv[1:4]
font = TTFont(src)
font.flavor = None
gs = font.getGlyphSet()
cff = font["CFF "].cff
top = cff.topDictIndex[0]
hmtx = font["hmtx"]
CAP_SHIFT = 290  # caps accents sit 290 units above lowercase ones (cacute vs Cacute)
THICK = 67


def contours(name):
    rec = RecordingPen()
    gs[name].draw(rec)
    out, cur = [], []
    for op, args in rec.value:
        cur.append((op, args))
        if op in ("closePath", "endPath"):
            out.append(cur)
            cur = []
    return out


def bounds_of(cs):
    pts = [p for c in cs for _, args in c for p in args]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def glyph_bounds(name):
    bp = BoundsPen(gs)
    gs[name].draw(bp)
    return bp.bounds


def replay(cs, pen, t=Transform()):
    tp = TransformPen(pen, t)
    for c in cs:
        for op, args in c:
            getattr(tp, op)(*args)


def tilde_contour(cx, cy, width, amp):
    """Closed polygon: band between centerline ± THICK/2 (vertical offset), vertical end cuts."""
    n = 32
    x0 = cx - width / 2
    pts_top, pts_bot = [], []
    for i in range(n + 1):
        t = i / n
        x = x0 + width * t
        y = cy + amp * math.sin(2 * math.pi * t)
        pts_top.append((round(x, 2), round(y + THICK / 2, 2)))
        pts_bot.append((round(x, 2), round(y - THICK / 2, 2)))
    poly = pts_bot + list(reversed(pts_top))
    c = [("moveTo", (poly[0],))]
    c += [("lineTo", (p,)) for p in poly[1:]]
    c.append(("closePath", ()))
    return [c]


acute_lc = [c for c in contours("cacute") if bounds_of([c])[1] > 450]
acute_uc = [c for c in contours("Cacute") if bounds_of([c])[1] > 740]
assert len(acute_lc) == 1 and len(acute_uc) == 1

new_glyphs = {}  # name -> (contour list, advance, lsb-calc later, kerning base or None)


def add(name, cs, advance, kern_base=None):
    new_glyphs[name] = (cs, advance, kern_base)


def accented(base, accent_cs, name, base_cs=None, center=None):
    bcs = base_cs if base_cs is not None else contours(base)
    bx0, _, bx1, _ = bounds_of(bcs)
    ax0, _, ax1, _ = bounds_of(accent_cs)
    target = center if center is not None else (bx0 + bx1) / 2
    dx = target - (ax0 + ax1) / 2
    moved = []
    rec = RecordingPen()
    replay(accent_cs, rec, Transform().translate(dx, 0))
    moved = [rec.value]
    add(name, bcs + moved, hmtx[base][0], kern_base=base)


for base in "aeou":
    accented(base, acute_lc, base + "acute")
for base in "AEIOU":
    accented(base, acute_uc, base + "acute")

# í: dotless stem of i (drop the dot contour) + acute centered on the stem
i_cs = contours("i")
stem = [c for c in i_cs if bounds_of([c])[1] < 10]
sx0, _, sx1, _ = bounds_of(stem)
accented("i", acute_lc, "iacute", base_cs=stem, center=(sx0 + sx1) / 2)

# ñ Ñ: tilde centered on the lowercase/caps accent band (acute centers: 521 / 811.5)
for base, cy, width, amp in (("n", 521, 196, 21), ("N", 521 + CAP_SHIFT, 232, 23)):
    bx0, _, bx1, _ = glyph_bounds(base)
    cs = contours(base) + tilde_contour((bx0 + bx1) / 2, cy, width, amp)
    add(base + "tilde", cs, hmtx[base][0], kern_base=base)


def rotated_180(name, new_name, top_y):
    cs = contours(name)
    x0, y0, x1, y1 = glyph_bounds(name)
    cx = (x0 + x1) / 2
    # rotate around (cx, 0), then lift so the rotated glyph's top sits at top_y
    t = Transform().translate(cx, 0).rotate(math.pi).translate(-cx, 0)
    rec = RecordingPen()
    replay(cs, rec, t)
    # after rotation, y range is [-y1, -y0]; shift so top (-y0) -> top_y
    dy = top_y - (-y0)
    rec2 = RecordingPen()
    for op, args in rec.value:
        getattr(rec2, op)(*[(x, y + dy) for x, y in args])
    add(new_name, [rec2.value], hmtx[name][0], kern_base=None)


rotated_180("question", "questiondown", 470)
rotated_180("exclam", "exclamdown", 470)


def shifted(name, new_name, dx, dy, advance=None, only=None):
    cs = contours(name)
    if only is not None:
        cs = [cs[i] for i in only]
    rec = RecordingPen()
    replay(cs, rec, Transform().translate(dx, dy))
    add(new_name, [rec.value], advance if advance is not None else hmtx[name][0])


# ’ ” are the comma / „ raised to the opening-quote band (623..829), as in the font's “
shifted("comma", "quoteright", 0, 720)
shifted("quotedblbase", "quotedblright", 0, 720)
# ‘ is the first mark of “
shifted("quotedblleft", "quoteleft", 0, 0, advance=hmtx["comma"][0], only=[0])
# · is the period lifted to half the x-height
px0, py0, px1, py1 = glyph_bounds("period")
shifted("period", "periodcentered", 0, 210 - (py0 + py1) / 2)


def rect(x0, y0, x1, y1):
    return [[("moveTo", ((x0, y0),)), ("lineTo", ((x1, y0),)), ("lineTo", ((x1, y1),)),
             ("lineTo", ((x0, y1),)), ("closePath", ())]]


# straight quotes: stems of accent thickness, opening-quote band height
add("quotesingle", rect(50, 600, 50 + THICK, 730), 50 + THICK + 50)
add("quotedbl", rect(50, 600, 50 + THICK, 730) + rect(50 + THICK + 50, 600, 50 + 2 * THICK + 50, 730),
    50 + 2 * THICK + 100)

UNICODES = {
    "aacute": 0xE1, "eacute": 0xE9, "iacute": 0xED, "oacute": 0xF3, "uacute": 0xFA,
    "Aacute": 0xC1, "Eacute": 0xC9, "Iacute": 0xCD, "Oacute": 0xD3, "Uacute": 0xDA,
    "ntilde": 0xF1, "Ntilde": 0xD1, "questiondown": 0xBF, "exclamdown": 0xA1,
    "quoteleft": 0x2018, "quoteright": 0x2019, "quotedblright": 0x201D,
    "periodcentered": 0xB7, "quotesingle": 0x27, "quotedbl": 0x22,
}
assert set(UNICODES) == set(new_glyphs), set(UNICODES) ^ set(new_glyphs)

order = font.getGlyphOrder()
assert top.charset is order
cs_index = top.CharStrings
for name, (cs, advance, _) in new_glyphs.items():
    pen = T2CharStringPen(advance, None)
    for c in cs:
        for op, args in c:
            getattr(pen, op)(*[(round(x), round(y)) for x, y in args])
    charstring = pen.getCharString(private=top.Private, globalSubrs=cff.GlobalSubrs)
    cs_index.charStringsIndex.append(charstring)
    cs_index.charStrings[name] = len(cs_index.charStringsIndex) - 1
    order.append(name)  # top.charset is the same list object as the glyph order
    x0, _, _, _ = bounds_of(cs)
    hmtx[name] = (advance, round(x0))

font.setGlyphOrder(order)
for table in font["cmap"].tables:
    if table.isUnicode():
        for name, cp in UNICODES.items():
            table.cmap[cp] = name

# copy kerning from base glyphs to accented variants (PairPos format 1)
gpos = font["GPOS"].table
for lookup in gpos.LookupList.Lookup:
    if lookup.LookupType != 2:
        continue
    for st in lookup.SubTable:
        if st.Format != 1:
            continue
        cov = st.Coverage.glyphs
        # as second glyph
        for ps in st.PairSet:
            extra = []
            for rec in ps.PairValueRecord:
                for name, (_, _, kb) in new_glyphs.items():
                    if kb == rec.SecondGlyph:
                        import copy
                        r2 = copy.deepcopy(rec)
                        r2.SecondGlyph = name
                        extra.append(r2)
            ps.PairValueRecord.extend(extra)
            ps.PairValueRecord.sort(key=lambda r: font.getGlyphID(r.SecondGlyph))
            ps.PairValueCount = len(ps.PairValueRecord)
        # as first glyph
        import copy
        for name, (_, _, kb) in new_glyphs.items():
            if kb in cov:
                ps = copy.deepcopy(st.PairSet[cov.index(kb)])
                cov.append(name)
                st.PairSet.append(ps)
        pairs = sorted(zip(cov, st.PairSet), key=lambda p: font.getGlyphID(p[0]))
        st.Coverage.glyphs = [p[0] for p in pairs]
        st.PairSet = [p[1] for p in pairs]
        st.PairSetCount = len(st.PairSet)

for rec in font["name"].names:
    if rec.nameID == 5:
        rec.string = "Version 1.001; Trust Spanish glyph extension (á é í ó ú ñ ¿ ¡ ’ ”)"

font.save(out_otf)
font.flavor = "woff2"
font.save(out_woff2)
print("added", len(new_glyphs), "glyphs; total", len(font.getGlyphOrder()))
