"""Funnel steps, wrong-page links and SEO meta tags, from the funnel's own step list.

Usage:
  python check_funnel_steps.py START_URL --out DIR [--ctas DIR/ctas]

GoHighLevel puts the funnel's full step list (every page path) in each page's data. This script reads it from the
start page, opens every step (pixels blocked) and checks:

  1. Steps: every page of THIS funnel, including steps nothing links to (leftover or test pages) and steps that are
     not published (GHL sends them back to the first step).
  2. Wrong-page links: every button/link found by test_ctas.py (--ctas) that lands on the same domain but on a page
     that is NOT a step of this funnel, e.g. an old upsell from the previous funnel with the old design. "It
     navigates" is not enough: the destination must be the right page of this funnel.
  3. SEO / share meta tags per step: title, meta description, og:title / og:description / og:image, canonical,
     favicon, noindex. Flags missing tags, titles or descriptions copied across steps, titles that describe a
     different page (e.g. the opt-in title on an upsell), auto-generated slugs (optin-page-1109,
     upsell-page-3387-1294), and offer / thank-you pages that are indexable by Google.

Writes DIR/funnel-steps.json with a ready "funnel_steps" block for findings.json.
"""
import argparse
import glob
import json
import os
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import launch, new_context, prepare, write_json  # noqa: E402

LEGAL_RE = re.compile(r"terms|privacy|refund|policy|disclaimer|cookie|support|contact|legal", re.I)
AUTO_SLUG_RE = re.compile(r"(^|-)(page|step|optin|upsell|downsell|thank-you)-?\d{3,}(-\d+)*$|-\d{4,}$", re.I)
STEPS_JS = r"""
() => {
  const nd = document.getElementById('__NUXT_DATA__');
  if (!nd) return [];
  let arr; try { arr = JSON.parse(nd.textContent); } catch (e) { return []; }
  return [...new Set(arr.filter(v => typeof v === 'string' && /^\/[a-z0-9][a-z0-9-]{1,80}$/i.test(v)))];
}
"""
PAGE_JS = r"""
() => {
  const m = n => document.querySelector(`meta[name="${n}"]`)?.content || document.querySelector(`meta[property="${n}"]`)?.content || null;
  const h = [...document.querySelectorAll('h1, h2')].map(e => e.innerText.trim().replace(/\s+/g, ' ')).filter(Boolean);
  const nd = document.getElementById('__NUXT_DATA__');
  return {title: document.title, description: m('description'), og_title: m('og:title'), og_description: m('og:description'),
          og_image: m('og:image'), robots: m('robots'), canonical: document.querySelector('link[rel=canonical]')?.href || null,
          favicon: document.querySelector('link[rel~=icon]')?.href || null, headline: (h[0] || '').slice(0, 140),
          words: (document.body.innerText.match(/\S+/g) || []).length, data: nd ? nd.textContent.slice(0, 400000) : ''};
}
"""


