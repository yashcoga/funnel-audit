"""Check every link found by scan_page.py, calendar invites, and the domain's legal pages.

Usage:
  python check_links.py --scans DIR [--out DIR]            # uses every *-desktop.json / *-mobile.json in DIR
  python check_links.py --urls URL [URL ...] [--out DIR]   # check specific links

For each unique link: HTTP status, final URL after redirects, page title.
Calendar links (evt.to / addevent / Google Calendar / .ics): event start, title and any Zoom/Meet link inside.
For each domain seen: probes /privacy-policy, /privacy, /terms, /terms-and-conditions, /refund-policy and a
nonsense path, and says whether each is a real page or just redirects somewhere else.
Writes DIR/links.json.
"""
import argparse
import glob
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import read_json, resolve, write_json  # noqa: E402

LEGAL = ["/privacy-policy", "/privacy", "/terms", "/terms-and-conditions", "/terms-of-service", "/refund-policy",
         "/disclaimer", "/this-page-should-not-exist-xyz"]
CAL_RE = re.compile(r"evt\.to|addevent\.com|calendar\.google\.com|\.ics(\?|$)|outlook\.(live|office)\.com/calendar", re.I)
MEET_RE = re.compile(r"(?:https?://)?[\w.-]*(?:zoom\.us/(?:j|w|meeting/register)/[\w?=&/-]+|meet\.google\.com/[\w-]+|teams\.microsoft\.com/l/[\w%/.-]+)")


def calendar_details(body):
    det = {}
    m = re.search(r'class="start"[^>]*>([^<]+)<', body) or re.search(r'"startDate"\s*:\s*"([^"]+)"', body) \
        or re.search(r"DTSTART[^:]*:(\S+)", body)
    if m:
        det["start"] = m.group(1).strip()
    m = re.search(r'class="end"[^>]*>([^<]+)<', body) or re.search(r"DTEND[^:]*:(\S+)", body)
    if m:
        det["end"] = m.group(1).strip()
    m = re.search(r'class="timezone"[^>]*>([^<]+)<', body)
    if m:
        det["timezone"] = m.group(1).strip()
    meets = sorted(set(x.split("&source")[0] for x in MEET_RE.findall(urllib.parse.unquote(body))))
    if meets:
        det["meeting_links"] = meets[:5]
    return det


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scans", help="folder with scan_page.py JSON files")
    ap.add_argument("--urls", nargs="*", default=[])
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    links = {}   # url -> {texts, pages}
    domains = set()
    if a.scans:
        for f in glob.glob(os.path.join(a.scans, "*-desktop.json")) + glob.glob(os.path.join(a.scans, "*-mobile.json")):
            d = read_json(f)
            if "links" not in d:
                continue
            domains.add(urllib.parse.urlparse(d["url"]).netloc)
            for l in d["links"]:
                u = l.get("abs") or ""
                if not u.startswith("http"):
                    continue
                e = links.setdefault(u.split("#")[0], {"texts": set(), "pages": set(), "visible": False})
                e["texts"].add(l["text"][:60])
                e["pages"].add(urllib.parse.urlparse(d["url"]).path)
                e["visible"] = e["visible"] or l["visible"]
    for u in a.urls:
        links.setdefault(u, {"texts": set(), "pages": set(), "visible": True})
        domains.add(urllib.parse.urlparse(u).netloc)

    out = {"links": [], "legal_pages": []}
    print(f"Checking {len(links)} links ...")
    for u, meta in sorted(links.items()):
        r = resolve(u)
        item = {"url": u, "status": r.get("status"), "final_url": r.get("final_url"), "title": r.get("title"),
                "error": r.get("error"), "link_texts": sorted(meta["texts"]), "on_pages": sorted(meta["pages"]),
                "visible": meta["visible"]}
        if CAL_RE.search(u) or CAL_RE.search(r.get("final_url") or ""):
            item["calendar"] = calendar_details(r.get("body") or "")
        out["links"].append(item)
        bot_walled = re.search(r"(facebook|instagram|linkedin|tiktok|x|twitter)\.com", urllib.parse.urlparse(u).netloc)
        if (r.get("status") or 0) and r["status"] < 400:
            flag = ""
        elif bot_walled:
            flag = "  (social site blocks automated checks; open it in the browser to confirm)"
            item["needs_browser_check"] = True
        else:
            flag = "  <-- PROBLEM"
        hid = "" if meta["visible"] else " (hidden link)"
        print(f"  {r.get('status')}  {u[:70]}{hid}  -> {(r.get('final_url') or '')[:70]}{flag}")
        if item.get("calendar"):
            print(f"       calendar: {item['calendar']}")

    for dom in sorted(domains):
        for path in LEGAL:
            u = f"https://{dom}{path}"
            r = resolve(u)
            final = r.get("final_url") or ""
            real = (r.get("status") == 200 and urllib.parse.urlparse(final).path.rstrip("/") == path.rstrip("/"))
            out["legal_pages"].append({"url": u, "status": r.get("status"), "final_url": final, "title": r.get("title"),
                                       "exists": real})
            label = "EXISTS" if real else f"missing (-> {urllib.parse.urlparse(final).path or final})"
            print(f"  legal {path:32} {label}")
    if a.out:
        print("->", write_json(os.path.join(a.out, "links.json"), out))


if __name__ == "__main__":
    main()
