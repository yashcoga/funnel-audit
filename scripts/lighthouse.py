"""Lighthouse performance details per page (what exactly is slow), with ad pixels blocked.

Usage:
  python lighthouse.py URL [URL ...] --out DIR [--strategy mobile|desktop|both]

Safe for thank-you pages (Purchase/Lead pixels are blocked). Needs Node.js (runs `npx lighthouse`).
For each page: score, FCP, LCP, TBT, CLS, Speed Index, page weight, the LCP element, the heaviest images,
scripts that block the page longest, and the biggest savings Lighthouse suggests.
Note: results come from this computer's connection. If you're outside the US, some ad scripts may be blocked
in your country (e.g. TikTok in India) and inflate load times; say so in the report.
Writes DIR/lighthouse/<page>-<strategy>.json (raw) and DIR/lighthouse.json (summary).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import slug, write_json  # noqa: E402

BLOCK = ["*facebook.com/tr*", "*analytics.tiktok.com*", "*google.com/rmkt*", "*google.com/ccm*", "*googleadservices*",
         "*t.whop.tw/conversions*", "*google-analytics.com/g/collect*", "*analytics.google.com/g/collect*"]
LH_VERSION = "12.2.1"


def run_lh(url, strategy, raw_dir):
    out = os.path.join(raw_dir, f"{slug(url)}-{strategy}.json")
    npx = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
    cmd = [npx, "-y", f"lighthouse@{LH_VERSION}", url, "--only-categories=performance", "--output=json",
           f"--output-path={out}", "--chrome-flags=--headless=new", "--quiet"]
    if strategy == "desktop":
        cmd.insert(4, "--preset=desktop")
    cmd += [f"--blocked-url-patterns={b}" for b in BLOCK]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=400, shell=(os.name == "nt"))
    if r.returncode != 0 or not os.path.exists(out):
        return None, (r.stderr or r.stdout)[-400:]
    return out, None


def summarize(path):
    d = json.load(open(path, encoding="utf-8"))
    a = d["audits"]
    dv = lambda k: a.get(k, {}).get("displayValue")
    s = {"score": round((d["categories"]["performance"]["score"] or 0) * 100),
         "fcp": dv("first-contentful-paint"), "lcp": dv("largest-contentful-paint"), "tbt": dv("total-blocking-time"),
         "cls": dv("cumulative-layout-shift"), "speed_index": dv("speed-index"), "weight": dv("total-byte-weight")}
    try:
        s["lcp_element"] = a["largest-contentful-paint-element"]["details"]["items"][0]["items"][0]["node"]["snippet"][:200]
    except Exception:
        s["lcp_element"] = None
    reqs = a.get("network-requests", {}).get("details", {}).get("items", [])
    imgs = sorted([r for r in reqs if r.get("resourceType") == "Image"], key=lambda r: -(r.get("transferSize") or 0))
    s["heaviest_images"] = [{"url": r["url"], "kb": round((r.get("transferSize") or 0) / 1024)} for r in imgs[:6]]
    s["requests"] = len(reqs)
    tp = a.get("third-party-summary", {}).get("details", {}).get("items", [])
    s["third_party_blocking"] = [{"entity": (t.get("entity") if isinstance(t.get("entity"), str) else (t.get("entity") or {}).get("text")),
                                  "blocking_ms": round(t.get("blockingTime") or 0), "kb": round((t.get("transferSize") or 0) / 1024)}
                                 for t in sorted(tp, key=lambda t: -(t.get("blockingTime") or 0))[:6]]
    opps = []
    for k, v in a.items():
        det = v.get("details") or {}
        if v.get("score") is not None and v["score"] < 0.9 and (det.get("type") == "opportunity" or "savings" in str(v.get("displayValue", "")).lower()):
            opps.append({"id": k, "title": v.get("title"), "savings": v.get("displayValue")})
    s["opportunities"] = opps[:10]
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--strategy", default="both", choices=["mobile", "desktop", "both"])
    a = ap.parse_args()
    raw = os.path.join(a.out, "lighthouse")
    os.makedirs(raw, exist_ok=True)
    strategies = ["mobile", "desktop"] if a.strategy == "both" else [a.strategy]
    summary = {}
    for u in a.urls:
        summary[u] = {}
        for st in strategies:
            path, err = run_lh(u, st, raw)
            if err:
                summary[u][st] = {"error": err}
                print(f"  {u} [{st}] ERROR: {err[-200:]}")
                continue
            s = summarize(path)
            s["raw"] = path
            summary[u][st] = s
            print(f"  {u} [{st}] score {s['score']}  LCP {s['lcp']}  TBT {s['tbt']}  CLS {s['cls']}  {s['weight']}")
            for im in s["heaviest_images"][:3]:
                print(f"       image {im['kb']} KB  {im['url'][-70:]}")
            for t in s["third_party_blocking"][:3]:
                if t["blocking_ms"]:
                    print(f"       blocks page {t['blocking_ms']} ms: {t['entity']}")
    print("->", write_json(os.path.join(a.out, "lighthouse.json"), summary))


if __name__ == "__main__":
    main()
