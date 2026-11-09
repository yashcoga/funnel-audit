"""Root domain check: does the bare domain open a real page? A broken root is a launch blocker.

Usage:
  python check_root_domain.py START_URL --out DIR

A funnel page can work (brand.com/join) while the domain itself (brand.com) shows "404 Page not found", because no
default page was set for the domain. Anyone who types the address, shortens a link or taps the logo lands on the error.
This script opens, with ad pixels blocked:

  1. https://<funnel domain>/          the root. Broken = Critical (desktop + mobile)
  2. the www / non-www twin            broken = High
  3. http://<funnel domain>/           must reach a working secure page. Broken or not secure = High
  4. https://<main domain>/            only when the funnel is on a subdomain. Broken = Critical

Broken means: HTTP error, a domain that doesn't resolve, an SSL error, or a page that says 404 / not found / parked or
is blank (some builders send those with status 200). A root that works but shows a different page from this funnel is
reported as "confirm it's the right page". 403 / 429 / 503 can be our automated browser being refused, so those are
reported as CHECK, never as broken.

Writes DIR/root-domain.json ("root_domain" block + ready "fixes" for findings.json) and screenshots in DIR/root-domain/.
"""
import argparse
import os
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import launch, new_context, prepare, write_json  # noqa: E402

TWO_PART_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "org.au", "co.nz", "co.in", "co.za", "com.br", "com.mx"}
ERROR_RE = re.compile(r"\b404\b|page not found|site not found|domain not found|no such (site|page)"
                      r"|page (can.?t|cannot|could not) be found|page does ?n.?t exist", re.I)
PARKED_RE = re.compile(r"domain (is )?(parked|for sale)|parked (free|domain)|buy this domain|this domain (may be|is) for sale"
                       r"|future home of|domain (is ?n.?t|is not) (connected|configured)|default web ?(site|server)? ?page", re.I)
REFUSED = {403, 429, 503}
NET_ERRORS = [("ERR_NAME_NOT_RESOLVED", "the domain doesn't resolve (no DNS record)"),
              ("ERR_CERT", "SSL certificate error (browsers show a security warning)"),
              ("ERR_SSL", "SSL error (browsers show a security warning)"),
              ("ERR_CONNECTION", "the server refused or dropped the connection"),
              ("ERR_TIMED_OUT", "the server didn't answer"),
              ("Timeout", "the page didn't load within 30 seconds")]
PAGE_JS = r"""
() => {
  const h = [...document.querySelectorAll('h1, h2')].map(e => e.innerText.trim().replace(/\s+/g, ' ')).filter(Boolean);
  const text = (document.body?.innerText || '').trim().replace(/\s+/g, ' ');
  return {title: document.title, heading: (h[0] || '').slice(0, 140), text: text.slice(0, 400),
          words: (text.match(/\S+/g) || []).length, media: document.querySelectorAll('img, video, iframe').length,
          ghl: !!document.getElementById('__NUXT_DATA__') && /leadconnector|msgsndr|highlevel/i.test(document.documentElement.innerHTML)};
}
"""
STEPS_JS = r"""
() => {
  const nd = document.getElementById('__NUXT_DATA__');
  if (!nd) return [];
  let arr; try { arr = JSON.parse(nd.textContent); } catch (e) { return []; }
  return [...new Set(arr.filter(v => typeof v === 'string' && /^\/[a-z0-9][a-z0-9-]{1,80}$/i.test(v)))];
}
"""


def apex_of(host):
    parts = host.split(".")
    return ".".join(parts[-3:] if ".".join(parts[-2:]) in TWO_PART_SUFFIXES else parts[-2:])


def bare(host):
    return host[4:] if host.startswith("www.") else host


def load(page, url):
    """Open a URL; retry once so a network blip is never reported as a broken domain."""
    err = None
    for attempt in range(2):
        try:
            nav = prepare(page, url, wait_ms=2500, scroll=False, timeout=30000)
            return {**nav, **page.evaluate(PAGE_JS), "error": None}
        except Exception as e:
            err = str(e)
            page.wait_for_timeout(1500)
    reason = next((text for code, text in NET_ERRORS if code in err), err.splitlines()[0][:120])
    return {"status": None, "final_url": url, "error": reason}


def same_page(r, base):
    if not base or base.get("error"):
        return False
    if r.get("heading") and base.get("heading"):
        return r["heading"] == base["heading"]
    return bool(r.get("title")) and r.get("title") == base.get("title")


