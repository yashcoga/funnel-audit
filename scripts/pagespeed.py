"""Google PageSpeed Insights scores (mobile + desktop) for several pages at once.

Usage:
  python pagespeed.py URL [URL ...] --out DIR [--force]

Uses the PageSpeed API when the environment variable PSI_API_KEY is set (free key from Google Cloud),
otherwise opens pagespeed.web.dev in a headless browser and reads the results.
For each page and form factor: Performance score, FCP, LCP, TBT, CLS, Speed Index, the real-visitor
"Core Web Vitals Assessment" (Chrome UX Report) and a shareable report link.

SAFETY: Google's test browser fires the page's live ad pixels. Purchase thank-you pages would send a fake
Purchase, so URLs containing "thank", "confirm", "receipt", "success" or "order-complete" are refused unless
--force is given. Test those with lighthouse.py instead (it blocks the pixels).
Writes DIR/pagespeed.json.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import UA, launch, write_json  # noqa: E402

UNSAFE = re.compile(r"thank|confirm|receipt|success|order-complete|purchase", re.I)
METRICS = ["First Contentful Paint", "Largest Contentful Paint", "Total Blocking Time", "Cumulative Layout Shift",
           "Speed Index"]
FIELD = ["Largest Contentful Paint (LCP)", "Interaction to Next Paint (INP)", "Cumulative Layout Shift (CLS)",
         "First Contentful Paint (FCP)", "Time to First Byte (TTFB)"]


def via_api(url, key):
    out = {}
    for strategy in ("mobile", "desktop"):
        q = urllib.parse.urlencode({"url": url, "strategy": strategy, "category": "performance", "key": key})
        with urllib.request.urlopen(f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?{q}", timeout=180) as r:
            d = json.load(r)
        lh = d["lighthouseResult"]
        a = lh["audits"]
        le = d.get("loadingExperience") or {}
        out[strategy] = {
            "score": round(lh["categories"]["performance"]["score"] * 100),
            "metrics": {m: a[k]["displayValue"] for m, k in zip(METRICS, ["first-contentful-paint", "largest-contentful-paint",
                                                                          "total-blocking-time", "cumulative-layout-shift", "speed-index"])},
            "field": {"assessment": le.get("overall_category"),
                      "metrics": {k: v.get("percentile") for k, v in (le.get("metrics") or {}).items()}},
            "report": f"https://pagespeed.web.dev/analysis?url={urllib.parse.quote(url, safe='')}&form_factor={strategy}",
        }
    return out


PANEL_JS = "() => { const p = [...document.querySelectorAll('[role=tabpanel]')].filter(p => p.getBoundingClientRect().height > 0)[0]; return (p || document.body).innerText; }"


def parse_panel(t):
    field_part, _, lab = t.partition("Diagnose performance issues")
    res = {"metrics": {}, "field": {"metrics": {}}}
    m = re.search(r"^\s*(\d{1,3})\s*\n\s*Performance", lab, re.M)
    res["score"] = int(m.group(1)) if m else None
    for k in METRICS:
        m = re.search(re.escape(k) + r"\s*\n\s*([\d.,]+\s*(?:ms|s)?)", lab)
        if m:
            res["metrics"][k] = m.group(1).strip()
    m = re.search(r"Core Web Vitals Assessment:\s*\n?\s*(\w+)", field_part)
    res["field"]["assessment"] = m.group(1) if m else ("No data" if "No Data" in field_part else None)
    for k in FIELD:
        m = re.search(re.escape(k) + r"\s*\n\s*([\d.,]+\s*(?:ms|s)?|N/A)", field_part)
        if m:
            res["field"]["metrics"][k] = m.group(1).strip()
    res["field"]["scope"] = "origin" if re.search(r"This URL\s*\n\s*Origin", field_part) and "No Data" in field_part else "url or origin"
    return res


def _wait_panel(pg, seconds):
    """Poll the visible results panel until the lab score is there; return its text (or None)."""
    for _ in range(int(seconds / 3)):
        t = pg.evaluate(PANEL_JS)
        lab = t.partition("Diagnose performance issues")[2]
        if lab and re.search(r"^\s*\d{1,3}\s*\n\s*Performance", lab, re.M):
            return t
        if re.search(r"Lighthouse returned error|something went wrong|unable to load", t, re.I):
            return t
        pg.wait_for_timeout(3000)
    return None


def via_web(urls):
    results = {}
    with sync_playwright() as p:
        browser = launch(p)
        # a normal Chrome user agent: pagespeed.web.dev doesn't finish for "HeadlessChrome"
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, user_agent=UA)
        tabs = []
        for u in urls:
            pg = ctx.new_page()
            pg.goto("https://pagespeed.web.dev/analysis?url=" + urllib.parse.quote(u, safe="") + "&form_factor=mobile",
                    wait_until="domcontentloaded")
            tabs.append((u, pg))
        for u, pg in tabs:
            out = {}
            for ff in ("Mobile", "Desktop"):
                try:
                    pg.get_by_role("tab", name=re.compile(ff, re.I)).first.click(timeout=60000)
                except Exception:
                    pass
                t = _wait_panel(pg, 240 if ff == "Mobile" else 150)
                if t is None:
                    out[ff.lower()] = {"error": "no result within the time limit", "report": pg.url}
                    continue
                r = parse_panel(t)
                r["report"] = f"{pg.url.split('?')[0]}?form_factor={ff.lower()}"
                out[ff.lower()] = r
            results[u] = out
        browser.close()
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true", help="allow thank-you/confirmation pages (fires live pixels!)")
    a = ap.parse_args()
    urls = []
    for u in a.urls:
        if UNSAFE.search(urllib.parse.urlparse(u).path) and not a.force:
            print(f"  SKIPPED {u}: looks like a thank-you/confirmation page (Google's test would fire a live Purchase). "
                  f"Use lighthouse.py for it.")
            continue
        urls.append(u)
    if not urls:
        return
    key = os.environ.get("PSI_API_KEY")
    t0 = time.time()
    if key:
        results = {u: via_api(u, key) for u in urls}
        source = "PageSpeed Insights API"
    else:
        results = via_web(urls)
        source = "pagespeed.web.dev"
    path = write_json(os.path.join(a.out, "pagespeed.json"), {"source": source, "results": results})
    print(f"Source: {source} ({int(time.time() - t0)} s)")
    for u, r in results.items():
        m, d = r.get("mobile", {}), r.get("desktop", {})
        if "error" in m or "error" in d:
            print(f"  {u}: ERROR mobile={m.get('error')} desktop={d.get('error')}  ({m.get('report')})")
            continue
        print(f"  {u}\n     mobile {m.get('score')}  desktop {d.get('score')}   mobile LCP {m['metrics'].get('Largest Contentful Paint')}"
              f"  TBT {m['metrics'].get('Total Blocking Time')}  CLS {m['metrics'].get('Cumulative Layout Shift')} / "
              f"{d['metrics'].get('Cumulative Layout Shift')}\n     real visitors: mobile {m['field'].get('assessment')}, "
              f"desktop {d['field'].get('assessment')}   report: {m.get('report')}")
    print("->", path)


if __name__ == "__main__":
    main()
