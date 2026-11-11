"""Scan one funnel page on desktop and/or mobile and write everything an audit needs to JSON + screenshots.

Usage:
  python scan_page.py URL [URL ...] --out DIR [--device desktop|mobile|both] [--allow-tracking] [--no-images] [--jobs 4]

Several URLs are scanned at the same time (one process per page, --jobs at once). Image sizes are fetched in parallel.

Writes, per device:
  DIR/<page>-<device>.json         all data + automatic flags
  DIR/<page>-<device>-full.png     full-page screenshot
  DIR/<page>-<device>-part-N.png   the full page cut into readable parts (look at these!)

Automatic flags (in "flags"): invisible/low-contrast text, duplicate paragraphs, sideways scroll on mobile,
broken images, oversized images, images without width/height, dates and time zones, empty headings,
no privacy/terms links, hidden elements that contain text (leftovers), console errors.
Design flags (the "design" block in the JSON): first button/form below the first screen, headline too long,
one word alone on a last line, button text wrapping on mobile, body text under 16px on mobile, blurry images,
more than 3 font families.
Ad pixels are blocked by default (logged as "would have fired").
"""
import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fetch_size, full_page_screenshot, new_context, prepare, slug, write_json, launch  # noqa: E402

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
  // inside a swipe row / clipped box (overflow-x not visible) = not page-level sideways scroll
  const clipped = e => { for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) if (getComputedStyle(p).overflowX !== 'visible') return true; return false; };
  const overflow = [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect();
    return r.width > 0 && r.right > W + 2 && getComputedStyle(e).position !== 'fixed' && !e.closest('svg') && !clipped(e); })
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

