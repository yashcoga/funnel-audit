"""Check that every date and time on the funnel pages comes from GoHighLevel custom values, not typed-in text.

Usage:
  python check_dynamic_dates.py URL [URL ...] --out DIR [--value "October 19, 2026" --value "7:00 PM ET"]
                                [--test-params "masterclass_date=November 2, 2026&masterclass_time=8:00 PM ET"]
                                [--device desktop|mobile|both]

GoHighLevel fills {{ custom_values.x }} in on its server, so the page's HTML can't show which text came from a
custom value. This script uses three tests instead:
  1. Leftover tags: any visible "{{ custom_values.x }}" means the tag is NOT being filled (wrong name / empty value).
  2. Swap test (pages with a date script that reads ?masterclass_date= / ?masterclass_time=, e.g. masterclass
     templates; window.MC_EVENT): the page is loaded again with a test date. Every date/time that does not change
     is typed-in text = hard-coded.
  3. Match test (all other pages): each date/time on the page is compared with the current custom value text
     (--value, or read from the page's RAW_DATE / RAW_TIME). An exact match is probably the custom value; extra
     words around it (e.g. a weekday the value doesn't contain) or no match = typed-in, confirm in the GHL editor.
It also lists fixed per-class links (calendar invites, Zoom links) that do not follow the custom values, and time
zone labels that are wrong for the season (EST between March and November).

Ad pixels and form submissions are blocked. Writes DIR/dynamic-dates.json.
"""
import argparse
import datetime
import os
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import launch, new_context, prepare, slug, write_json  # noqa: E402

DEFAULT_TEST = "masterclass_date=November 2, 2026&masterclass_time=8:00 PM ET"

