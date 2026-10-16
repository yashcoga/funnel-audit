"""Switch-over check: compare the opt-in that ads and automations use TODAY with the new opt-in that replaces it.

Usage:
  python compare_live_funnel.py --old OLD_OPTIN_URL --new NEW_OPTIN_URL --out DIR [--device desktop|mobile]

When a new design replaces a funnel that already has live ads and GoHighLevel automations, the safe switch is to put
the new design INTO THE OLD funnel's opt-in step, keeping its URL, its form and its pixels. Workflows are usually
triggered by "Form submitted: <that form>" or a page view / opt-in on <that funnel step>, and ads point at that URL
and optimise on that pixel. A new page with a new form, a new funnel or no pixel silently breaks all of them.

The script opens both pages (pixels blocked, nothing is submitted) and compares what can be seen from outside:
  - address: domain and path (where the ads point)
  - GoHighLevel funnel / step / page IDs (same step = the new design sits in the old step)
  - form IDs (native form, survey or embedded widget) and the form fields (name, type, required, hidden)
  - the next step the form goes to (GHL funnelNextPageId)
  - Meta pixel IDs, Google tags (G-/AW-/GTM-), TikTok pixel IDs
It prints one line per check (SAME / DIFFERENT / MISSING) with what it breaks, and writes DIR/switch-over.json with a
ready "switch_over" block for findings.json. GHL workflows themselves can't be seen from outside: the report lists
them as a checklist for the person with GHL access.
"""
import argparse
import os
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import launch, new_context, prepare, write_json  # noqa: E402

COLLECT_JS = r"""
() => {
  // GHL keeps page data in window.__NUXT__; walk it for the funnel/step/page/form IDs.
  const ids = {funnelId: new Set(), stepId: new Set(), pageId: new Set(), funnelNextPageId: new Set(), locationId: new Set(),
               formId: new Set(), surveyId: new Set()};
  const forms = [];
  const seen = new WeakSet();
  const walk = (o, depth) => {
    if (!o || typeof o !== 'object' || depth > 60 || seen.has(o)) return;
    seen.add(o);
    // a GHL form element: {formId: {value: <id>, text: <form name>}, action: {value: 'url'|...}, visitWebsite: {url}}
    if (o.formId && typeof o.formId === 'object' && typeof o.formId.value === 'string') {
      const vw = o.visitWebsite && (o.visitWebsite.url !== undefined ? o.visitWebsite : o.visitWebsite.value);
      const act = o.action && (typeof o.action.value === 'string' ? o.action.value : '');
      forms.push({id: o.formId.value, name: o.formId.text || '', action: act || '',
                  redirect: (act === 'url' && vw && typeof vw.url === 'string') ? vw.url : ''});
    }
    for (const [k, v] of Object.entries(o)) {
      if (k in ids && typeof v === 'string' && v.length >= 6 && v.length <= 40) ids[k].add(v);
      else if (k in ids && v && typeof v === 'object' && typeof v.value === 'string' && v.value.length >= 6) ids[k].add(v.value);
      if (v && typeof v === 'object') walk(v, depth + 1);
    }
  };
  // Nuxt 3 ships page data as a flat "devalue" array in <script id="__NUXT_DATA__">: numbers are references.
  const nd = document.getElementById('__NUXT_DATA__');
  if (nd) {
    try {
      const arr = JSON.parse(nd.textContent), memo = new Map();
      const TAGS = new Set(['Reactive', 'ShallowReactive', 'Ref', 'ShallowRef', 'EmptyRef', 'EmptyShallowRef', 'NuxtError', 'Set', 'Map', 'Date', 'Object']);
      const hyd = (i, d) => {
        if (typeof i !== 'number' || i < 0 || i >= arr.length) return i;
        if (memo.has(i)) return memo.get(i);
        const v = arr[i];
        if (d > 80 || v === null || typeof v !== 'object') { memo.set(i, v); return v; }
        if (Array.isArray(v)) {
          if (typeof v[0] === 'string' && TAGS.has(v[0])) { const r = v.length > 1 ? hyd(v[1], d + 1) : null; memo.set(i, r); return r; }
          const r = []; memo.set(i, r); for (const x of v) r.push(hyd(x, d + 1)); return r;
        }
        const r = {}; memo.set(i, r); for (const [k, x] of Object.entries(v)) r[k] = hyd(x, d + 1); return r;
      };
      walk(hyd(0, 0), 0);
    } catch (e) {}
  }
  try { walk(window.__NUXT__, 0); } catch (e) {}
  for (const f of forms) ids.formId.add(f.id);
  const html = document.documentElement.outerHTML;
  // native GHL form/survey: field wrappers are id="el_<FORMID>_<field>_<n>"
  for (const m of html.matchAll(/id="el_([A-Za-z0-9]{15,25})_/g)) ids.formId.add(m[1]);
  for (const f of document.querySelectorAll('iframe[src*="/widget/form/"], iframe[src*="/widget/survey/"]')) {
    const m = f.src.match(/\/widget\/(form|survey)\/([A-Za-z0-9]+)/); if (m) ids[m[1] === 'form' ? 'formId' : 'surveyId'].add(m[2]);
  }
  const fields = [...document.querySelectorAll('input, select, textarea')]
    .filter(i => !['submit', 'button'].includes(i.type) && !/turnstile|recaptcha|captcha/i.test(i.name || ''))
    .map(i => ({name: i.name || i.id || '', type: i.type || i.tagName.toLowerCase(), required: i.required || i.getAttribute('aria-required') === 'true',
                hidden: i.type === 'hidden', label: (i.labels && i.labels[0] ? i.labels[0].innerText : i.placeholder || '').trim().slice(0, 60)}))
    .filter(f => f.name);
  const uniq = (re) => [...new Set([...html.matchAll(re)].map(m => m[1]))];
  const pixels = {
    meta: uniq(/fbq\(\s*['"]init['"]\s*,\s*['"](\d{10,20})['"]/g),
    google: uniq(/\b((?:G|AW|GTM|UA)-[A-Z0-9-]{4,20})\b/g),
    tiktok: uniq(/ttq\.load\(\s*['"]([A-Z0-9]{10,30})['"]/g),
  };
  const out = {}; for (const k in ids) out[k] = [...ids[k]];
  const fseen = new Set();
  return {title: document.title, ids: out, fields, pixels, forms: forms.filter(f => !fseen.has(f.id) && fseen.add(f.id))};
}
"""


