"""Click every visible CTA button and link on a page and record where each one goes.

Usage:
  python test_ctas.py URL --out DIR [--device desktop|mobile|both]

For each clickable element (buttons, [role=button], links), on a freshly loaded page:
  - same-page jumps (#form, #order): records the scroll position after the click and whether a form field,
    checkout iframe or the target element is actually on screen
  - links to other pages: records the final URL (opened, then closed) and HTTP status
  - new tabs / popups: records the URL
Form submit buttons are NOT clicked (use optin_test.py). Ad pixels and form submissions are blocked.
Writes DIR/ctas-<page>-<device>.json and prints a table.
"""
import argparse
import os
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import launch, new_context, prepare, slug, write_json  # noqa: E402

LIST_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const nearInputs = b => { let e = b; for (let i = 0; i < 5 && e; i++) { e = e.parentElement;
      if (e && e.querySelector('input[type=email],input[type=tel],input[name*=email i],input[name*=phone i]')) return true; } return false; };
  const els = [...document.querySelectorAll('a[href], button, [role=button], input[type=submit]')];
  const out = [];
  els.forEach((el, i) => {
    if (!vis(el)) return;
    if (el.closest('a[href]') && el.tagName !== 'A') return;   // button inside a link: the link is tested
    const isForm = el.tagName !== 'A' && (!!el.closest('form') || nearInputs(el));
    el.setAttribute('data-fa-cta', String(i));
    const r = el.getBoundingClientRect();
    out.push({idx: i, tag: el.tagName.toLowerCase(), text: (el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 90),
      href: el.getAttribute('href'), target: el.getAttribute('target'), id: el.id || null, form_submit: isForm,
      y: Math.round(r.top + scrollY)});
  });
  return out;
}
"""

AFTER_JS = r"""
(targetId) => {
  const H = innerHeight; const onScreen = el => { const r = el.getBoundingClientRect(); return r.bottom > 0 && r.top < H; };
  const field = [...document.querySelectorAll('input:not([type=hidden]), select, textarea')].find(e => e.getBoundingClientRect().width > 0 && onScreen(e));
  const ifr = [...document.querySelectorAll('iframe')].filter(f => /checkout|whop|stripe|pay|order/i.test(f.src || '') && f.getBoundingClientRect().width > 0);
  const checkout = ifr.length ? Math.round(ifr[0].getBoundingClientRect().top) : null;
  let target = null;
  if (targetId) { const t = document.getElementById(targetId) || document.querySelector(`[name="${targetId}"]`);
    if (t) target = Math.round(t.getBoundingClientRect().top); }
  return {scroll_y: Math.round(scrollY), url: location.href, form_field_on_screen: !!field,
    checkout_iframe_top: checkout, checkout_on_screen: checkout !== null && checkout < H - 150, target_top: target, viewport_h: H};
}
"""


def test(browser, url, device):
    ctx, log = new_context(browser, device, tracking="block", block_forms=True)
    page = ctx.new_page()
    prepare(page, url)
    ctas = page.evaluate(LIST_JS)
    results = []
    for c in ctas:
        r = dict(c)
        if c["form_submit"]:
            r["result"] = "skipped (form submit; use optin_test.py)"
            results.append(r)
            continue
        href = c["href"] or ""
        try:
            # fresh page state for every CTA
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            page.evaluate(LIST_JS)
            loc = page.locator(f'[data-fa-cta="{c["idx"]}"]').first
            if href.startswith(("mailto:", "tel:", "sms:")):
                r["result"] = f"contact link ({href.split(':')[0]})"
                results.append(r)
                continue
            before = page.url
            new_pages = []
            ctx.on("page", lambda pg: new_pages.append(pg))
            loc.scroll_into_view_if_needed(timeout=5000)
            if device == "mobile":
                loc.tap(timeout=8000)
            else:
                loc.click(timeout=8000)
            page.wait_for_timeout(2200)
            if new_pages:
                np = new_pages[0]
                try:
                    np.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                r.update(result="opens new tab", final_url=np.url)
                np.close()
            elif page.url.split("#")[0] != before.split("#")[0]:
                r.update(result="navigates", final_url=page.url, title=page.title())
            else:
                target = href[1:] if href.startswith("#") else None
                after = page.evaluate(AFTER_JS, target)
                where = []
                if after["form_field_on_screen"]:
                    where.append("form field on screen")
                if after["checkout_iframe_top"] is not None:
                    where.append("checkout on screen" if after["checkout_on_screen"]
                                 else f"checkout NOT on screen (starts {after['checkout_iframe_top']}px down)")
                if not where:
                    where.append("no form/checkout on screen")
                r.update(result="same page: " + ", ".join(where), **after)
        except Exception as e:
            r["result"] = f"ERROR: {str(e).splitlines()[0][:140]}"
        results.append(r)
    ctx.close()
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="both", choices=["desktop", "mobile", "both"])
    a = ap.parse_args()
    devices = ["desktop", "mobile"] if a.device == "both" else [a.device]
    with sync_playwright() as p:
        browser = launch(p)
        for d in devices:
            res = test(browser, a.url, d)
            path = write_json(os.path.join(a.out, f"ctas-{slug(a.url)}-{d}.json"), {"url": a.url, "device": d, "ctas": res})
            print(f"\n== {d}: {a.url}   ({len(res)} clickable elements)  -> {path}")
            for r in res:
                dest = r.get("final_url") or (r.get("href") or "")
                print(f"  - [{r['tag']}] {r['text'][:55]!r:58} {dest[:60]:60} => {r['result']}")
        browser.close()


if __name__ == "__main__":
    main()