def classify(r, role, ctx):
    """Return (status, status_text, what we found). status: ok | warn | fail | check."""
    if r["error"]:
        return "fail", "Broken", r["error"]
    code, text, words = r["status"], f"{r['title']} {r['text']}", r["words"]
    if code in REFUSED:
        return "check", "Check by hand", f"HTTP {code}: the server may be refusing our automated browser. Open it in a normal browser."
    if code and code >= 400:
        return "fail", "Broken", f"HTTP {code}. The page says: \"{r['text'][:80] or r['title']}\""
    if words < 80 and ERROR_RE.search(text):
        return "fail", "Broken", f"Error page sent as HTTP {code}. The page says: \"{r['text'][:80]}\""
    if words < 200 and PARKED_RE.search(text):
        return "fail", "Broken", f"Parked or placeholder page. The page says: \"{r['text'][:80]}\""
    if words < 3 and not r["media"]:
        return "fail", "Broken", "The page is blank."
    final = urllib.parse.urlparse(r["final_url"])
    if role == "http" and final.scheme != "https":
        return "warn", "Not secure", "Works, but stays on http:// instead of moving to https://."
    if role == "apex":
        return "ok", "Works", f"Opens \"{r['title'] or r['heading'] or final.path}\". It doesn't have to be this funnel."
    if bare(final.hostname or "") != bare(ctx["host"]):
        return "warn", "Confirm", f"Sends visitors to another domain: {final.hostname}."
    path = final.path.rstrip("/") or "/"
    if same_page(r, ctx["base"]) or path == ctx["start_path"] or path in ctx["steps"]:
        return "ok", "Works", f"Opens this funnel ({path})."
    return "warn", "Confirm", f"Works, but shows a different page from this funnel: \"{r['title'] or r['heading']}\"."


SEVERITY = {"root": "critical", "apex": "critical", "twin": "high", "http": "high"}


def action_for(row, ctx):
    if row["status"] == "check":
        return "Open it in a normal browser. If it shows an error, treat it as broken."
    found, domain = row["found"], ctx["host"]
    if "resolve" in found:
        return f"Add the DNS record for {urllib.parse.urlparse(row['address']).hostname} at the domain registrar."
    if "SSL" in found:
        return "Issue or renew the SSL certificate for this address where the domain is connected."
    if row["status"] == "warn":
        if "stays on http" in found:
            return "Turn on the redirect to https:// where the domain is connected."
        return f"Confirm this is the page the domain should open. If not, point it to {ctx['start_url']}."
    if row["role"] == "apex":
        return "Tell whoever manages the main website: this domain must open a working page."
    if row["role"] == "twin":
        return "Connect this version of the address too, so it reaches the same page as the root."
    if row["role"] == "http":
        return "Connect the domain properly so http:// moves to the working https:// page."
    if ctx["ghl"]:
        return (f"In GoHighLevel open the domain settings for {domain} (Settings → Domains) and set its default "
                f"step/page to the page it should open, normally this funnel's first page: {ctx['start_url']}.")
    return f"In the page builder, set the home page of {domain} to the page it should open, normally {ctx['start_url']}."


