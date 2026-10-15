"""Find and audit every legal / policy page linked from the funnel (usually the footer: Terms, Privacy, Refund,
Disclaimer, Customer Support, Contact ...).

Usage:
  python check_legal_pages.py --scans DIR [--out DIR] [--urls URL [URL ...]]

Run it after the funnel pages are scanned (scan_page.py) and BEFORE check_links.py / copy_review.py: it scans every
legal page it finds into the same DIR (desktop + mobile screenshots), so those scripts include them too.

What it does:
  1. Footer matrix: which legal links each funnel page shows (every page needs at least Privacy + Terms).
  2. Funnel facts: prices, emails, company name and the brand names used on the funnel pages.
  3. Per legal page: HTTP status / redirect, page title, length, "last updated" date, placeholders, prices or offer
     words that appear nowhere in the funnel (leftovers from another offer), brand-name casing, company name and
     support email consistency, justified text on phones, and the clauses each page type normally needs
     (Privacy: SMS/A2P wording, cookies & pixels, rights; Terms: liability, governing law, results disclaimer;
     Refund: the policy for what this funnel sells and how to ask; Support: a way to reach a person).
Missing clauses are "check with the client / their lawyer" items, not legal advice.
Writes DIR/legal.json and prints a summary plus a ready-made "legal_pages" block for findings.json.
"""
import argparse
import datetime
import glob
import os
import re
import subprocess
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import launch, new_context, prepare, read_json, slug, write_json  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = [  # (kind, regex on link text or path)
    ("privacy", r"privacy|data (policy|protection)|gdpr|ccpa|do not sell"),
    ("terms", r"terms|conditions|\btos\b|user agreement"),
    ("refund", r"refund|return|cancell?ation"),
    ("disclaimer", r"disclaimer|earnings|income|results"),
    ("cookies", r"cookie"),
    ("support", r"support|contact|help ?desk|customer (care|service)"),
    ("sms", r"\bsms\b|text messag"),
    ("accessibility", r"accessibility"),
]
MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
PRICE_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d\d)?(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
COMPANY_RE = re.compile(r"\b((?:[A-Z][\w&'.-]*\s){1,6}(?:LLC|L\.L\.C\.|Inc\.?|Ltd\.?|Limited|Corp\.?|Corporation|Co\.|LLP|PLLC|GmbH))")
PLACEHOLDER_RE = re.compile(r"\[(?:company|business|your|name|date|email|address|website|state|insert)[^\]]{0,40}\]|\{\{[^}]{1,60}\}\}|lorem ipsum|"
                            r"\byour company\b|\bcompany name\b|example\.com|\binsert (?:here|name|date)\b|\bXXX+\b|\bTBD\b", re.I)
OFFER_WORDS = ["VIP", "Platinum", "Gold tier", "General Admission", "ticket", "upgrade tier", "summit", "workshop",
               "bootcamp", "retreat", "membership", "subscription", "coaching program", "mastermind", "course", "webinar",
               "challenge", "certification", "free trial", "monthly fee", "monthly payment"]
MONEY_NEAR = re.compile(r"purchas|payment|\bpay\b|price|checkout|order|\$", re.I)
UPDATED_RE = re.compile(r"(last\s+updated|last\s+revised|effective(?:\s+date)?|updated\s+on)\s*[:\-]?\s*([^\n]{0,40})", re.I)
FULL_DATE_RE = re.compile(rf"{MONTHS}\.?\s+(\d{{1,2}})(?!\d)(?:st|nd|rd|th)?,?\s+(\d{{4}})|(\d{{1,2}})/(\d{{1,2}})/(\d{{2,4}})", re.I)

CLAUSES = {
    "privacy": [
        ("what is collected", r"information we collect|we collect|personal (information|data)"),
        ("how it is used", r"how we use|we use (your|this)"),
        ("selling / sharing", r"(do not|don.t|never) sell|share (your|it|information)|third[- ]part"),
        ("cookies, pixels & ads tracking", r"cookie|pixel|tracking"),
        ("rights: access / delete / opt out", r"delete|deletion|access (your|to your)|opt[- ]out|unsubscribe"),
        ("contact for privacy questions", r"contact us|@|reach (us|out)"),
        ("children under 13", r"child|under (the age of )?1[38]|minors"),
    ],
    "sms": [  # A2P 10DLC / CTIA wording carriers check before approving a texting campaign
        ("Reply STOP to opt out", r"\bSTOP\b"),
        ("Reply HELP for help", r"\bHELP\b"),
        ("message frequency", r"frequency|recurring"),
        ("message and data rates may apply", r"(msg|message) (&|and) data rates"),
        ("consent is not a condition of purchase", r"not a condition"),
        ("phone numbers / SMS consent not shared or sold", r"(not|never) (be )?(shared|sold|share|sell)[^.]{0,120}(phone|mobile|sms|text)|"
                                                           r"(phone|mobile|sms|text)[^.]{0,120}(not|never) (be )?(shared|sold|share|sell)"),
    ],
    "terms": [
        ("acceptance of terms", r"agree to (be bound|these terms)|acceptance"),
        ("intellectual property", r"intellectual property|copyright"),
        ("no guarantee / results disclaimer", r"not (typical|guaranteed)|no guarantee|(do not|don.t) guarantee|results (may|will) vary"),
        ("disclaimer of warranties (\"as is\")", r"as is|as available|warrant"),
        ("limitation of liability", r"limitation of liability|not (be )?liable|in no event"),
        ("governing law / venue", r"governing law|governed by|jurisdiction|venue"),
        ("disputes / arbitration", r"arbitration|dispute"),
        ("refunds (or a link to the refund policy)", r"refund"),
        ("contact details", r"contact us|@"),
    ],
    "refund": [
        ("a clear refund rule (window or non-refundable)", r"\b\d+[- ]day|within \d+|non-?refundable|no refunds|all sales (are )?final|money[- ]back"),
        ("how to ask for a refund (email / form)", r"@|contact|reach out|email us|submit"),
    ],
    "support": [
        ("a way to reach a person (email / phone / form)", r"@|\(\d{3}\)|\d{3}[-. ]\d{3}[-. ]\d{4}|form"),
        ("expected response time", r"within \d+|business days?|hours|24/7|respond"),
    ],
}
FINANCE_RE = re.compile(r"\btrad(e|ing|er)\b|invest|forex|crypto|stock|options|income|profit|earn", re.I)


def kind_of(text, url):
    hay = f"{text} {urllib.parse.urlparse(url).path.replace('-', ' ').replace('_', ' ')}"
    for k, rx in KINDS:
        if re.search(rx, hay, re.I):
            return k
    return None


def norm(url):
    u = urllib.parse.urlparse(url)
    return f"{u.scheme}://{u.netloc}{u.path.rstrip('/') or '/'}"


JS_PAGE = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const blocks = [...document.querySelectorAll('p, li, div, span, td')].filter(e =>
      vis(e) && e.children.length < 6 && (e.innerText || '').trim().length > 80);
  const justified = blocks.filter(e => getComputedStyle(e).textAlign === 'justify').length;
  const h1 = [...document.querySelectorAll('h1')].filter(vis).map(e => e.innerText.trim());
  return {title: document.title, h1, text: document.body.innerText, justified, blocks: blocks.length,
          desc: (document.querySelector('meta[name=description]') || {}).content || null};
}
"""


def visit(browser, url, device):
    ctx, _ = new_context(browser, device)
    page = ctx.new_page()
    try:
        nav = prepare(page, url, wait_ms=1500, scroll=False)
        info = page.evaluate(JS_PAGE)
        info.update(nav)
    except Exception as e:
        info = {"error": str(e).splitlines()[0][:160]}
    ctx.close()
    return info


def brand_names(funnel_text):
    """Title-case phrases (2-4 words) the funnel uses at least 3 times, e.g. 'Charts To Cash'."""
    small = {"To", "Of", "The", "And", "For"}
    counts = {}
    for line in funnel_text.splitlines():
        toks = re.findall(r"[A-Za-z][\w'$]*", line)
        for i in range(len(toks)):
            for n in (2, 3, 4):
                ph = toks[i:i + n]
                if len(ph) < n or ph[0] in small or ph[-1] in small:
                    continue
                if all(t[0].isupper() and (t in small or not t.isupper() or len(t) <= 3) for t in ph):
                    k = " ".join(ph)
                    counts[k] = counts.get(k, 0) + 1
    keep = [c for c, n in counts.items() if n >= 3]
    return sorted(keep, key=lambda c: (-counts[c], len(c)))[:40]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scans", required=True, help="folder with the scan_page.py JSON of the funnel pages")
    ap.add_argument("--out", default=None)
    ap.add_argument("--urls", nargs="*", default=[], help="extra legal page URLs to check")
    ap.add_argument("--no-scan", action="store_true", help="don't run scan_page.py on the legal pages")
    a = ap.parse_args()
    out = a.out or a.scans
    scans = {}
    for f in sorted(glob.glob(os.path.join(a.scans, "*-desktop.json"))):
        d = read_json(f)
        if d.get("url"):
            scans[norm(d.get("requested_url") or d["url"])] = d

    # 1. legal links per funnel page
    legal, matrix, funnel_text = {}, {}, []
    for u, d in scans.items():
        own = kind_of(d.get("title") or "", u) if re.search(r"terms|privacy|refund|policy|disclaimer|support|contact|cookie|legal",
                                                          urllib.parse.urlparse(u).path, re.I) else None
        if own:
            legal.setdefault(u, {"kind": own, "name": d.get("title") or urllib.parse.urlparse(u).path, "linked_from": []})
            continue
        if ((d.get("http") or {}).get("status") or 0) >= 400:
            continue
        funnel_text.append(d.get("text") or "")
        kinds = set()
        for ln in d.get("links", []):
            href = ln.get("abs") or ""
            if not href.startswith("http"):
                continue
            k = kind_of(ln.get("text") or "", href)
            if not k:
                continue
            kinds.add(k)
            e = legal.setdefault(norm(href), {"kind": k, "name": (ln.get("text") or "").strip(), "linked_from": []})
            if not e["name"]:
                e["name"] = (ln.get("text") or "").strip()
            path = urllib.parse.urlparse(u).path or "/"
            if path not in e["linked_from"]:
                e["linked_from"].append(path)
        matrix[urllib.parse.urlparse(u).path or "/"] = sorted(kinds)
    for u in a.urls:
        legal.setdefault(norm(u), {"kind": kind_of("", u) or "other", "name": urllib.parse.urlparse(u).path, "linked_from": []})

    ftext = "\n".join(funnel_text)
    facts = {
        "prices": sorted(set(p.replace(" ", "") for p in PRICE_RE.findall(ftext))),
        "emails": sorted(set(e.lower().rstrip(".") for e in EMAIL_RE.findall(ftext))),
        "companies": sorted(set(c.strip() for c in COMPANY_RE.findall(ftext))),
        "brands": brand_names(ftext),
        "finance_niche": bool(len(FINANCE_RE.findall(ftext)) >= 5),
        "sms_on_funnel": bool(re.search(r"text messag|\bsms\b", ftext, re.I)),
    }
    footer_issues = [f"{p}: no Privacy link" for p, k in matrix.items() if "privacy" not in k] + \
                    [f"{p}: no Terms link" for p, k in matrix.items() if "terms" not in k]

    # 2. visit each legal page
    today = datetime.date.today()
    pages = []
    with sync_playwright() as p:
        browser = launch(p)
        for u, meta in sorted(legal.items(), key=lambda kv: [k for k, _ in KINDS].index(kv[1]["kind"]) if kv[1]["kind"] in dict(KINDS) else 99):
            dk = visit(browser, u, "desktop")
            mb = visit(browser, u, "mobile")
            text = dk.get("text") or ""
            path = urllib.parse.urlparse(u).path
            F = []

            def add(sev, msg):
                F.append({"severity": sev, "text": msg})

            if dk.get("error"):
                add("critical", f"Page did not load: {dk['error']}")
            else:
                if (dk.get("status") or 0) >= 400:
                    add("critical", f"HTTP {dk['status']}: the page is missing")
                elif norm(dk["final_url"]) != norm(u):
                    add("critical", f"Redirects to {dk['final_url']}: the page doesn't exist")
                if not (dk.get("title") or "").strip():
                    add("medium", "No page title (browser tab and search results show the bare URL)")
                words = len(re.findall(r"\w+", text))
                if meta["kind"] != "support" and words < 120:
                    add("high", f"Very short ({words} words): looks like a stub")
                for ph in sorted(set(m.group(0) for m in PLACEHOLDER_RE.finditer(text)))[:5]:
                    add("high", f"Placeholder text left in: “{ph}”")
                m = UPDATED_RE.search(text)
                if meta["kind"] in ("privacy", "terms", "refund", "disclaimer", "cookies") and not m:
                    add("medium", "No “Last updated” / effective date")
                elif m:
                    fd = FULL_DATE_RE.search(m.group(2) or "")
                    if fd:
                        dt, raw = None, re.sub(r"(?<=\d)(st|nd|rd|th)", "", fd.group(0)).replace(",", "").replace(".", "")
                        for fmt in ("%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y"):
                            try:
                                dt = datetime.datetime.strptime(raw, fmt).date()
                                break
                            except ValueError:
                                pass
                        if dt and dt > today:
                            add("high", f"“Last updated” date is in the future: {m.group(0).strip()}")
                    elif meta["kind"] in ("privacy", "terms"):
                        add("polish", f"Date is month-only (“{m.group(0).strip()}”); a full date is clearer")
                # leftovers from other offers
                for pr in sorted(set(x.replace(" ", "") for x in PRICE_RE.findall(text))):
                    if facts["prices"] and pr not in facts["prices"]:
                        ctx_m = re.search(r"[^.\n]{0,90}" + re.escape(pr) + r"[^.\n]{0,60}", text)
                        add("high", f"Price {pr} appears nowhere in the funnel (leftover from another offer?): "
                                    f"“{(ctx_m.group(0) if ctx_m else pr).strip()}”")
                for w in OFFER_WORDS:
                    rx = re.compile(r"\b" + re.escape(w) + r"s?\b", re.I if w.islower() else 0)
                    if rx.search(text) and not rx.search(ftext):
                        ctx_m = re.search(r"[^.\n]{0,80}\b" + re.escape(w) + r"[^.\n]{0,60}", text, 0 if not w.islower() else re.I)
                        snippet = (ctx_m.group(0) if ctx_m else w).strip()
                        sold = bool(MONEY_NEAR.search(snippet))
                        add("high" if sold else "medium",
                            f"Mentions “{w}”{' as something you pay for' if sold else ''}, but nothing in this funnel is "
                            f"called that{'' if sold else ' (fine if it is a related offer named on purpose)'}: “{snippet}”")
                seen_bad = set()
                for b in facts["brands"]:
                    for hit in set(re.findall(r"\b" + re.escape(b) + r"\b", text, re.I)):
                        if hit != b and hit.lower() not in seen_bad and not hit.isupper():
                            seen_bad.add(hit.lower())
                            add("polish", f"Brand written “{hit}”; the funnel writes “{b}”")
                emails = set(e.lower().rstrip(".") for e in EMAIL_RE.findall(text))
                if facts["emails"] and emails and not emails & set(facts["emails"]):
                    add("medium", f"Contact email {', '.join(sorted(emails))} differs from the funnel's {', '.join(facts['emails'])}")
                if meta["kind"] in ("privacy", "terms", "refund") and not emails:
                    add("medium", "No contact email on the page")
                if facts["companies"] and not any(c.lower() in text.lower() for c in facts["companies"]):
                    add("medium", f"Company name ({facts['companies'][0]}) not on the page")
                # clauses by page type
                checks = list(CLAUSES.get(meta["kind"], []))
                missing = [name for name, rx in checks if not re.search(rx, text, re.I)]
                if meta["kind"] == "terms" and facts["finance_niche"] and not re.search(r"not (financial|investment) advice|educational purposes", text, re.I):
                    missing.append("“not financial advice / education only” (finance niche)")
                if missing:
                    add("medium", "Missing common clauses (check with the client or their lawyer): " + "; ".join(missing))
                if meta["kind"] == "privacy" and (facts["sms_on_funnel"] or re.search(r"text messag|\bsms\b", text, re.I)):
                    sms_missing = [name for name, rx in CLAUSES["sms"] if not re.search(rx, text, re.I)]
                    if sms_missing:
                        add("high", "SMS section is missing carrier-required wording: " + "; ".join(sms_missing))
                if mb.get("justified"):
                    add("medium", f"Justified text on phones ({mb['justified']} blocks): uneven word gaps; use left-aligned text")
            pages.append({"url": u, "path": path, "kind": meta["kind"], "name": meta["name"], "linked_from": meta["linked_from"],
                          "status": dk.get("status"), "final_url": dk.get("final_url"), "title": dk.get("title"),
                          "words": len(re.findall(r"\w+", text)), "findings": F})
        browser.close()

    # 3. screenshots + standard scan for the other scripts
    if not a.no_scan:
        for pg in pages:
            if pg["status"] and pg["status"] < 400 and not os.path.exists(os.path.join(a.scans, f"{slug(pg['url'])}-desktop.json")):
                subprocess.run([sys.executable, os.path.join(HERE, "scan_page.py"), pg["url"], "--out", a.scans, "--no-images"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    rank = {"critical": 0, "high": 1, "medium": 2, "polish": 3}
    rows = []
    for pg in pages:
        worst = min([rank[f["severity"]] for f in pg["findings"]] or [9])
        rows.append({"page": pg["path"], "name": pg["name"], "status": "fail" if worst <= 1 else "warn" if worst <= 3 else "ok",
                     "status_text": {0: "Missing", 1: "Fix", 2: "Check", 3: "Polish"}.get(worst, "OK"),
                     "findings": [f["text"] for f in sorted(pg["findings"], key=lambda f: rank[f["severity"]])], "action": ""})
    res = {"funnel_facts": facts, "footer_matrix": matrix, "footer_issues": footer_issues, "pages": pages,
           "report_block": {"intro": "Every legal page linked from the funnel footer, checked against what this funnel sells.",
                            "matrix": matrix, "rows": rows}}
    path = write_json(os.path.join(out, "legal.json"), res)

    print("Funnel facts:", {k: v for k, v in facts.items() if k != "brands"})
    print("Brand names used for casing checks:", ", ".join(facts["brands"][:10]))
    print("Footer links per funnel page:")
    for pth, k in matrix.items():
        print(f"  {pth:40s} {', '.join(k) or 'NONE'}")
    for x in footer_issues:
        print("  PROBLEM", x)
    for pg in pages:
        print(f"\n== {pg['kind']:10s} {pg['url']}  (HTTP {pg['status']}, title: {pg['title']!r}, {pg['words']} words)")
        for f in sorted(pg["findings"], key=lambda f: rank[f["severity"]]):
            print(f"   [{f['severity']}] {f['text']}")
        if not pg["findings"]:
            print("   OK")
    print("->", path)


if __name__ == "__main__":
    main()