def kind_of(path, info):
    p = path.lower()
    t = " ".join([info.get("headline") or "", info.get("title") or ""]).lower()
    if LEGAL_RE.search(p):
        return "legal"
    if re.search(r"confirm|thank|access-granted|success|welcome", p) or re.search(r"you're in|confirmed|thank you", t):
        return "thank-you"
    if re.search(r"down|special|deal|last-chance", p):
        return "downsell"
    if re.search(r"upsell|bundle|offer|oto|vip", p):
        return "upsell"
    if re.search(r"opt-?in|register|signup|join", p):
        return "opt-in"
    return "page"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("start_url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ctas", help="folder with test_ctas.py results (default: OUT/ctas)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    base = urllib.parse.urlparse(a.start_url)
    origin = f"{base.scheme}://{base.netloc}"
    with sync_playwright() as p:
        browser = launch(p)
        ctx, _ = new_context(browser, "desktop", block_forms=True)
        page = ctx.new_page()
        prepare(page, a.start_url, wait_ms=3000, scroll=False)
        paths = page.evaluate(STEPS_JS)
        start_path = urllib.parse.urlparse(page.url).path.rstrip("/") or "/"
        first_final = None
        steps = []
        for path in paths:
            url = origin + path
            info_http = prepare(page, url, wait_ms=2500, scroll=False)
            info = page.evaluate(PAGE_JS)
            final_path = urllib.parse.urlparse(page.url).path.rstrip("/") or "/"
            published = final_path == path.rstrip("/")
            data = info.pop("data")
            steps.append({"path": path, "url": url, "final_url": page.url, "published": published,
                          "status": info_http.get("status"), **info, "kind": kind_of(path, info), "_data": data})
            if first_final is None and not published:
                first_final = final_path
        ctx.close()
        browser.close()

    funnel_paths = {s["path"].rstrip("/") for s in steps} | {"/", start_path}
    # --- wrong-page links (from test_ctas.py results) ---
    wrong = []
    ctas_dir = a.ctas or os.path.join(a.out, "ctas")
    step_urls = {s["path"].rstrip("/") for s in steps if s["published"]} | {"/"}
    for f in sorted(glob.glob(os.path.join(ctas_dir, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        src = urllib.parse.urlparse(d.get("url", ""))
        if src.netloc != base.netloc or (src.path.rstrip("/") or "/") not in step_urls:
            continue   # only links found on this funnel's own pages
        for c in d.get("ctas", []):
            dest = c.get("final_url") or c.get("url") or ""
            u = urllib.parse.urlparse(dest)
            if u.netloc != base.netloc:
                continue
            dpath = u.path.rstrip("/") or "/"
            if dpath not in funnel_paths:
                wrong.append({"page": src.path or "/", "device": d.get("device"), "link": c.get("text", "")[:80],
                              "lands_on": dest.split("?")[0],
                              "why": "not a page of this funnel (old or other funnel): check it's the intended page and design"})
    seen = set()
    wrong = [w for w in wrong if not ((w["page"], w["link"], w["lands_on"]) in seen or seen.add((w["page"], w["link"], w["lands_on"])))]

    # --- SEO / meta checks ---
    pub = [s for s in steps if s["published"] and s["kind"] != "legal"]
    titles, descs = {}, {}
    for s in pub:
        titles.setdefault(s["title"] or "", []).append(s["path"])
        descs.setdefault(s["description"] or "", []).append(s["path"])
    opt_in_title = next((s["title"] for s in pub if s["kind"] == "opt-in"), None)
    for s in steps:
        issues = []
        if not s["published"]:
            issues.append("not published: GHL sends it to the first step")
        else:
            slug = s["path"].strip("/")
            if AUTO_SLUG_RE.search(slug):
                issues.append(f"auto-generated address /{slug}: use a clean one (e.g. /bundle, /special-offer, /confirmed)")
            if s["kind"] != "legal":
                if not s["title"]:
                    issues.append("no page title")
                elif len(titles.get(s["title"], [])) > 1:
                    issues.append("same title as " + ", ".join(x for x in titles[s["title"]] if x != s["path"]))
                if s["kind"] in ("upsell", "downsell", "thank-you") and opt_in_title and s["title"] == opt_in_title:
                    issues.append("uses the opt-in's title (browser tab and shares describe the wrong page)")
                if not s["description"]:
                    issues.append("no meta description")
                elif len(descs.get(s["description"], [])) > 1:
                    issues.append("same meta description as " + ", ".join(x for x in descs[s["description"]] if x != s["path"]))
                if not s["og_image"]:
                    issues.append("no share image (og:image): links shared on Facebook/WhatsApp show no picture")
                if not s["og_title"]:
                    issues.append("no og:title")
                if s["kind"] in ("upsell", "downsell", "thank-you") and "noindex" not in (s["robots"] or "").lower():
                    issues.append("offer/thank-you page can be indexed by Google: add noindex")
                if s["title"] and len(s["title"]) > 65:
                    issues.append(f"title is {len(s['title'])} characters (Google shows about 60)")
                if s["description"] and not (70 <= len(s["description"]) <= 160):
                    issues.append(f"description is {len(s['description'])} characters (aim for 70–160)")
            if s["kind"] == "legal" and s["title"] and opt_in_title and s["title"] == opt_in_title:
                slug = s["path"].lower()
                name = next((n for k, n in [("privacy", "Privacy Policy"), ("terms", "Terms of Service"), ("refund", "Refund Policy"),
                                            ("support", "Customer Support"), ("contact", "Contact"), ("disclaimer", "Disclaimer"),
                                            ("cookie", "Cookie Policy")] if k in slug), "the page name")
                issues.append(f"title is the opt-in's title, not the page name (e.g. \"{name} | <brand>\")")
            if not s["favicon"]:
                issues.append("no favicon")
        s["issues"] = issues
        s.pop("_data", None)

    print(f"Funnel steps from {a.start_url}:")
    for s in steps:
        mark = "OK  " if not s["issues"] else ("OFF " if not s["published"] else "FIX ")
        print(f"  {mark} {s['path']:<36} [{s['kind']}] {('title: ' + repr((s['title'] or '')[:60])) if s['published'] else ''}")
        for i in s["issues"]:
            print(f"         - {i}")
    print("\nLinks that land outside this funnel:" if wrong else "\nNo links land outside this funnel.")
    for w in wrong:
        print(f"  FIX  {w['page']} [{w['device']}] '{w['link']}' -> {w['lands_on']}")

    block = {
        "intro": "Every page in this funnel's step list (read from GoHighLevel), with its SEO and share tags, and every link that leaves the funnel.",
        "rows": [{"page": s["path"], "kind": s["kind"], "title": s["title"] if s["published"] else "(not published)",
                  "status": "ok" if not s["issues"] else ("warn" if s["published"] else "fail"),
                  "status_text": "OK" if not s["issues"] else ("Fix" if s["published"] else "Not published"),
                  "issues": s["issues"]} for s in steps],
        "wrong_links": wrong,
    }
    path = write_json(os.path.join(a.out, "funnel-steps.json"), {"steps": steps, "wrong_links": wrong, "funnel_steps": block})
    print(f"\n-> {path}  (copy \"funnel_steps\" into findings.json)")


if __name__ == "__main__":
    main()