def shot(page, out, name):
    os.makedirs(os.path.join(out, "root-domain"), exist_ok=True)
    rel = f"root-domain/{name}.png"
    page.screenshot(path=os.path.join(out, rel), full_page=False)
    return rel


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("start_url")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    u = urllib.parse.urlparse(a.start_url)
    host, apex = u.hostname, apex_of(u.hostname)
    targets = [("root", f"https://{host}/", "Root of the funnel's domain")]
    if host.startswith("www."):
        targets.append(("twin", f"https://{bare(host)}/", "Without www"))
    elif host == apex:
        targets.append(("twin", f"https://www.{host}/", "With www"))
    targets.append(("http", f"http://{host}/", "Not secure (http://)"))
    if bare(host) != apex:
        targets.append(("apex", f"https://{apex}/", "Main domain above this subdomain"))

    rows, shots = [], []
    with sync_playwright() as p:
        browser = launch(p)
        ctx_d, _ = new_context(browser, "desktop", block_forms=True)
        page = ctx_d.new_page()
        base = load(page, a.start_url)
        steps = set(page.evaluate(STEPS_JS)) if not base["error"] else set()
        ctx = {"host": host, "start_url": a.start_url, "start_path": u.path.rstrip("/") or "/", "steps": steps,
               "base": base, "ghl": bool(base.get("ghl"))}
        for role, url, label in targets:
            r = load(page, url)
            status, text, found = classify(r, role, ctx)
            row = {"role": role, "check": label, "address": url, "lands_on": r["final_url"], "http": r["status"],
                   "status": status, "status_text": text, "found": found}
            row["action"] = "None" if status == "ok" else action_for(row, ctx)
            # A root that works but shows another page is "confirm it", never a launch blocker.
            row["severity"] = None if status in ("ok", "check") else ("high" if status == "warn" else SEVERITY[role])
            if role in ("twin", "http") and status == "fail" and rows[0]["status"] == "fail" and r["final_url"] == rows[0]["lands_on"]:
                row["action"] = "Lands on the broken root, so fixing the root fixes this too. Re-test after."
                row["severity"] = None   # same cause as the Critical fix: don't list it twice
            if status in ("ok", "check") and role != "root":
                rows.append(row)
                continue
            if not r["error"]:
                shots.append({"file": shot(page, a.out, f"{role}-desktop"), "caption": f"{url} on desktop: {text}", "mobile": False})
            rows.append(row)
        if rows[0]["status"] != "fail" or not base["error"]:
            ctx_m, _ = new_context(browser, "mobile", block_forms=True)
            mp = ctx_m.new_page()
            r = load(mp, targets[0][1])
            status, text, found = classify(r, "root", ctx)
            if not r["error"]:
                shots.append({"file": shot(mp, a.out, "root-mobile"), "caption": f"{targets[0][1]} on iPhone: {text}", "mobile": True})
            if status == "fail" and rows[0]["status"] != "fail":
                rows[0].update(status=status, status_text=text, found=f"On mobile only: {found}", severity="critical")
                rows[0]["action"] = action_for(rows[0], ctx)
            ctx_m.close()
        ctx_d.close()
        browser.close()

    crit = [x for x in rows if x["severity"] == "critical"]
    high = [x for x in rows if x["severity"] == "high"]
    print(f"Root domain check for {a.start_url}:")
    for x in rows:
        mark = {"ok": "OK   ", "warn": "FIX  ", "fail": "BROKEN", "check": "CHECK"}[x["status"]]
        print(f"  {mark} {x['address']:<44} -> {x['lands_on']}  [{x['http']}]")
        print(f"         {x['found']}")
        if x["status"] != "ok":
            print(f"         Do: {x['action']}")

    def fix(fid, severity, title, group):
        return {"id": fid, "severity": severity, "title": title, "where": ", ".join(x["address"] for x in group),
                "bullets": [f"{x['address']}: {x['found']}" for x in group],
                "screenshots": [s for s in shots if any(s["caption"].startswith(x["address"]) for x in group)],
                "now": "; ".join(f"{x['address']} {x['status_text'].lower()}" for x in group),
                "change_to": " ".join(dict.fromkeys(x["action"] for x in group)),
                "note": "After the fix, re-run check_root_domain.py: every address must reach a working page."}

    fixes = []
    if crit:
        title = f"The root domain doesn't open a page: {crit[0]['address']}"
        fixes.append(fix("RD1", "critical", title, crit))
    if high:
        fixes.append(fix("RD2", "high", f"Check where {bare(host)} and its other versions land", high))
    if crit:
        verdict = f"Not configured properly: {crit[0]['address']} is broken ({crit[0]['found']}). Launch blocker."
    elif high:
        verdict = f"The root domain opens a page, but {len(high)} item{'s' if len(high) > 1 else ''} need{'' if len(high) > 1 else 's'} attention (see the table)."
    elif any(x["status"] == "check" for x in rows):
        verdict = "Some addresses couldn't be confirmed automatically. Open them in a normal browser."
    else:
        verdict = "The root domain is configured properly."
    block = {"intro": "The bare domain and its other versions, opened the way a visitor would. A funnel page can work while the domain itself shows an error.",
             "verdict": verdict, "ok": not crit,
             "rows": [{k: x[k] for k in ("check", "address", "lands_on", "http", "status", "status_text", "found", "action")} for x in rows],
             "screenshots": shots}
    path = write_json(os.path.join(a.out, "root-domain.json"), {"root_domain": block, "fixes": fixes})
    print(f"\n{verdict}\n-> {path}  (copy \"root_domain\" into findings.json and put \"fixes\" first in the fix list)")


if __name__ == "__main__":
    main()