def collect(browser, url, device):
    ctx, log = new_context(browser, device, block_forms=True)
    page = ctx.new_page()
    reqs = []
    page.on("request", lambda r: reqs.append(r.url))
    info = prepare(page, url, wait_ms=4500)
    data = page.evaluate(COLLECT_JS)
    # IDs GHL sends on page load (page-view / attribution calls), and pixel IDs in blocked pixel requests
    for u in reqs:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(u).query))
        for k in ("funnelId", "pageId", "stepId", "formId"):
            if q.get(k) and q[k] not in data["ids"].setdefault(k, []):
                data["ids"][k].append(q[k])
        if "facebook.com/tr" in u and q.get("id") and q["id"] not in data["pixels"]["meta"]:
            data["pixels"]["meta"].append(q["id"])
    data["final_url"] = info["final_url"]
    data["status"] = info["status"]
    ctx.close()
    return data


def addr(url):
    p = urllib.parse.urlparse(url)
    return p.netloc.lower(), (p.path.rstrip("/") or "/")


def row(check, old, new, status, impact, action):
    return {"check": check, "old": old, "new": new, "status": status, "impact": impact, "action": action}


def fmt(v):
    if isinstance(v, (list, tuple, set)):
        return ", ".join(v) if v else "none"
    return v or "none"


