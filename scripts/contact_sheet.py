"""Stack screenshots into a few review images (so they can be checked in one look).

Usage:  python contact_sheet.py OUT_PREFIX img1.png img2.png ...
Writes OUT_PREFIX-1.png, OUT_PREFIX-2.png ... (about 4 images each, 900 px wide).
"""
import sys

from PIL import Image, ImageDraw


def make_sheets(paths, prefix, per_sheet=4, width=900):
    out = []
    ims = []
    for p in paths:
        try:
            im = Image.open(p).convert("RGB")
        except Exception:
            continue
        if im.width > width:
            im = im.resize((width, int(im.height * width / im.width)))
        ims.append((p, im))
    for i in range(0, len(ims), per_sheet):
        grp = ims[i:i + per_sheet]
        h = sum(im.height + 34 for _, im in grp)
        sheet = Image.new("RGB", (width, h), "white")
        d = ImageDraw.Draw(sheet)
        y = 0
        for p, im in grp:
            d.text((6, y + 8), p.replace("\\", "/").split("/")[-1], fill=(200, 0, 40))
            sheet.paste(im, (0, y + 28))
            y += im.height + 34
        path = f"{prefix}-{i // per_sheet + 1}.png"
        sheet.save(path)
        out.append(path)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    for s in make_sheets(sys.argv[2:], sys.argv[1]):
        print(s)
