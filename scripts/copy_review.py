"""Prepare the copy audit: everything Claude needs to review the funnel's copy, in one readable file.

Usage:
  python copy_review.py --scans DIR [--out DIR]

Reads the scan_page.py JSON files (desktop + mobile per page) and writes DIR/copy-review.md and copy-review.json:
  - each page's copy split into sections (by heading), desktop version
  - sentences that appear ONLY on desktop or ONLY on mobile (separate mobile/desktop elements drift apart)
  - all button / CTA texts across the funnel
  - prices, dates/times, percentages and product names per page (to check they agree across pages)
  - readability: words, average sentence length, reading grade, long sentences, ALL-CAPS share, "you" vs "we/I"
  - conversion signals to review: claims (income/results), urgency & scarcity wording, proof (testimonials,
    numbers, logos), risk reversal (guarantee/refund), objection handling (FAQ), and words that suggest
    the page is talking about itself instead of the reader
This does NOT judge the copy; it collects the evidence so the copy review is complete and consistent.
"""
import argparse
import glob
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import read_json, write_json  # noqa: E402

PRICE_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d\d)?(?!\d)")
PCT_RE = re.compile(r"\b\d{1,3}\s?%")
TIME_RE = re.compile(r"\b\d{1,2}(?::\d\d)?\s?(?:am|pm)\s?(?:[ECMP][SD]?T)?\b", re.I)
MONTH_RE = re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?", re.I)
SIGNALS = {
    "claims (check they're backed up / compliant)": r"\b(guarantee[ds]?|proven|results?|income|revenue|make \$|earn|six[- ]figure|7[- ]figure|daily (?:leads|sales)|overnight|instant(?:ly)?|best|#1|number one)\b",
    "urgency & scarcity (must be true)": r"\b(limited|only \d+|seats? (?:are )?limited|last chance|hurry|ends? (?:soon|tonight|today)|disappears|expires?|one[- ]time|today only|before it'?s gone|countdown|don'?t miss)\b",
    "proof (testimonials, numbers, logos)": r"\b(testimonial|review|students?|clients?|customers?|members?|featured (?:in|on)|as seen|case stud(?:y|ies)|trusted by|\d[\d,]+\+? (?:people|entrepreneurs|students|clients))\b",
    "risk reversal": r"\b(money[- ]back|refund|guarantee|risk[- ]free|cancel anytime|no risk)\b",
    "objection handling": r"\b(faq|frequently asked|what if|even if|do i need|will this work|is this for)\b",
    "free / value words": r"\b(free|bonus|value|worth|save|discount|\$\d+ off|% off)\b",
}


def sentences(text):
    text = re.sub(r"[ \t]+", " ", text)
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) > 2]


def syllables(word):
    w = word.lower()
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def readability(text):
    sents = [s for s in sentences(text) if len(s.split()) > 2]
    words = re.findall(r"[A-Za-z']+", text)
    if not sents or not words:
        return {}
    syl = sum(syllables(w) for w in words)
    wps = len(words) / len(sents)
    grade = 0.39 * wps + 11.8 * (syl / len(words)) - 15.59
    caps = sum(1 for w in words if len(w) > 2 and w.isupper())
    you = len(re.findall(r"\b(you|your|you're|yourself)\b", text, re.I))
    we = len(re.findall(r"\b(we|our|us|I|my|me)\b", text))
    long = [s for s in sents if len(s.split()) > 28]
    return {"words": len(words), "sentences": len(sents), "avg_words_per_sentence": round(wps, 1),
            "reading_grade": round(grade, 1), "all_caps_word_share": f"{round(100 * caps / len(words))}%",
            "you_vs_we_I": f"{you} : {we}", "long_sentences": long[:8]}


def sections(text, headings):
    """Split page text at visible headings (in order)."""
    heads = [h["text"] for h in headings if h["visible"] and len(h["text"]) > 2]
    out, cur, title = [], [], "(top of page)"
    hs = set(heads)
    for line in text.split("\n"):
        l = line.strip()
        if not l:
            continue
        if l in hs or l.upper() in {h.upper() for h in hs}:
            if cur:
                out.append({"heading": title, "text": "\n".join(cur)})
            title, cur = l, []
        else:
            cur.append(l)
    if cur:
        out.append({"heading": title, "text": "\n".join(cur)})
    return out