def compare(o, n):
    rows = []
    od, op = addr(o["final_url"])
    nd, np_ = addr(n["final_url"])
    same_addr = (od, op) == (nd, np_)
    rows.append(row("Page address (where the ads point)", od + op, nd + np_, "same" if same_addr else "different",
                    "" if same_addr else "Ads, emails and links still point to the old address.",
                    "" if same_addr else f"Publish the new design at {od}{op} (the old funnel's opt-in step). Don't send ads to a new URL."))

    oi, ni = o["ids"], n["ids"]
    for key, label, impact in [
        ("funnelId", "GHL funnel", "Workflows and reports filtered on the old funnel won't see new sign-ups."),
        ("stepId", "GHL funnel step", "Triggers on the old opt-in step (page view / opt-in) won't fire."),
    ]:
        a, b = set(oi.get(key, [])), set(ni.get(key, []))
        if not a and not b:
            continue
        st = "same" if a and a == b else "different"
        rows.append(row(label, fmt(sorted(a)), fmt(sorted(b)), st, "" if st == "same" else impact,
                        "" if st == "same" else "Paste the new design into the OLD funnel's opt-in step instead of using the new funnel's step."))

    a, b = set(oi.get("formId", []) + oi.get("surveyId", [])), set(ni.get("formId", []) + ni.get("surveyId", []))
    names = {f["id"]: f["name"] for f in o.get("forms", []) + n.get("forms", []) if f.get("name")}
    show = lambda s: ", ".join(f"{i} ({names[i]})" if i in names else i for i in sorted(s)) or "none"
    if a or b:
        st = "same" if a and a == b else ("missing" if not b else "different")
        rows.append(row("Form (the ID GHL workflows trigger on)", show(a), show(b), st,
                        "" if st == "same" else "Every workflow with the trigger \"Form submitted: <old form>\" stops: no tags, no emails, no SMS, no calendar invite.",
                        "" if st == "same" else f"Use the old form ({show(a)}) in the new design, or add the new form to every workflow trigger that uses the old one."))

    ofr = sorted({f["redirect"] for f in o.get("forms", []) if f.get("redirect")})
    nfr = sorted({f["redirect"] for f in n.get("forms", []) if f.get("redirect")})
    if any(f.get("action") == "none" for f in n.get("forms", [])):
        # GHL form element "On submit: none" = the form shows its own thank-you message and the visitor stays put
        rows.append(row("Where the form sends people", fmt(ofr) if ofr else "next funnel step",
                        "nowhere: shows the form's thank-you message", "missing",
                        "Sign-ups stay on the opt-in and never reach the upsell or the thank-you page.",
                        "GHL funnel editor → this step → click the form element → Action on submit: \"Go to next step\" (or the "
                        "upsell URL). It's a page setting, so other funnels using the same form are not affected."))
    elif ofr or nfr:
        st = "same" if ofr == nfr else "different"
        rows.append(row("Where the form sends people", fmt(ofr), fmt(nfr) if nfr else "next funnel step", st,
                        "" if st == "same" else "After the switch, sign-ups go somewhere else than today (fine if intended).",
                        "" if st == "same" else "Confirm the new path (upsell → downsell → thank-you) is the one you want, and retire the old next page on purpose."))

    of = {f["name"]: f for f in o["fields"]}
    nf = {f["name"]: f for f in n["fields"]}
    if not of:
        rows.append(row("Form fields", "not visible (the form loads on click)", fmt(sorted(nf)), "unknown",
                        "Couldn't read the old form's fields from the page.",
                        "Open both forms in GHL → Sites → Forms and check the new one has every field (and hidden field) the old one has."))
    else:
        missing = [k for k in of if k not in nf]
        extra = [k for k in nf if k not in of]
        req_changed = [k for k in of if k in nf and of[k]["required"] != nf[k]["required"]]
        st = "same" if not (missing or extra or req_changed) else "different"
        detail = "; ".join(x for x in [
            f"only on old: {', '.join(missing)}" if missing else "",
            f"only on new: {', '.join(extra)}" if extra else "",
            f"required changed: {', '.join(req_changed)}" if req_changed else ""] if x)
        rows.append(row("Form fields", fmt(sorted(of)), fmt(sorted(nf)), st,
                        "" if st == "same" else f"{detail}. Workflows, custom fields and SMS that use a missing field get no data.",
                        "" if st == "same" else "Keep every field the old form collects (including hidden ones), with the same field names."))

    a, b = set(oi.get("funnelNextPageId", [])), set(ni.get("funnelNextPageId", []))
    if a or b:
        st = "same" if a and a == b else "different"
        rows.append(row("Next step after the form (GHL page ID)", fmt(sorted(a)), fmt(sorted(b)), st,
                        "" if st == "same" else "Sign-ups land on a different next page than today.",
                        "" if st == "same" else "Check the form/step goes to the new upsell; confirm the old thank-you path is retired on purpose."))

    for key, label in [("meta", "Meta pixel"), ("google", "Google tags"), ("tiktok", "TikTok pixel")]:
        a, b = set(o["pixels"].get(key, [])), set(n["pixels"].get(key, []))
        if not a and not b:
            continue
        lost = sorted(a - b)
        st = "same" if not lost and a == b else ("missing" if lost else "different")
        rows.append(row(label, fmt(sorted(a)), fmt(sorted(b)), st,
                        "" if st == "same" else (f"The ads optimise on {', '.join(lost)}; the new page doesn't load it, so leads stop being counted." if lost else "New tags the old page didn't have."),
                        "" if st == "same" else ("Keep the same pixel(s) on the new page (put the design in the old step, or add them to the new funnel's tracking code)." if lost else "Confirm the new tags are wanted.")))
    return rows


