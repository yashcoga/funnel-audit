"""Take marked-up screenshots (red box + label on the exact spot) from a list of shots.

Usage:
  python annotate.py shots.json --out DIR

shots.json is a list like:
[
  {"name": "c1-optin-date", "url": "https://site.com/join", "device": "desktop",
   "find": {"selector": "#button-abc_btn"},            # or {"text": "October 13th"} (regex, case-insensitive)
   "extend": {"selector": ".c-countdown"},              # optional: grow the box to include another element
   "label": "Date: 13 Oct (cancelled) + countdown",
   "label_pos": "auto",                                 # auto | above | below
   "pad": 120,                                          # context around the box (px), or [top, right, bottom, left]
   "full_width": false,                                 # crop the full page width instead of around the box
   "before": [{"click": "button.submit"}, {"wait": 1500}, {"tap": "a[href='#order']"}, {"scroll": 800}],
   "viewport_only": false,                              # screenshot the visible screen (after "before" steps)
   "caption_bar": "Text shown in a red bar at the bottom (viewport_only shots)",
   "box": {"x": 240, "y": 3100, "w": 960, "h": 80}      # optional: draw this exact box (page coordinates) instead of find
  }
]
"text" finds the innermost visible element whose text matches; add "nth": 1 for the second match.
Ad pixels and form submissions are blocked. Prints one line per shot; writes DIR/<name>.png and a contact sheet
DIR/_contact-sheet-N.png to look at.
"""
import argparse
import os
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CLEAR_MARKS_JS, FIND_TEXT_JS, MARK_JS, launch, new_context, prepare, read_json  # noqa: E402
from contact_sheet import make_sheets  # noqa: E402

BOX_JS = "(sel) => { const e = [...document.querySelectorAll(sel)].find(x => x.getBoundingClientRect().width > 0) || document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return {x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height}; }"


def locate(page, spec):
    if "selector" in spec:
        return page.evaluate(BOX_JS, spec["selector"])
    if "text" in spec:
        return page.evaluate(FIND_TEXT_JS, {"pattern": spec["text"], "nth": spec.get("nth", 0)})
    return None


def union(a, b):
    x = min(a["x"], b["x"]); y = min(a["y"], b["y"])
    return {"x": x, "y": y, "w": max(a["x"] + a["w"], b["x"] + b["w"]) - x, "h": max(a["y"] + a["h"], b["y"] + b["h"]) - y}


def run_steps(page, steps, device):
    for st in steps or []:
        if "wait" in st:
            page.wait_for_timeout(st["wait"])
        elif "click" in st or "tap" in st:
            loc = page.locator(st.get("click") or st.get("tap")).first
            loc.scroll_into_view_if_needed()
            (loc.tap if ("tap" in st and device == "mobile") else loc.click)()
            page.wait_for_timeout(st.get("after", 1500))
        elif "scroll" in st:
            page.evaluate("y => window.scrollTo(0, y)", st["scroll"])
            page.wait_for_timeout(600)


def shoot(browser, shot, out, cache):
    device = shot.get("device", "desktop")
    key = (shot["url"], device)
    if key not in cache or shot.get("before"):
        if key in cache:
            cache[key][0].close()
        ctx, _ = new_context(browser, device, tracking="block", block_forms=True)
        page = ctx.new_page()
        prepare(page, shot["url"])
        cache[key] = (ctx, page)
    ctx, page = cache[key]
    page.evaluate(CLEAR_MARKS_JS)
    run_steps(page, shot.get("before"), device)
    path = os.path.join(out, shot["name"] + ".png")
    if shot.get("viewport_only"):
        if shot.get("caption_bar"):
            page.evaluate("""(t) => { const l = document.createElement('div'); l.className = '__fa_mark'; l.textContent = t;
              Object.assign(l.style, {position:'fixed', left:'12px', right:'12px', bottom:'16px', background:'#e11d48', color:'#fff',
              font:'700 15px Arial, sans-serif', padding:'10px 12px', borderRadius:'8px', zIndex:2147483647, textAlign:'center'});
              document.body.appendChild(l); }""", shot["caption_bar"])
        if shot.get("find") or shot.get("box"):
            box = shot.get("box") or locate(page, shot["find"])
            if box:
                page.evaluate(MARK_JS, {"box": box, "label": shot.get("label"), "pos": shot.get("label_pos", "auto")})
        page.screenshot(path=path)
        return path, "viewport"
    box = shot.get("box") or locate(page, shot.get("find", {}))
    if not box:
        return None, f"not found: {shot.get('find')}"
    if shot.get("extend"):
        b2 = locate(page, shot["extend"])
        if b2:
            box = union(box, b2)
    lab = page.evaluate(MARK_JS, {"box": box, "label": shot.get("label"), "pos": shot.get("label_pos", "auto")})
    area = union(box, lab) if lab else box
    pad = shot.get("pad", 80)
    t, r, b, l = (pad, pad, pad, pad) if isinstance(pad, (int, float)) else pad
    W = page.evaluate("document.documentElement.clientWidth")
    H = page.evaluate("document.documentElement.scrollHeight")
    if shot.get("full_width"):
        x0, x1 = 0, W
    else:
        x0, x1 = max(0, area["x"] - l), min(W, area["x"] + area["w"] + r)
    y0, y1 = max(0, area["y"] - t), min(H, area["y"] + area["h"] + b)
    page.screenshot(path=path, full_page=True, clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})
    return path, "ok"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    shots = read_json(a.spec)
    os.makedirs(a.out, exist_ok=True)
    done = []
    with sync_playwright() as p:
        browser = launch(p)
        cache = {}
        for s in shots:
            try:
                path, status = shoot(browser, s, a.out, cache)
            except Exception as e:
                path, status = None, "ERROR " + str(e).splitlines()[0][:140]
            print(f"  {s['name']:32} {status}")
            if path:
                done.append(path)
        for ctx, _ in cache.values():
            ctx.close()
        browser.close()
    for sheet in make_sheets(done, os.path.join(a.out, "_contact-sheet")):
        print("  review:", sheet)


if __name__ == "__main__":
    main()