COLLECT_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0.05; };
  const MON = '(Jan(uary)?|Feb(ruary)?|Mar(ch)?|Apr(il)?|May|June?|July?|Aug(ust)?|Sep(t|tember)?|Oct(ober)?|Nov(ember)?|Dec(ember)?)';
  const DATE = new RegExp('\\b' + MON + '\\.?\\s+\\d{1,2}(st|nd|rd|th)?\\b|\\b\\d{1,2}[\\/.]\\d{1,2}[\\/.]\\d{2,4}\\b|\\b(Mon|Tues?|Wed(nes)?|Thu(rs)?|Fri|Sat(ur)?|Sun)day,?\\s+(' + MON + '|\\d)', 'i');
  const TIME = /\b\d{1,2}(:\d{2})?\s*(AM|PM|a\.m\.|p\.m\.)\b|\b(EST|EDT|ET|CST|CDT|CT|MST|MDT|PST|PDT|PT)\b/i;
  const items = [], seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = n.textContent.trim();
    const el = n.parentElement;
    if (!t || !el || el.closest('script,style,noscript,pre,svg')) continue;
    if (!(DATE.test(t) || TIME.test(t))) continue;
    if (!vis(el)) continue;
    // report the smallest block around the text, so "Monday, October 19" stays one item
    let blk = el;
    for (let i = 0; i < 3 && blk.parentElement && (blk.parentElement.innerText || '').length < 140; i++) blk = blk.parentElement;
    const text = (blk.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 140);
    if (seen.has(text)) continue;
    seen.add(text);
    const r = blk.getBoundingClientRect();
    items.push({text, y: Math.round(r.top + scrollY), tag: blk.tagName.toLowerCase(), cls: String(blk.className || '').slice(0, 40)});
  }
  const tags = (document.body.innerText.match(/\{\{\s*custom_values\.[\w.]+\s*\}\}/g) || []);
  const fixed = [...document.querySelectorAll('a[href], [data-href]')].map(a => a.getAttribute('href') || a.dataset.href)
    .filter(h => h && /addevent\.com|evt\.to|calendar\.google|outlook\.(live|office)|\.ics(\?|$)|zoom\.us\/(j|w|meeting)|us\d+web\.zoom/i.test(h));
  const ev = window.MC_EVENT ? {state: window.MC_EVENT.state, source: window.MC_EVENT.source,
    rawDate: window.MC_EVENT.rawDate, rawTime: window.MC_EVENT.rawTime} : null;
  // links kept in page code (e.g. CONFIG.calendarUrl used by a button), not in an href
  const CAL = /https?:(\/\/|\\u002F\\u002F)[^'"\s]*?(addevent\.com|evt\.to|calendar\.google|zoom\.us)[^'"\s]*/gi;
  for (const sc of document.scripts) for (const m of (sc.textContent.match(CAL) || []))
    if (!/action=TEMPLATE$/.test(m)) fixed.push(m.replace(/\\u002F/g, '/'));   // bare template = built from the date at runtime
  return {items, tags, fixed: [...new Set(fixed)], ev};
}
"""

RAW_RE = re.compile(r'RAW_(DATE|TIME)\s*=\s*\\?"([^"\\]*)\\?"')


def collect(browser, url, device):
    ctx, log = new_context(browser, device, block_forms=True)
    page = ctx.new_page()
    info = prepare(page, url, wait_ms=4000)
    page.wait_for_timeout(1500)
    data = page.evaluate(COLLECT_JS)
    html = page.content()
    data["raw_values"] = {k.lower(): v for k, v in RAW_RE.findall(html) if v and "{{" not in v}
    data["http"] = info
    ctx.close()
    return data


def with_params(url, params):
    sep = "&" if urllib.parse.urlparse(url).query else "?"
    return url + sep + urllib.parse.urlencode(dict(urllib.parse.parse_qsl(params)))


def season_zone_issue(text, today):
    """EST/CST/PST shown while daylight time applies (2nd Sunday in March to 1st Sunday in November)."""
    if re.search(r"\b(EST|CST|MST|PST)\b", text) and 3 <= today.month <= 11:
        return "standard-time label (EST/CST/PST) during daylight time; use ET/CT/PT"
    return None


def classify(text, values):
    if not values:
        return "unknown", "no custom value text known (pass --value)"
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    t = norm(text)
    hits = [v for v in values if norm(v) and norm(v) in t]
    if not hits:
        return "typed", "does not contain the custom value text: probably typed in"
    rest = t
    for v in hits:
        rest = rest.replace(norm(v), " ")
    if re.search(r"\b(mon|tues?|wed(nes)?|thu(rs)?|fri|sat(ur)?|sun)day\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b|\b\d{1,2}(:\d{2})?\s*(am|pm)\b", rest):
        return "partly_typed", "contains the custom value plus a typed-in date/weekday/time"
    return "custom_value", "matches the custom value text"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--value", action="append", default=[], help="current text of a date/time custom value (repeat)")
    ap.add_argument("--test-params", default=DEFAULT_TEST, help="URL parameters for the swap test")
    ap.add_argument("--device", default="both", choices=["desktop", "mobile", "both"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    devices = ["desktop", "mobile"] if a.device == "both" else [a.device]
    today = datetime.date.today()
    results = []
    learned = set(a.value)
    with sync_playwright() as p:
        browser = launch(p)
        for url in a.urls:
            for d in devices:
                base = collect(browser, url, d)
                learned.update(base["raw_values"].values())
                res = {"url": url, "device": d, "resolver": base["ev"], "leftover_tags": base["tags"],
                       "fixed_links": base["fixed"], "raw_values": base["raw_values"], "items": []}
                swapped_texts = None
                if base["ev"]:
                    sw = collect(browser, with_params(url, a.test_params), d)
                    res["swap_resolver"] = sw["ev"]
                    swapped_texts = {i["text"] for i in sw["items"]}
                for it in base["items"]:
                    row = dict(it)
                    if swapped_texts is not None and (res.get("swap_resolver") or {}).get("source") == "url":
                        row["result"] = "hard_coded" if it["text"] in swapped_texts else "dynamic"
                        row["why"] = ("unchanged when the date was swapped" if row["result"] == "hard_coded"
                                      else "changed with the swapped date")
                    else:
                        row["result"], row["why"] = classify(it["text"], learned)
                    z = season_zone_issue(it["text"], today)
                    if z:
                        row["zone_issue"] = z
                    res["items"].append(row)
                results.append(res)
        browser.close()

    for v in sorted(learned):
        z = season_zone_issue(v, today)
        if z:
            print(f"!! custom value text {v!r}: {z}")
    for r in results:
        mode = "swap test" if r["resolver"] else "match test"
        print(f"\n== {r['url']}  [{r['device']}]  ({mode}"
              + (f", date script source: {r['resolver'].get('source')}" if r["resolver"] else "") + ")")
        if r["leftover_tags"]:
            print(f"   PROBLEM  custom value tag shown as text (not filled): {sorted(set(r['leftover_tags']))}")
        if not r["items"]:
            print("   no dates or times on this page")
        for it in r["items"]:
            mark = {"dynamic": "OK  ", "custom_value": "OK? ", "hard_coded": "FIX ", "typed": "FIX?",
                    "partly_typed": "FIX?", "unknown": "??  "}[it["result"]]
            extra = f"  [{it['zone_issue']}]" if it.get("zone_issue") else ""
            print(f"   {mark} {it['text'][:90]!r}  -> {it['why']}{extra}")
        for l in r["fixed_links"]:
            print(f"   FIXED LINK (update per class or make it a custom value): {l}")
    path = write_json(os.path.join(a.out, "dynamic-dates.json"),
                      {"custom_value_texts": sorted(learned), "test_params": a.test_params, "pages": results})
    print(f"\n-> {path}")
    print("OK = follows the custom value · OK? = matches the value text (confirm in GHL editor) · "
          "FIX = typed in · FIX? = probably typed in (confirm in GHL editor)")


if __name__ == "__main__":
    main()