CHECKLIST = [
    "GHL → Automation → Workflows: search every workflow for the old form and the old funnel/step in its triggers (Form Submitted, Funnel/Website PageView, Opt-in, Order Submitted). Note each one.",
    "GHL → Sites → Funnels: put the new design into the OLD funnel's opt-in step (same URL, same form). Only if that's impossible, add the new form/step to every trigger found above.",
    "Meta Ads Manager: confirm every live ad's website URL is the old opt-in URL, and the pixel on the page is the one the ad set optimises on.",
    "Emails, SMS, bio links and QR codes that link to the old opt-in still work after the switch.",
    "After switching: one test sign-up, then check in GHL that the contact got the same tags, workflow and messages as a sign-up on the old page.",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", help="opt-in that live ads and automations use today")
    ap.add_argument("--old-json", help="switch-over.json from an earlier run: compare with the old page as it was "
                                       "then (use after the switch, when the old page has been replaced)")
    ap.add_argument("--new", required=True, help="new opt-in that replaces it")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="desktop", choices=["desktop", "mobile"])
    a = ap.parse_args()
    if not (a.old or a.old_json):
        ap.error("give --old URL or --old-json FILE")
    os.makedirs(a.out, exist_ok=True)
    with sync_playwright() as p:
        browser = launch(p)
        if a.old_json:
            import json
            o = json.load(open(a.old_json, encoding="utf-8"))["old"]
            o["title"] = f"{o['title']} (snapshot)"
        else:
            o = collect(browser, a.old, a.device)
        n = collect(browser, a.new, a.device)
        browser.close()
    if not a.old_json and (o.get("status") or 200) >= 400:
        print(f"!! The old opt-in returns HTTP {o['status']}. If ads still point at it, they land on an error page.\n"
              f"   To compare with the old page as it was, re-run with --old-json <earlier switch-over.json>.\n")
    rows = compare(o, n)
    bad = [r for r in rows if r["status"] not in ("same", "unknown")]
    critical = {"Form (the ID GHL workflows trigger on)", "GHL funnel step", "Meta pixel", "Page address (where the ads point)"}
    if any(r["check"] == "Where the form sends people" and r["status"] == "missing" for r in rows):
        critical.add("Where the form sends people")
    if any(r["check"].startswith("Form (") and r["status"] == "same" for r in rows):
        critical.discard("GHL funnel step")   # same form: only step-triggered workflows are at risk -> check in GHL
    short = lambda c: re.sub(r"\s*\(.*?\)", "", c).lower()
    verdict = ("Safe: the new opt-in keeps the old address, form and pixels." if not bad else
               f"Not safe to switch as built: {len(bad)} things differ ({', '.join(short(r['check']) for r in bad)}).")
    print(f"OLD  {o['final_url']}  ({o['title']!r}, HTTP {o['status']})")
    print(f"NEW  {n['final_url']}  ({n['title']!r}, HTTP {n['status']})\n")
    for r in rows:
        mark = {"same": "SAME     ", "different": "DIFFERENT", "missing": "MISSING  ", "unknown": "CHECK    "}[r["status"]]
        print(f"  {mark} {r['check']}\n            old: {r['old']}\n            new: {r['new']}")
        if r["impact"]:
            print(f"            -> {r['impact']}\n            fix: {r['action']}")
    print(f"\n{verdict}")
    print("\nCheck in GHL (can't be seen from outside):")
    for c in CHECKLIST:
        print(f"  - {c}")
    block = {
        "intro": f"Ads and automations run on the old opt-in today ({o['final_url']}). The new opt-in was compared with it.",
        "verdict": verdict,
        "rows": [{"check": r["check"], "old": r["old"], "new": r["new"],
                  "status": "ok" if r["status"] == "same" else ("fail" if r["check"] in critical and r["status"] != "unknown" else "warn"),
                  "status_text": {"same": "Same", "different": "Different", "missing": "Missing on new", "unknown": "Check in GHL"}[r["status"]],
                  "action": r["action"] or "None"} for r in rows],
        "checklist": CHECKLIST,
    }
    path = write_json(os.path.join(a.out, "switch-over.json"), {"old": o, "new": n, "rows": rows, "verdict": verdict, "switch_over": block})
    print(f"\n-> {path}  (copy \"switch_over\" into findings.json)")


if __name__ == "__main__":
    main()
