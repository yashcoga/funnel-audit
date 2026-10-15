"""Scan one funnel page on desktop and/or mobile and write everything an audit needs to JSON + screenshots.

Usage:
  python scan_page.py URL --out DIR [--device desktop|mobile|both] [--allow-tracking] [--no-images]

Writes, per device:
  DIR/<page>-<device>.json         all data + automatic flags
  DIR/<page>-<device>-full.png     full-page screenshot
  DIR/<page>-<device>-part-N.png   the full page cut into readable parts (look at these!)

Automatic flags (in "flags"): invisible/low-contrast text, duplicate paragraphs, sideways scroll on mobile,
broken images, oversized images, images without width/height, dates and time zones, empty headings,
no privacy/terms links, hidden elements that contain text (leftovers), console errors.
Ad pixels are blocked by default (logged as "would have fired").
"""
import argparse
import os
import re
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fetch_size, new_context, prepare, slug, write_json, launch  # noqa: E402

COLLECT_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0.05; };
  const box = el => { const r = el.getBoundingClientRect(); return {x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(r.height)}; };
  const txt = el => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
  const meta = n => document.querySelector(`meta[name="${n}"]`)?.content || document.querySelector(`meta[property="${n}"]`)?.content || null;

  const links = [...document.querySelectorAll('a[href]')].map(a => ({text: txt(a).slice(0, 90), href: a.getAttribute('href'),
    abs: a.href, target: a.target || null, visible: vis(a), box: box(a)}));
  const buttons = [...document.querySelectorAll('button, [role=button], input[type=submit]')].map(b => ({
    text: txt(b).slice(0, 90) || b.value || '', id: b.id || null, type: b.type || null, visible: vis(b), box: box(b),
    in_form: !!b.closest('form') || (() => { let e = b; for (let i = 0; i < 5 && e; i++) { e = e.parentElement; if (e && e.querySelector('input[type=email],input[type=tel],input[name*=email i]')) return true; } return false; })()
  }));
  const inputs = [...document.querySelectorAll('input, select, textarea')].filter(i => i.type !== 'hidden').map(i => ({
    tag: i.tagName.toLowerCase(), type: i.type, name: i.name || null, placeholder: i.placeholder || null, required: i.required,
    autocomplete: i.autocomplete || null, visible: vis(i),
    label: (i.labels && i.labels[0] ? txt(i.labels[0]) : (i.closest('label') ? txt(i.closest('label')) : '')).slice(0, 200)}));
  const iframes = [...document.querySelectorAll('iframe')].map(f => ({src: (f.src || '').slice(0, 300), visible: vis(f), box: box(f)}));
  const images = [...document.querySelectorAll('img')].map(i => ({src: i.currentSrc || i.src, alt: i.alt, natural_w: i.naturalWidth,
    natural_h: i.naturalHeight, shown_w: i.clientWidth, shown_h: i.clientHeight, visible: vis(i),
    broken: i.complete && i.naturalWidth === 0 && !!(i.currentSrc || i.src), loading: i.loading || null,
    has_size_attrs: i.hasAttribute('width') && i.hasAttribute('height'), box: box(i)}));
  const headings = [...document.querySelectorAll('h1,h2,h3,h4')].map(h => ({tag: h.tagName.toLowerCase(), text: txt(h).slice(0, 160), visible: vis(h), box: box(h)}));

  // --- invisible / low-contrast text: text colour vs the first opaque background behind it
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(Number); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  // A background layer = a positioned child that paints an image over its parent's base colour (GoHighLevel puts every
  // section background in its own `div.bg.bgCover`, so the section itself reports e.g. black with no image).
  const covers = (a, b) => { const r = a.getBoundingClientRect(), t = b.getBoundingClientRect();
    return r.left <= t.left + 2 && r.right >= t.right - 2 && r.top <= t.top + 2 && r.bottom >= t.bottom - 2; };
  const layerBehind = (anc, el) => [...anc.children].some(c => { if (c.contains(el)) return false;
    const cs = getComputedStyle(c); if (!['absolute', 'fixed'].includes(cs.position)) return false;
    const paints = (cs.backgroundImage && cs.backgroundImage !== 'none') || ['IMG', 'VIDEO', 'PICTURE', 'CANVAS', 'IFRAME'].includes(c.tagName);
    return paints && covers(c, el); });
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const s = getComputedStyle(e);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;  // image/gradient behind: can't judge
      if (layerBehind(e, el)) return null;                                 // image layer behind: can't judge
      for (const ps of ['::before', '::after']) { const p = getComputedStyle(e, ps);
        if (p.content !== 'none' && p.backgroundImage !== 'none') return null; }
      const c = parse(s.backgroundColor); if (c && c.a > 0.9) return c; } return {r: 255, g: 255, b: 255, a: 1}; };
  const lowContrast = [];
  for (const el of document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,span,a,div,label')) {
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 2)) continue;
    if (!vis(el)) continue;
    const s = getComputedStyle(el); const fg = parse(s.color); if (!fg || fg.a < 0.1) continue;
    const bg = bgOf(el); if (!bg) continue;
    const L1 = lum(fg), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    if (ratio < 1.5) lowContrast.push({text: txt(el).slice(0, 120), ratio: Math.round(ratio * 100) / 100, color: s.color, box: box(el)});
  }

  // --- duplicate text blocks (same paragraph twice on the page, both visible)
  const seen = {}; const dups = [];
  for (const el of document.querySelectorAll('p, li, h1, h2, h3, h4')) {
    if (!vis(el)) continue; const t = txt(el); if (t.length < 40) continue;
    if (seen[t] !== undefined && !seen[t].contains(el) && !el.contains(seen[t])) dups.push({text: t.slice(0, 140), box: box(el)});
    else seen[t] = el;
  }

  // --- hidden elements that still contain real text (template leftovers, alternate mobile/desktop blocks)
  const hidden = [];
  for (const el of document.querySelectorAll('section, div, p, a, h1, h2, h3, form')) {
    const s = getComputedStyle(el); if (s.display !== 'none') continue;
    if (el.parentElement && getComputedStyle(el.parentElement).display === 'none') continue;
    const t = (el.textContent || '').trim().replace(/\s+/g, ' '); if (t.length < 15) continue;
    hidden.push({tag: el.tagName.toLowerCase(), cls: (el.className + '').slice(0, 60), text: t.slice(0, 160),
      links: [...el.querySelectorAll('a[href]')].map(a => a.href).slice(0, 5)});
  }

  // --- elements wider than the screen (sideways scroll)
  const W = document.documentElement.clientWidth;
  const overflow = [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect();
    return r.width > 0 && r.right > W + 2 && getComputedStyle(e).position !== 'fixed'; })
    .slice(0, 8).map(e => ({tag: e.tagName.toLowerCase(), cls: (e.className + '').slice(0, 60), right: Math.round(e.getBoundingClientRect().right), box: box(e)}));

  const nav = performance.getEntriesByType('navigation')[0] || {};
  const fcp = performance.getEntriesByName('first-contentful-paint')[0];
  return {
    url: location.href, title: document.title, lang: document.documentElement.lang || null,
    meta: {description: meta('description'), og_title: meta('og:title'), og_description: meta('og:description'),
      og_image: meta('og:image'), viewport: meta('viewport'), robots: meta('robots'),
      canonical: document.querySelector('link[rel=canonical]')?.href || null,
      favicon: document.querySelector('link[rel*=icon]')?.href || null},
    text: document.body.innerText, headings, links, buttons, inputs, iframes, images,
    low_contrast: lowContrast.slice(0, 15), duplicates: dups.slice(0, 15), hidden_with_text: hidden.slice(0, 20),
    overflow, page_width: W, scroll_width: document.documentElement.scrollWidth, page_height: document.body.scrollHeight,
    timing: {ttfb_ms: Math.round(nav.responseStart || 0), dom_ready_ms: Math.round(nav.domContentLoadedEventEnd || 0),
      load_ms: Math.round(nav.loadEventEnd || 0), fcp_ms: fcp ? Math.round(fcp.startTime) : null,
      requests: performance.getEntriesByType('resource').length},
    scripts: [...document.scripts].map(s => s.src).filter(Boolean).map(s => s.slice(0, 140)),
  };
}
"""

MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE_RE = re.compile(rf"(?:(?:Mon|Tues?|Wed(?:nes)?|Thu(?:rs)?|Fri|Sat(?:ur)?|Sun)(?:day)?,?\s+)?{MONTHS}\.?\s+\d{{1,2}}(?!\d)(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?(?:[^\n]{{0,30}}?\d{{1,2}}(?::\d\d)?\s*(?:am|pm|AM|PM)\s*(?:[ECMP][SD]?T)?)?", re.I)
TZ_RE = re.compile(r"\b\d{1,2}(?::\d\d)?\s*(?:am|pm)\s*([ECMP][SD]T|ET|CT|MT|PT)\b", re.I)


def split_screenshot(path, device, out_prefix):
    """Cut a tall screenshot into parts that are readable when viewed."""
    from PIL import Image
    im = Image.open(path)
    w, h = im.size
    parts = []
    if device == "mobile":
        strip = 2400
        strips = [im.crop((0, y, w, min(y + strip, h))) for y in range(0, h, strip)]
        for i in range(0, len(strips), 4):
            grp = strips[i:i + 4]
            sheet = Image.new("RGB", (w * len(grp) + 20 * (len(grp) - 1), strip), "white")
            for j, s in enumerate(grp):
                sheet.paste(s, (j * (w + 20), 0))
            sheet = sheet.resize((sheet.width // 2, sheet.height // 2))
            p = f"{out_prefix}-part-{len(parts) + 1}.png"
            sheet.save(p)
            parts.append(p)
    else:
        strip = 1800
        for y in range(0, h, strip):
            p = f"{out_prefix}-part-{len(parts) + 1}.png"
            im.crop((0, y, w, min(y + strip, h))).save(p)
            parts.append(p)
    return parts


def scan(browser, url, device, out, allow_tracking=False, weigh_images=True):
    ctx, log = new_context(browser, device, tracking="observe" if allow_tracking else "block", block_forms=True)
    page = ctx.new_page()
    console = []
    page.on("console", lambda m: console.append(f"{m.type}: {m.text[:200]}") if m.type in ("error",) else None)
    failed = []
    page.on("requestfailed", lambda r: failed.append(f"{r.url[:140]} ({r.failure})") if "facebook.com/tr" not in r.url else None)
    bad_status = []
    page.on("response", lambda r: bad_status.append(f"{r.status} {r.url[:140]}") if r.status >= 400 else None)
    nav = prepare(page, url)
    data = page.evaluate(COLLECT_JS)
    name = f"{slug(url)}-{device}"
    shot = os.path.join(out, f"{name}-full.png")
    page.screenshot(path=shot, full_page=True)
    data["screenshots"] = {"full": shot, "parts": split_screenshot(shot, device, os.path.join(out, name))}
    data["device"] = device
    data["requested_url"] = url
    data["http"] = nav
    data["console_errors"] = console[:30]
    data["failed_requests"] = failed[:30]
    data["error_responses"] = bad_status[:30]
    data["tracking"] = log.events
    data["tracking_summary"] = log.summary()
    ctx.close()

    # ---- image weights (largest first)
    if weigh_images:
        seen = {}
        for img in data["images"]:
            src = img["src"]
            if not src or src.startswith("data:"):
                continue
            if src not in seen:
                seen[src] = fetch_size(src)
            size, ctype, status = seen[src]
            img["bytes"] = size
            img["content_type"] = ctype
    # ---- flags
    flags = []
    t = data["text"]
    dates = sorted(set(m.group(0).strip() for m in DATE_RE.finditer(t)))
    if dates:
        flags.append({"type": "dates_found", "severity": "check", "detail": dates[:20],
                      "why": "Compare every date/time with the live event schedule (marketing sheet)."})
    tzs = sorted(set(m.group(1).upper() for m in TZ_RE.finditer(t)))
    if "EST" in tzs or "CST" in tzs or "PST" in tzs:
        flags.append({"type": "standard_time_zone", "severity": "polish", "detail": tzs,
                      "why": "EST/CST/PST are winter-only; between March and November use ET/CT/PT (or EDT/CDT/PDT)."})
    for lc in data["low_contrast"]:
        flags.append({"type": "invisible_text", "severity": "high", "detail": lc,
                      "why": "Text colour is (almost) the same as the background, so visitors can't see it."})
    for d in data["duplicates"]:
        flags.append({"type": "duplicate_text", "severity": "polish", "detail": d, "why": "Same paragraph appears twice."})
    if device == "mobile" and (data["scroll_width"] > data["page_width"] + 1 or data["overflow"]):
        flags.append({"type": "sideways_scroll", "severity": "medium", "detail": data["overflow"][:5],
                      "why": f"Page is {data['scroll_width']}px wide on a {data['page_width']}px screen."})
    for img in data["images"]:
        if img["broken"]:
            flags.append({"type": "broken_image", "severity": "high", "detail": img["src"]})
        b = img.get("bytes") or 0
        if img["visible"] and img["natural_w"] and img["shown_w"] and img["natural_w"] > 2.2 * img["shown_w"] and b > 80000:
            flags.append({"type": "oversized_image", "severity": "medium",
                          "detail": {"src": img["src"], "kb": b // 1024, "natural_w": img["natural_w"], "shown_w": img["shown_w"]},
                          "why": "Image is far larger than it's shown; resize to about 2x the shown width and use WebP."})
        elif b > 300000:
            flags.append({"type": "heavy_image", "severity": "medium",
                          "detail": {"src": img["src"], "kb": b // 1024, "type": img.get("content_type")},
                          "why": "Over 300 KB; convert to WebP/compress."})
    unsized = [i["src"] for i in data["images"] if i["visible"] and not i["has_size_attrs"]]
    if unsized:
        flags.append({"type": "images_without_size", "severity": "check", "detail": len(unsized),
                      "why": "Images without width/height can cause layout shift (CLS) while loading."})
    for h in data["headings"]:
        if not h["text"] and h["visible"]:
            flags.append({"type": "empty_heading", "severity": "polish", "detail": h["box"]})
            break
    link_text = " ".join((l["text"] + " " + (l["href"] or "")).lower() for l in data["links"] if l["visible"])
    if "privacy" not in link_text:
        flags.append({"type": "no_privacy_link", "severity": "critical",
                      "why": "No visible Privacy Policy link (Meta requires one on pages that collect data)."})
    if "terms" not in link_text:
        flags.append({"type": "no_terms_link", "severity": "medium", "why": "No visible Terms link."})
    for hd in data["hidden_with_text"]:
        ext = [u for u in hd["links"] if u and not u.startswith(("javascript", "#"))]
        flags.append({"type": "hidden_element_with_text", "severity": "check", "detail": hd,
                      "why": "Hidden in the browser (visitors don't see it). Often a mobile/desktop twin or a template leftover; "
                             "only report it if it's a leftover" + (f"; links: {ext}" if ext else "") + "."})
    if console:
        flags.append({"type": "console_errors", "severity": "check", "detail": console[:5]})
    if bad_status:
        flags.append({"type": "error_responses", "severity": "check", "detail": bad_status[:8]})
    data["flags"] = flags
    path = write_json(os.path.join(out, f"{name}.json"), data)
    return path, data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="both", choices=["desktop", "mobile", "both"])
    ap.add_argument("--allow-tracking", action="store_true", help="let ad pixels fire (default: blocked and logged)")
    ap.add_argument("--no-images", action="store_true", help="skip downloading images to measure their weight")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    devices = ["desktop", "mobile"] if a.device == "both" else [a.device]
    with sync_playwright() as p:
        browser = launch(p)
        for d in devices:
            path, data = scan(browser, a.url, d, a.out, a.allow_tracking, not a.no_images)
            vis_links = [l for l in data["links"] if l["visible"]]
            vis_btns = [b for b in data["buttons"] if b["visible"]]
            print(f"\n== {d}: {data['url']}  (HTTP {data['http']['status']}, title: {data['title']!r})")
            print(f"   json: {path}")
            print(f"   screenshots: {len(data['screenshots']['parts'])} parts, e.g. {data['screenshots']['parts'][0]}")
            print(f"   visible links: {len(vis_links)}, visible buttons: {len(vis_btns)}, images: {len(data['images'])}, "
                  f"iframes: {len(data['iframes'])}, FCP: {data['timing']['fcp_ms']} ms")
            print(f"   tracking (blocked={not a.allow_tracking}): {', '.join(data['tracking_summary'][:12]) or 'none'}")
            for f in data["flags"]:
                det = f.get("detail")
                det = (str(det)[:150] + "...") if det is not None and len(str(det)) > 150 else det
                print(f"   FLAG [{f['severity']}] {f['type']}: {det if det is not None else ''}")
        browser.close()


if __name__ == "__main__":
    main()