# Design measurements (shared design checklist with page-design-review): first screen, headline length, small text,
# lone last-line words, wrapping buttons, fonts in use. Runs after the screenshot.
DESIGN_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0.05; };
  const top = el => Math.round(el.getBoundingClientRect().top + scrollY);
  const txt = el => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
  const vh = innerHeight, pageH = document.body.scrollHeight;
  const isBtnLink = a => /btn|button|cta/i.test((a.className || '') + '') || getComputedStyle(a).display.includes('block') && parseFloat(getComputedStyle(a).paddingTop) >= 8;
  const ctaEls = [...document.querySelectorAll('button, [role=button], input[type=submit], a[href]')]
    .filter(e => vis(e) && (e.tagName !== 'A' || isBtnLink(e)) && txt(e || '').length + (e.value || '').length > 1);
  const formFrames = [...document.querySelectorAll('iframe')].filter(f => vis(f) && /widget\/(form|survey)|leadconnector|msgsndr|form/i.test(f.src || ''));
  const cands = [...ctaEls.map(e => ({y: top(e), text: (txt(e) || e.value || '').slice(0, 60)})),
                 ...formFrames.map(f => ({y: top(f), text: '(form in a frame)'}))].filter(c => c.y >= 0).sort((a, b) => a.y - b.y);
  const firstAction = cands[0] || null;

  // per-word line positions: is the last line a single word? how many lines?
  const lines = el => { const words = []; const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) { const re = /\S+/g; let m;
      while ((m = re.exec(n.textContent))) { const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
        const rc = r.getClientRects()[0]; if (rc && rc.width > 0) words.push({w: m[0], t: Math.round(rc.top)}); } }
    const tops = []; for (const x of words) if (!tops.some(t => Math.abs(t - x.t) < 4)) tops.push(x.t);
    const last = words.length ? words[words.length - 1] : null;
    const onLast = last ? words.filter(x => Math.abs(x.t - last.t) < 4) : [];
    return {n: tops.length, words: words.length, lastLine: onLast.map(x => x.w).join(' '), lastTop: last ? Math.round(last.t + scrollY) : 0}; };

  const heads = [...document.querySelectorAll('h1,h2,h3')].filter(vis).map(h => {
    const cs = getComputedStyle(h); const L = lines(h);
    return {tag: h.tagName.toLowerCase(), y: top(h), size: cs.fontSize, lines: L.n, text: txt(h).slice(0, 100)}; });
  // main headline = the biggest-font heading on the first screen (GHL often uses h2/p for the headline)
  const firstScreen = [...document.querySelectorAll('h1,h2,h3,p,div')].filter(e => vis(e) && top(e) < vh &&
    [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 8));
  let headline = null;
  for (const e of firstScreen) { const s = parseFloat(getComputedStyle(e).fontSize);
    if (!headline || s > headline.px) headline = {px: s, el: e}; }
  const hl = headline ? {text: txt(headline.el).slice(0, 120), size: Math.round(headline.px) + 'px', y: top(headline.el), lines: lines(headline.el).n} : null;

  // lone word on the last line (headings, paragraphs, list items, buttons)
  const lone = [], wrapBtns = [], small = [], seenLone = new Set();
  const blocks = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,button,a,label,span,div')].filter(e => vis(e) &&
    [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1));
  const legal = /privacy|terms|disclaimer|©|copyright|rights reserved|not (a )?guarantee|results (may|will) vary|facebook|meta platforms/i;
  for (const e of blocks) {
    const t = txt(e); if (t.length < 3) continue;
    const L = lines(e);
    const btn = e.matches('button,[role=button]') || (e.tagName === 'A' && isBtnLink(e));
    const key = L.lastLine + '@' + L.lastTop;
    if (L.n >= 2 && L.words >= 2 && !L.lastLine.includes(' ') && /[A-Za-z0-9]{2,}/.test(L.lastLine) && !seenLone.has(key)) {
      seenLone.add(key); lone.push({text: t.slice(0, 120), last_word: L.lastLine, tag: e.tagName.toLowerCase(), y: top(e)}); }
    if (btn && L.n >= 2 && t.length < 60) wrapBtns.push({text: t, lines: L.n, y: top(e)});
    const fs = parseFloat(getComputedStyle(e).fontSize);
    if (fs < 16 && t.length > 60 && !legal.test(t) && top(e) < pageH - 900 && !e.closest('footer,form,label'))
      small.push({text: t.slice(0, 90), size: fs + 'px', y: top(e)});
  }
  const fonts = {};
  for (const e of blocks) { const f = getComputedStyle(e).fontFamily.split(',')[0].replace(/["']/g, '').trim(); fonts[f] = (fonts[f] || 0) + 1; }
  const dedupe = arr => { const s = new Set(); return arr.filter(x => { const k = x.text.slice(0, 50); if (s.has(k)) return false; s.add(k); return true; }); };
  return {first_screen_h: vh, first_action: firstAction, headline: hl, headings: heads.slice(0, 40),
          lone_words: dedupe(lone).slice(0, 25), wrapping_buttons: dedupe(wrapBtns).slice(0, 10),
          small_text: dedupe(small).slice(0, 10), fonts};
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
    full_page_screenshot(page, shot)
    data["screenshots"] = {"full": shot, "parts": split_screenshot(shot, device, os.path.join(out, name))}
    try:
        page.evaluate("window.scrollTo(0, 0)")
        data["design"] = page.evaluate(DESIGN_JS)
    except Exception as e:  # never let the design pass break the scan
        data["design"] = {"error": str(e)[:200]}
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
        srcs = sorted({i["src"] for i in data["images"] if i["src"] and not i["src"].startswith("data:")})
        with ThreadPoolExecutor(max_workers=8) as ex:
            seen = dict(zip(srcs, ex.map(fetch_size, srcs)))
        for img in data["images"]:
            if img["src"] in seen:
                size, ctype, status = seen[img["src"]]
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
        if (img["visible"] and img["natural_w"] and img["shown_w"] >= 120 and not img["broken"]
                and ".svg" not in (img["src"] or "").lower() and img["natural_w"] < 0.9 * img["shown_w"]):
            flags.append({"type": "blurry_image", "severity": "medium",
                          "detail": {"src": img["src"][-90:], "natural_w": img["natural_w"], "shown_w": img["shown_w"]},
                          "why": "Image file is narrower than the space it fills, so it looks soft. Upload one at least 2x the shown width."})
    # ---- design measurements (see the "Design review" step in SKILL.md)
    dz = data.get("design") or {}
    fa, vh = dz.get("first_action"), dz.get("first_screen_h") or 0
    if vh:
        limit = vh if device == "desktop" else int(vh * 1.5)
        if not fa:
            flags.append({"type": "no_cta_found", "severity": "check",
                          "why": "No button or form found; check the screenshot (pop-up or custom element?)."})
        elif fa["y"] > limit:
            flags.append({"type": "cta_below_first_screen", "severity": "high" if device == "desktop" else "medium",
                          "detail": fa, "why": f"First button/form starts at {fa['y']}px; the first screen is {vh}px tall"
                          + (" (on a phone it should be within about 1.5 screens)." if device == "mobile" else ".")})
    hl = dz.get("headline")
    if hl and hl["lines"] > (3 if device == "desktop" else 4):
        flags.append({"type": "headline_too_long", "severity": "medium", "detail": hl,
                      "why": f"Main headline runs {hl['lines']} lines at {hl['size']}; aim for "
                             + ("3 or fewer on desktop." if device == "desktop" else "4 or fewer on a phone.")})
    if dz.get("lone_words"):
        flags.append({"type": "lone_word_last_line", "severity": "polish", "detail": dz["lone_words"][:8],
                      "why": f"{len(dz['lone_words'])} text block(s) end with one word alone on the last line (team rule: never). "
                             "Fix with text-wrap: balance/pretty or &nbsp; between the last two words."})
    if device == "mobile" and dz.get("wrapping_buttons"):
        flags.append({"type": "button_text_wraps", "severity": "polish", "detail": dz["wrapping_buttons"][:5],
                      "why": "Button text breaks onto 2+ lines on a phone; shorten it or reduce the size."})
    if device == "mobile" and dz.get("small_text"):
        flags.append({"type": "small_body_text", "severity": "medium", "detail": dz["small_text"][:5],
                      "why": "Body text under 16px on a phone is hard to read (footer/legal lines excluded)."})
    fams = [f for f, n in (dz.get("fonts") or {}).items() if n >= 3]
    if len(fams) > 3:
        flags.append({"type": "many_fonts", "severity": "polish", "detail": fams,
                      "why": "More than 3 font families in use; check against the client's design system."})
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
    ap.add_argument("urls", nargs="+", metavar="URL")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4, help="pages scanned at the same time (default 4)")
    ap.add_argument("--device", default="both", choices=["desktop", "mobile", "both"])
    ap.add_argument("--allow-tracking", action="store_true", help="let ad pixels fire (default: blocked and logged)")
    ap.add_argument("--no-images", action="store_true", help="skip downloading images to measure their weight")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if len(a.urls) > 1 and a.jobs > 1:
        # one process per page (Playwright's sync API can't be shared across threads)
        t0, running, queue = time.time(), [], list(a.urls)
        extra = ["--device", a.device] + (["--allow-tracking"] if a.allow_tracking else []) + (["--no-images"] if a.no_images else [])
        while queue or running:
            while queue and len(running) < a.jobs:
                u = queue.pop(0)
                log = open(os.path.join(a.out, f"scan-{slug(u)}.log"), "w+", encoding="utf-8", errors="replace")
                env = dict(os.environ, PYTHONIOENCODING="utf-8")
                cmd = [sys.executable, os.path.abspath(__file__), u, "--out", a.out, "--jobs", "1"] + extra
                running.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env), log))
            for pr, log in running[:]:
                if pr.poll() is not None:
                    log.seek(0)
                    print(log.read(), end="", flush=True)
                    log.close()
                    running.remove((pr, log))
            time.sleep(0.3)
        print(f"\nDone: {len(a.urls)} pages in {time.time() - t0:.0f}s")
        return
    a.url = a.urls[0]
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
            dz = data.get("design") or {}
            if dz.get("first_screen_h"):
                fa, hl = dz.get("first_action"), dz.get("headline")
                print(f"   design: first screen {dz['first_screen_h']}px, first button/form at "
                      f"{fa['y'] if fa else 'none'}px{' (' + fa['text'] + ')' if fa else ''}, headline "
                      f"{(str(hl['lines']) + ' lines at ' + hl['size']) if hl else '?'}, fonts: "
                      f"{', '.join(f for f, n in dz.get('fonts', {}).items() if n >= 3)}")
            for f in data["flags"]:
                det = f.get("detail")
                det = (str(det)[:150] + "...") if det is not None and len(str(det)) > 150 else det
                print(f"   FLAG [{f['severity']}] {f['type']}: {det if det is not None else ''}")
        browser.close()


if __name__ == "__main__":
    main()