def norm(s):
    return re.sub(r"[^a-z0-9$%]+", " ", s.lower()).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scans", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out_dir = a.out or a.scans
    pages = {}
    for f in sorted(glob.glob(os.path.join(a.scans, "*-desktop.json")) + glob.glob(os.path.join(a.scans, "*-mobile.json"))):
        d = read_json(f)
        if "text" not in d:
            continue
        path = urllib.parse.urlparse(d["url"]).path or "/"
        pages.setdefault(path, {})[d["device"]] = d
    report = {"pages": {}, "ctas": [], "cross_page": {}}
    md = ["# Copy review evidence\n",
          "Read every section below. Judge accuracy, consistency, grammar, clarity, compliance and conversion",
          "using the copy checklist in SKILL.md. This file only collects the evidence.\n"]
    for path, devs in pages.items():
        d = devs.get("desktop") or devs.get("mobile")
        m = devs.get("mobile")
        p = {"url": d["url"], "title": d["title"], "meta_description": d["meta"].get("description")}
        p["readability"] = readability(d["text"])
        p["sections"] = sections(d["text"], d["headings"])
        p["prices"] = sorted(set(PRICE_RE.findall(d["text"])))
        p["percentages"] = sorted(set(PCT_RE.findall(d["text"])))
        p["dates"] = sorted(set(x.strip() for x in MONTH_RE.findall(d["text"])))
        p["times"] = sorted(set(x.strip() for x in TIME_RE.findall(d["text"])))
        p["signals"] = {k: sorted(set(x.lower() if isinstance(x, str) else x[0].lower() for x in re.findall(rx, d["text"], re.I)))
                        for k, rx in SIGNALS.items()}
        if m and devs.get("desktop"):
            ds = {norm(s): s for s in sentences(devs["desktop"]["text"])}
            ms = {norm(s): s for s in sentences(m["text"])}
            only_d = [ds[k] for k in ds if k not in ms and len(k) > 12]
            only_m = [ms[k] for k in ms if k not in ds and len(k) > 12]
            p["only_on_desktop"] = only_d[:30]
            p["only_on_mobile"] = only_m[:30]
        ctas = []
        for dev, dd in devs.items():
            for b in dd["buttons"] + [l for l in dd["links"]]:
                t = (b.get("text") or "").strip()
                if t and b.get("visible") and len(t) < 120:
                    ctas.append(t)
        p["ctas"] = sorted(set(ctas))
        report["pages"][path] = p
        report["ctas"] += [(path, c) for c in p["ctas"]]

        md.append(f"\n## {path}  —  {d['title']}\n")
        md.append(f"Meta description: {p['meta_description']!r}\n")
        r = p["readability"]
        if r:
            md.append(f"Readability: {r['words']} words, {r['avg_words_per_sentence']} words/sentence, grade {r['reading_grade']}, "
                      f"ALL CAPS {r['all_caps_word_share']}, you:we/I = {r['you_vs_we_I']}")
            for s in r["long_sentences"]:
                md.append(f"  - long sentence: {s}")
        md.append(f"\nPrices: {p['prices']}   Dates: {p['dates']}   Times: {p['times']}   %: {p['percentages']}")
        md.append("\nCTAs / links: " + " | ".join(p["ctas"]))
        md.append("\nConversion signals found:")
        for k, v in p["signals"].items():
            md.append(f"  - {k}: {', '.join(v) if v else 'NONE'}")
        if p.get("only_on_desktop") or p.get("only_on_mobile"):
            md.append("\n### Desktop vs mobile differences (check each one)")
            for s in p.get("only_on_desktop", []):
                md.append(f"  - DESKTOP ONLY: {s}")
            for s in p.get("only_on_mobile", []):
                md.append(f"  - MOBILE ONLY: {s}")
        md.append("\n### Copy by section (desktop)")
        for sec in p["sections"]:
            md.append(f"\n**{sec['heading']}**\n{sec['text']}")
    # cross-page consistency
    allp = report["pages"]
    report["cross_page"] = {
        "prices_by_page": {k: v["prices"] for k, v in allp.items()},
        "dates_by_page": {k: v["dates"] + v["times"] for k, v in allp.items()},
        "titles": {k: v["title"] for k, v in allp.items()},
    }
    md.append("\n## Across the funnel (must agree)")
    for k, v in report["cross_page"].items():
        md.append(f"\n**{k}**")
        for pg, val in v.items():
            md.append(f"  - {pg}: {val}")
    titles = [v["title"] for v in allp.values()]
    dup_titles = sorted(set(t for t in titles if titles.count(t) > 1))
    if dup_titles:
        md.append(f"\nSame page title used on several pages: {dup_titles}")
    write_json(os.path.join(out_dir, "copy-review.json"), report)
    mdp = os.path.join(out_dir, "copy-review.md")
    with open(mdp, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"Pages: {list(pages)}")
    for path, p in allp.items():
        print(f"  {path}: {p['readability'].get('words')} words, grade {p['readability'].get('reading_grade')}, "
              f"desktop-only {len(p.get('only_on_desktop', []))}, mobile-only {len(p.get('only_on_mobile', []))}, "
              f"prices {p['prices']}, proof words: {len(p['signals']['proof (testimonials, numbers, logos)'])}")
    print("->", mdp)


if __name__ == "__main__":
    main()
