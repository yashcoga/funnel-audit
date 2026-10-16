"""Build the funnel audit report (one clickable HTML file) from a findings JSON file.

Usage:
  python build_report.py findings.json --out FOLDER [--preview DIR] [--pdf]

Writes FOLDER/<file_name>.html (self-contained: screenshots are embedded, works offline and can be shared as one
file), checks its links, and saves preview screenshots (desktop + phone) to --preview (default: next to
findings.json) so the result can be looked at. --pdf also writes a print-ready PDF (needs PyMuPDF for the check).
See report_example.json (next to this script) for every field. Text fields accept simple HTML (<b>, <i>, <br>,
<a href>, <span class="mono">). Bare https:// links and funnel paths like /join are made clickable automatically.
Screenshot paths are relative to the findings file (or absolute).
"""
import argparse
import base64
import html
import io
import os
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import launch, read_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SEV = {"critical": ("crit", "c-crit", "Critical"), "high": ("high", "c-high", "High"),
       "medium": ("med", "c-med", "Medium"), "polish": ("", "c-pol", "Polish")}
STATUS = {"ok": "pass", "pass": "pass", "warn": "warn", "fail": "fail"}
EXTRA_CSS = """
.legal-pg{font-size:13px;color:var(--muted)}
#legal ~ .tbl td ul,section td ul{margin:0;padding-left:18px}
.verdict.ready{background:var(--accent-soft);border-color:var(--accent)}
.verdict.ready strong.v{color:var(--accent)}
.verdict.mostly{background:var(--high-soft);border-color:var(--high)}
.verdict.mostly strong.v{color:var(--high)}
.cc-list{display:flex;flex-direction:column;gap:12px}
.cc{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 16px;display:flex;flex-direction:column;gap:8px}
.cc-head{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:baseline}
.cc-head .pg{font-size:13px;color:var(--muted)}
.cc .see{font-size:15px}.cc .rec{font-size:15px;background:var(--to);border-radius:6px;padding:8px 10px}
.cc p,.verdict p,.fix p{max-width:none}
@media print{.cc{break-inside:avoid}}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


class Linker:
    def __init__(self, base, paths):
        self.base = base.rstrip("/") if base else None
        self.paths = sorted({p for p in paths if p and p != "/"}, key=len, reverse=True)

    def __call__(self, s):
        if s is None:
            return ""
        s = str(s)
        out, in_a = [], False
        for part in re.split(r"(<[^>]+>)", s):
            if part.startswith("<"):
                if re.match(r"<a\b", part, re.I):
                    in_a = True
                elif re.match(r"</a>", part, re.I):
                    in_a = False
                out.append(part)
                continue
            if not in_a:
                part = re.sub(r"(?<![\"'=])\b(https?://[^\s<>\"')]+[^\s<>\"').,;:])",
                              lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', part)
                if self.base and self.paths:
                    rx = r"(?<![\w/.:>\"-])(" + "|".join(re.escape(p) for p in self.paths) + r")(?![\w/-])"
                    part = re.sub(rx, lambda m: f'<a href="{self.base}{m.group(1)}">{m.group(1)}</a>', part)
            out.append(part)
        return "".join(out)


def img_uri(path, mobile=False):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    maxw = 600 if mobile else 1100
    if im.width > maxw:
        im = im.resize((maxw, int(im.height * maxw / im.width)))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=82, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def figure(shot, base_dir, L):
    p = shot["file"] if os.path.isabs(shot["file"]) else os.path.join(base_dir, shot["file"])
    if not os.path.exists(p):
        return f'<p class="note">[missing screenshot: {esc(shot["file"])}]</p>'
    mob = shot.get("mobile", False)
    cap = shot.get("caption", "")
    return (f'<figure class="shot{" mobile" if mob else ""}"><img src="{img_uri(p, mob)}" alt="{esc(re.sub("<[^>]+>", "", cap))}">'
            f'<figcaption>{L(cap)}</figcaption></figure>')


def score_cls(v):
    try:
        v = int(v)
    except Exception:
        return ""
    return "s-bad" if v < 50 else ("s-mid" if v < 90 else "s-good")


def build_html(d, base_dir):
    paths = set()
    for st in d.get("funnel_map", []):
        u = st.get("url") or ""
        paths.add(urllib.parse.urlparse(u).path if "://" in u else u)
    for r in d.get("speed", {}).get("rows", []):
        paths.add(r.get("page"))
    for r in (d.get("legal_pages") or {}).get("rows", []):
        paths.add(r.get("page"))
    L = Linker(d.get("base_url"), paths)
    fixes = d.get("fixes", [])
    copy = d.get("copy_fixes", [])
    cc = d.get("copy_audit", {})
    nav = [("verdict", "Verdict")]
    if d.get("funnel_steps"):
        nav.append(("steps", "Steps &amp; SEO"))
    if d.get("switch_over"):
        nav.append(("switch", "Switch-over"))
    nav += [("index", "All fixes"), ("map", "Funnel map"), ("fixes", "Fix details")]
    if copy:
        nav.append(("copy", "Copy fixes"))
    if cc.get("items"):
        nav.append(("conversion", "Copy &amp; conversion"))
    nav.append(("tracking", "Tracking"))
    if d.get("dynamic_dates"):
        nav.append(("dates", "Dates"))
    if d.get("legal_pages"):
        nav.append(("legal", "Legal pages"))
    nav.append(("speed", "Speed"))
    if d.get("recheck"):
        nav.append(("recheck", "Re-check"))
    if d.get("sop"):
        nav.append(("sop", "Test SOP"))
    h = []
    A = h.append
    title = esc(d.get("report_title") or f"{d['funnel_name']} Funnel Audit")
    css = open(os.path.join(HERE, "report_style.css"), encoding="utf-8").read() + EXTRA_CSS
    A(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;700;800&family=Source+Sans+3:ital,wght@0,400;0,600;0,700;1,400&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
{css}
</style>
</head>
<body>
<div class="wrap">
<header>
  <span class="eyebrow">Pre-launch funnel audit · tested {esc(d.get('tested_on', ''))}</span>
  <h1>{esc(d['funnel_name'])}</h1>
  <div class="meta">""")
    if d.get("start_url"):
        A(f'<span>Start page: <a class="mono" href="{esc(d["start_url"])}">{esc(d["start_url"].split("://")[-1])}</a></span>')
    A(f'<span>{esc(d.get("devices", "Desktop 1440 px + iPhone 390 px"))}</span>')
    if d.get("test_code"):
        A(f'<span>Coupon code: <span class="mono">{esc(d["test_code"])}</span></span>')
    if d.get("sheet_url"):
        A(f'<span><a href="{esc(d["sheet_url"])}">Marketing sheet</a></span>')
    A("</div>\n<nav class=\"toc\">" + "".join(f'<a href="#{i}">{t}</a>' for i, t in nav) + "</nav>\n</header>")

    v = d.get("verdict", {})
    vcls = {"ready": "ready", "ready_with_fixes": "mostly"}.get(v.get("status"), "")
    A(f'<section aria-labelledby="verdict"><h2 id="verdict">Verdict</h2><div class="verdict {vcls}">'
      f'<strong class="v">{L(v.get("headline", ""))}</strong>')
    if v.get("intro"):
        A(f"<p>{L(v['intro'])}</p>")
    if v.get("blockers"):
        A("<ol>" + "".join(f"<li>{L(x)}</li>" for x in v["blockers"]) + "</ol>")
    A("</div>")
    if v.get("working"):
        A('<div class="good"><h3>Working correctly</h3><ul>' + "".join(f"<li>{L(x)}</li>" for x in v["working"]) + "</ul></div>")
    A("</section>")

    # funnel steps (from GHL), wrong-page links and SEO / share meta tags
    fs = d.get("funnel_steps")
    if fs:
        A('<section aria-labelledby="steps"><h2 id="steps">Funnel steps, wrong-page links &amp; SEO tags</h2>')
        if fs.get("intro"):
            A(f"<p>{L(fs['intro'])}</p>")
        wl = fs.get("wrong_links") or []
        if wl:
            A('<h3>Links that land outside this funnel</h3><div class="tbl"><table><thead><tr><th>On page</th><th>Link</th><th>Lands on</th></tr></thead><tbody>')
            for w in wl:
                A(f'<tr><td>{L(w["page"])}</td><td>{L(w["link"])}</td><td class="fail">{L(w["lands_on"])}</td></tr>')
            A("</tbody></table></div>")
        else:
            A('<p class="pass">Every link on the funnel pages stays inside this funnel.</p>')
        A('<div class="tbl"><table><thead><tr><th>Page</th><th>Title now</th><th>Result</th><th>What to fix</th></tr></thead><tbody>')
        for r in fs.get("rows", []):
            iss = r.get("issues") or []
            fix = ("<ul>" + "".join(f"<li>{L(x)}</li>" for x in iss) + "</ul>") if iss else "None"
            A(f'<tr><td>{L(r["page"])}<br><span class="legal-pg">{esc(r.get("kind", ""))}</span></td><td>{L(r.get("title") or "")}</td>'
              f'<td class="{STATUS.get(r.get("status"), "")}" style="white-space:nowrap">{L(r.get("status_text", ""))}</td><td>{fix}</td></tr>')
        A("</tbody></table></div>")
        if fs.get("notes"):
            A(f'<p class="note">{L(fs["notes"])}</p>')
        A("</section>")

    # switch-over from the live funnel (ads + automations run on the old opt-in)
    so = d.get("switch_over")
    if so:
        A('<section aria-labelledby="switch"><h2 id="switch">Switch-over from the live funnel</h2>')
        if so.get("intro"):
            A(f"<p>{L(so['intro'])}</p>")
        if so.get("verdict"):
            A(f'<div class="verdict"><strong class="v">{L(so["verdict"])}</strong></div>')
        A('<div class="tbl"><table><thead><tr><th>Check</th><th>Old (live now)</th><th>New</th><th>Result</th><th>What to do</th></tr></thead><tbody>')
        for r in so.get("rows", []):
            A(f'<tr><td>{L(r["check"])}</td><td class="mono">{L(r["old"])}</td><td class="mono">{L(r["new"])}</td>'
              f'<td class="{STATUS.get(r.get("status"), "")}" style="white-space:nowrap">{L(r.get("status_text", ""))}</td><td>{L(r.get("action", ""))}</td></tr>')
        A("</tbody></table></div>")
        if so.get("checklist"):
            A('<h3>Check in GHL before switching (can\'t be seen from outside)</h3><ol>'
              + "".join(f"<li>{L(x)}</li>" for x in so["checklist"]) + "</ol>")
        A("</section>")

    # index
    rows = "".join(
        f'<tr><td class="num"><a href="#{esc(f["id"].lower())}">{esc(f["id"])}</a></td><td><a href="#{esc(f["id"].lower())}">{esc(re.sub("<[^>]+>", "", f["title"]))}</a></td>'
        f'<td><span class="chip {SEV[f["severity"]][1]}">{esc(f.get("chip") or SEV[f["severity"]][2])}</span></td></tr>' for f in fixes)
    if copy:
        rows += f'<tr><td class="num"><a href="#copy">P</a></td><td><a href="#copy">Copy fixes ({len(copy)} lines)</a></td><td><span class="chip c-pol">Polish</span></td></tr>'
    if cc.get("items"):
        rows += f'<tr><td class="num"><a href="#conversion">CC</a></td><td><a href="#conversion">Copy &amp; conversion review ({len(cc["items"])} points)</a></td><td><span class="chip c-high">Review</span></td></tr>'
    A(f'<section aria-labelledby="index"><h2 id="index">All fixes at a glance</h2><p class="note">Click any item to jump to it. '
      f'Each item has a “Back to all fixes” link.</p><div class="tbl"><table><thead><tr><th>#</th><th>Fix</th><th>Priority</th></tr></thead>'
      f"<tbody>{rows}</tbody></table></div></section>")

    # funnel map
    A('<section aria-labelledby="map"><h2 id="map">Funnel map (as tested)</h2><div class="flow">')
    for st in d.get("funnel_map", []):
        u = st.get("url", "")
        full = u if "://" in u else (d.get("base_url", "").rstrip("/") + u if u else "")
        shown = urllib.parse.urlparse(full).path if full else ""
        A(f'<div class="step"><h3>{esc(st["title"])}</h3><span class="u">'
          + (f'<a href="{esc(full)}">{esc(shown or full)}</a>' if full else "")
          + (f' · {L(st["meta"])}' if st.get("meta") else "") + "</span>"
          + ("<ul>" + "".join(f"<li>{L(x)}</li>" for x in st.get("items", [])) + "</ul>" if st.get("items") else "") + "</div>")
    A("</div></section>")

    # fixes
    A('<section aria-labelledby="fixes"><h2 id="fixes">Fix list</h2><div class="legend"><span class="chip c-crit">Critical · before ads</span>'
      '<span class="chip c-high">High</span><span class="chip c-med">Medium</span><span class="chip c-pol">Polish</span></div><div class="fixes">')
    for f in fixes:
        cls, chip, label = SEV[f["severity"]]
        A(f'<article class="fix {cls}" id="{esc(f["id"].lower())}"><div class="fix-head"><span class="id">{esc(f["id"])}</span>'
          f'<h3>{L(f["title"])}</h3><span class="chip {chip}">{esc(f.get("chip") or label)}</span></div>')
        if f.get("where"):
            A(f'<p class="where">{L(f["where"])}</p>')
        if f.get("body"):
            A(f"<p>{L(f['body'])}</p>")
        if f.get("bullets"):
            A("<ul>" + "".join(f"<li>{L(x)}</li>" for x in f["bullets"]) + "</ul>")
        for s in f.get("screenshots", []):
            A(figure(s, base_dir, L))
        if f.get("now") or f.get("change_to"):
            A(f'<div class="ba"><div class="now"><span class="lbl">{esc(f.get("now_label", "Now"))}</span>{L(f.get("now", ""))}</div>'
              f'<div class="to"><span class="lbl">{esc(f.get("change_label", "Change to"))}</span>{L(f.get("change_to", ""))}</div></div>')
        if f.get("note"):
            A(f'<p class="note">{L(f["note"])}</p>')
        A('<a class="back" href="#index">Back to all fixes ↑</a></article>')
    A("</div></section>")

    if copy:
        A('<section aria-labelledby="copy"><h2 id="copy">Copy fixes (polish)</h2><div class="copy-list">'
          '<div class="copy-row h"><span>Where</span><span>Now</span><span>Change to</span></div>')
        for c in copy:
            A(f'<div class="copy-row"><span>{L(c["where"])}' + (f'<br><span class="pg">{esc(c["device"])}</span>' if c.get("device") else "")
              + f'</span><span class="n">{L(c["now"])}</span><span class="t">{L(c["to"])}</span></div>')
        A("</div>")
        if d.get("copy_screenshots"):
            A('<div class="shots-row">' + "".join(figure(s, base_dir, L) for s in d["copy_screenshots"]) + "</div>")
        A('<a class="back" href="#index">Back to all fixes ↑</a></section>')

    if cc.get("items"):
        A('<section aria-labelledby="conversion"><h2 id="conversion">Copy &amp; conversion review</h2>')
        if cc.get("summary"):
            A(f"<p>{L(cc['summary'])}</p>")
        A('<div class="cc-list">')
        for it in cc["items"]:
            sev = SEV.get(it.get("priority", "medium"), SEV["medium"])
            A(f'<div class="cc"><div class="cc-head"><h3>{L(it["area"])}</h3><span class="chip {sev[1]}">{esc(sev[2])}</span>'
              f'<span class="pg">{L(it.get("page", ""))}</span></div><p class="see">{L(it["observation"])}</p>'
              f'<p class="rec"><b>Recommendation:</b> {L(it["recommendation"])}</p></div>')
        A('</div><a class="back" href="#index">Back to all fixes ↑</a></section>')

    t = d.get("tracking", {})
    A('<section aria-labelledby="tracking"><h2 id="tracking">Tracking seen during the test</h2>')
    if t.get("intro"):
        A(f"<p>{L(t['intro'])}</p>")
    A('<div class="tbl"><table><thead><tr><th>Page / action</th><th>Events</th><th>Status</th></tr></thead><tbody>')
    for r in t.get("rows", []):
        A(f'<tr><td>{L(r["step"])}</td><td>{L(r["events"])}</td><td class="{STATUS.get(r.get("status"), "")}">{L(r.get("status_text", ""))}</td></tr>')
    A("</tbody></table></div></section>")

    dd = d.get("dynamic_dates")
    if dd:
        A('<section aria-labelledby="dates"><h2 id="dates">Dates from GHL custom values</h2>')
        if dd.get("intro"):
            A(f"<p>{L(dd['intro'])}</p>")
        A('<div class="tbl"><table><thead><tr><th>Page</th><th>What it shows</th><th>Result</th><th>What to do</th></tr></thead><tbody>')
        for r in dd.get("rows", []):
            A(f'<tr><td>{L(r["page"])}</td><td>{L(r["text"])}</td><td class="{STATUS.get(r.get("status"), "")}">'
              f'{L(r.get("status_text", ""))}</td><td>{L(r.get("action", ""))}</td></tr>')
        A("</tbody></table></div>")
        if dd.get("notes"):
            A(f'<p class="note">{L(dd["notes"])}</p>')
        A("</section>")

    lg = d.get("legal_pages")
    if lg:
        A('<section aria-labelledby="legal"><h2 id="legal">Legal pages in the footer</h2>')
        if lg.get("intro"):
            A(f"<p>{L(lg['intro'])}</p>")
        mx = lg.get("matrix") or {}
        if mx:
            kinds = sorted({k for v in mx.values() for k in v})
            A('<div class="tbl"><table><thead><tr><th>Funnel page</th>' + "".join(f"<th>{esc(k.title())}</th>" for k in kinds)
              + "</tr></thead><tbody>")
            for pg, ks in mx.items():
                A(f"<tr><td>{L(pg)}</td>" + "".join(f'<td class="{"pass" if k in ks else "fail"}">{"✓" if k in ks else "✗"}</td>'
                                                   for k in kinds) + "</tr>")
            A("</tbody></table></div>")
        A('<div class="tbl"><table><thead><tr><th>Legal page</th><th>Result</th><th>What we found</th><th>What to do</th></tr></thead><tbody>')
        for r in lg.get("rows", []):
            found = "".join(f"<li>{L(x)}</li>" for x in r.get("findings", []))
            A(f'<tr><td>{L(r["page"])}' + (f'<br><span class="legal-pg">{esc(r["name"])}</span>' if r.get("name") else "")
              + f'</td><td class="{STATUS.get(r.get("status"), "")}">{L(r.get("status_text", ""))}</td>'
              f'<td>{"<ul>" + found + "</ul>" if found else "No problems found"}</td><td>{L(r.get("action", ""))}</td></tr>')
        A("</tbody></table></div>")
        if lg.get("notes"):
            A(f'<p class="note">{L(lg["notes"])}</p>')
        A("</section>")

    sp = d.get("speed", {})
    A('<section aria-labelledby="speed"><h2 id="speed">Page speed</h2>')
    if sp.get("intro"):
        A(f"<p>{L(sp['intro'])}</p>")
    A('<div class="tbl"><table><thead><tr><th>Page</th><th>Mobile</th><th>Desktop</th><th>Mobile LCP</th><th>Blocking time (mobile)</th>'
      '<th>Layout shift (mobile / desktop)</th><th>Report</th></tr></thead><tbody>')
    for r in sp.get("rows", []):
        rep = []
        if r.get("report_mobile"):
            rep.append(f'<a href="{esc(r["report_mobile"])}">Mobile</a>')
        if r.get("report_desktop"):
            rep.append(f'<a href="{esc(r["report_desktop"])}">Desktop</a>')
        A(f'<tr><td>{L(r["page"])}{esc(r.get("mark", ""))}</td><td class="num"><span class="score {score_cls(r.get("mobile"))}">{esc(r.get("mobile"))}</span></td>'
          f'<td class="num"><span class="score {score_cls(r.get("desktop"))}">{esc(r.get("desktop"))}</span></td><td class="num">{esc(r.get("lcp", ""))}</td>'
          f'<td class="num">{esc(r.get("tbt", ""))}</td><td class="num">{esc(r.get("cls", ""))}</td><td>{" · ".join(rep) or esc(r.get("source", ""))}</td></tr>')
    A("</tbody></table></div>")
    for k in ("field", "notes"):
        if sp.get(k):
            A(f'<p{" class=note" if k == "notes" else ""}>{L(sp[k])}</p>')
    A("</section>")

    rc = d.get("recheck")
    if rc:
        A(f'<section aria-labelledby="recheck"><h2 id="recheck">{esc(rc.get("title", "Re-check of the previous fix list"))}</h2>'
          '<div class="tbl"><table><thead><tr><th>Item</th><th>Status on the live site</th></tr></thead><tbody>')
        for r in rc.get("rows", []):
            A(f'<tr><td>{L(r["item"])}</td><td class="{STATUS.get(r.get("status"), "")}">{L(r["text"])}</td></tr>')
        A("</tbody></table></div></section>")

    sop = d.get("sop")
    if sop:
        A('<section aria-labelledby="sop"><h2 id="sop">Test purchase SOP and clean-up</h2><h3>How to place a test purchase on this funnel</h3>'
          "<ol>" + "".join(f"<li>{L(x)}</li>" for x in sop.get("steps", [])) + "</ol>")
        if sop.get("note"):
            A(f"<p>{L(sop['note'])}</p>")
        if sop.get("cleanup"):
            A("<h3>Test records to clean up</h3><ul>" + "".join(f"<li>{L(x)}</li>" for x in sop["cleanup"]) + "</ul>")
        A("</section>")
    if d.get("not_tested"):
        A('<section aria-labelledby="nt"><h2 id="nt">Not tested, and why</h2><ul>' + "".join(f"<li>{L(x)}</li>" for x in d["not_tested"]) + "</ul></section>")
    A(f'<footer>{L(d.get("footer", "Audit run with automated Chrome (desktop 1440×900, iPhone 390×844), Google PageSpeed Insights and Lighthouse."))}</footer>')
    A("</div>\n</body>\n</html>\n")
    return "\n".join(h)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("findings")
    ap.add_argument("--out", required=True, help="folder for the final report (the user's project folder)")
    ap.add_argument("--preview", default=None, help="folder for preview PNGs (default: next to findings)")
    ap.add_argument("--pdf", action="store_true", help="also write a PDF")
    a = ap.parse_args()
    d = read_json(a.findings)
    base_dir = os.path.dirname(os.path.abspath(a.findings))
    name = d.get("file_name") or f"{d['funnel_name']} - Funnel Audit"
    os.makedirs(a.out, exist_ok=True)
    html_text = build_html(d, base_dir)
    html_path = os.path.join(a.out, name + ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    prev_dir = a.preview or base_dir
    os.makedirs(prev_dir, exist_ok=True)
    uri = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    previews = []
    with sync_playwright() as p:
        b = launch(p)
        # previews + link check on the real file
        for dev, vp in (("desktop", {"width": 1280, "height": 900}), ("phone", {"width": 390, "height": 844})):
            pg = b.new_page(viewport=vp)
            pg.goto(uri, wait_until="networkidle")
            pg.evaluate("document.fonts.ready")
            if dev == "desktop":
                info = pg.evaluate("""() => {
                  const a = [...document.querySelectorAll('a[href]')];
                  const internal = a.filter(x => x.getAttribute('href').startsWith('#'));
                  const broken = internal.filter(x => !document.getElementById(decodeURIComponent(x.getAttribute('href').slice(1)))).map(x => x.getAttribute('href'));
                  return {links: a.length, internal: internal.length, external: a.length - internal.length, broken: [...new Set(broken)],
                          figures: document.querySelectorAll('figure').length, fixes: document.querySelectorAll('article.fix').length,
                          overflow: document.documentElement.scrollWidth > innerWidth + 1};
                }""")
            else:
                info["phone_overflow"] = pg.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
            pth = os.path.join(prev_dir, f"_report-preview-{dev}.png")
            pg.screenshot(path=pth, full_page=False)
            previews.append(pth)
            pg.close()
        pdf_note = ""
        if a.pdf:
            pdf_path = os.path.join(a.out, name + ".pdf")
            print_path = os.path.join(base_dir, "_print.html")
            with open(print_path, "w", encoding="utf-8") as f:
                f.write(html_text.replace('<html lang="en">', '<html lang="en" data-theme="light">', 1))
            footer = (f'<div style="font-size:8px;width:100%;padding:0 12mm;color:#5b665f;display:flex;justify-content:space-between;'
                      f'font-family:Arial"><span>{esc(d["funnel_name"])} · Funnel audit · {esc(d.get("tested_on", ""))}</span>'
                      f'<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')
            pg = b.new_page()
            pg.goto("file:///" + os.path.abspath(print_path).replace("\\", "/"), wait_until="networkidle")
            pg.evaluate("document.fonts.ready")
            pg.emulate_media(media="print", color_scheme="light")
            pg.pdf(path=pdf_path, format="A4", print_background=True, display_header_footer=True,
                   header_template="<span></span>", footer_template=footer,
                   margin={"top": "14mm", "bottom": "16mm", "left": "12mm", "right": "12mm"})
            os.remove(print_path)
            pdf_note = f"\nPDF:  {pdf_path} ({os.path.getsize(pdf_path) // 1024} KB)"
        b.close()
    print(f"HTML: {html_path}  ({os.path.getsize(html_path) // 1024} KB){pdf_note}")
    print(f"  {info['fixes']} fixes, {info['figures']} screenshots, {info['links']} links "
          f"({info['internal']} jump links, {info['external']} external)")
    if info["broken"]:
        print("  BROKEN jump links:", info["broken"])
    if info["overflow"] or info.get("phone_overflow"):
        print("  WARNING: the report scrolls sideways on", "phone" if info.get("phone_overflow") else "desktop")
    print("Look at:", ", ".join(previews))


if __name__ == "__main__":
    main()
