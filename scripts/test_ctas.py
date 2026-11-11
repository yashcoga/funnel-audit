"""Click every visible CTA button and link on a page and record where each one goes.

Usage:
  python test_ctas.py URL [URL ...] --out DIR [--device desktop|mobile|both] [--jobs 4]

Several pages and both devices run at the same time (one process each, --jobs at once). Links with the same
address (same href and target) are clicked once; the copies get the same result ("same link as #N"). Buttons
without an address are always clicked one by one (in GHL each button has its own action).
After a same-page click the page is only reloaded when needed (it navigated, opened a tab or left a pop-up open).

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
import subprocess
import sys
import time

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


OVERLAY_JS = r"""
() => { const H = innerHeight, W = innerWidth;
  return [...document.querySelectorAll('body *')].some(e => { const s = getComputedStyle(e);
    if (s.position !== 'fixed' || s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.1) return false;
    const r = e.getBoundingClientRect(); return r.width * r.height > 0.4 * W * H; }); }
"""


def _link_key(c):
    h = (c.get("href") or "").strip()
    if not h or h.startswith(("#", "javascript", "mailto:", "tel:", "sms:")):
        return None
    return (h, c.get("target") or "")


def _reset(page, url, need_reload):
    """Get back to a clean page: full reload only when needed, otherwise close pop-ups and scroll to the top."""
    if not need_reload:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
            if page.evaluate(OVERLAY_JS):
                need_reload = True
            else:
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(250)
        except Exception:
            need_reload = True
    if need_reload:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
    page.evaluate(LIST_JS)


def test(browser, url, device):
    ctx, log = new_context(browser, device, tracking="block", block_forms=True)
    page = ctx.new_page()
    try:
        prepare(page, url)
    except Exception as e:  # page didn't open (offline, DNS, timeout): record it instead of crashing
        ctx.close()
        return [{"idx": -1, "tag": "page", "text": "(page did not open)", "href": url,
                 "result": f"ERROR: page did not open: {str(e).splitlines()[0][:140]}"}]
    ctas = page.evaluate(LIST_JS)
    results = []
    tested = {}            # link key -> index in results of the first copy
    need_reload = False    # the page is fresh right after prepare()
    new_pages = []
    ctx.on("page", lambda pg: new_pages.append(pg))
    for c in ctas:
        r = dict(c)
        if c["form_submit"]:
            r["result"] = "skipped (form submit; use optin_test.py)"
            results.append(r)
            continue
        href = c["href"] or ""
        if href.startswith(("mailto:", "tel:", "sms:")):
            r["result"] = f"contact link ({href.split(':')[0]})"
            results.append(r)
            continue
        key = _link_key(c)
        if key and key in tested:
            first = results[tested[key]]
            r.update({k: v for k, v in first.items() if k in ("result", "final_url", "title")})
            r["same_as"] = first["idx"]
            r["result"] = f"{first['result']} (same link as #{first['idx']}, not clicked again)"
            results.append(r)
            continue
        before = page.url
        for attempt in (1, 2):
            try:
                _reset(page, url, need_reload or attempt == 2)
                need_reload = False
                loc = page.locator(f'[data-fa-cta="{c["idx"]}"]').first
                before = page.url
                new_pages.clear()
                loc.scroll_into_view_if_needed(timeout=5000)
                if device == "mobile":
                    loc.tap(timeout=8000)
                else:
                    loc.click(timeout=8000)
                page.wait_for_timeout(700)
                if not new_pages and page.url.split("#")[0] == before.split("#")[0]:
                    page.wait_for_timeout(700)  # let smooth scrolling / pop-ups settle
                break
            except Exception as e:
                if attempt == 2:
                    r["result"] = f"ERROR: {str(e).splitlines()[0][:140]}"
        if "result" in r:
            results.append(r)
            need_reload = True
            continue
        try:
            if new_pages:
                np = new_pages[0]
                try:
                    np.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                r.update(result="opens new tab", final_url=np.url)
                np.close()
            elif page.url.split("#")[0] != before.split("#")[0]:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                r.update(result="navigates", final_url=page.url, title=page.title())
                need_reload = True
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
            need_reload = True
        if key:
            tested[key] = len(results)
        results.append(r)
    ctx.close()
    return results


def run_one(url, device, out):
    with sync_playwright() as p:
        browser = launch(p)
        res = test(browser, url, device)
        browser.close()
    path = write_json(os.path.join(out, f"ctas-{slug(url)}-{device}.json"), {"url": url, "device": device, "ctas": res})
    clicked = sum(1 for r in res if "same_as" not in r and not r["result"].startswith(("skipped", "contact")))
    lines = [f"\n== {device}: {url}   ({len(res)} clickable elements, {clicked} clicked)  -> {path}"]
    for r in res:
        dest = r.get("final_url") or (r.get("href") or "")
        lines.append(f"  - [{r['tag']}] {r['text'][:55]!r:58} {dest[:60]:60} => {r['result']}")
    print("\n".join(lines), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="+", metavar="URL")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="both", choices=["desktop", "mobile", "both"])
    ap.add_argument("--jobs", type=int, default=4, help="pages/devices tested at the same time (default 4)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    devices = ["desktop", "mobile"] if a.device == "both" else [a.device]
    work = [(u, d) for u in a.urls for d in devices]
    if len(work) == 1 or a.jobs <= 1:
        for u, d in work:
            run_one(u, d, a.out)
        return
    # one process per page+device (Playwright's sync API can't be shared across threads)
    t0, running, queue = time.time(), [], list(work)
    while queue or running:
        while queue and len(running) < a.jobs:
            u, d = queue.pop(0)
            cmd = [sys.executable, os.path.abspath(__file__), u, "--out", a.out, "--device", d, "--jobs", "1"]
            log = open(os.path.join(a.out, f"ctas-{slug(u)}-{d}.log"), "w+", encoding="utf-8", errors="replace")
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            running.append((subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env), log))
        for pr, log in running[:]:
            if pr.poll() is not None:
                log.seek(0)
                print(log.read(), end="", flush=True)
                log.close()
                running.remove((pr, log))
        time.sleep(0.3)
    print(f"\nDone: {len(work)} page/device runs in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
