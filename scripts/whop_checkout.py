"""Test a Whop embedded checkout (upsell / downsell page): price, promo code, and an optional $0 test purchase.

Handles both Whop checkouts: the classic embed (whop.com/embedded/checkout) and the newer Whop Elements checkout
(cdn.whop.com/elements/amber/checkout), where the code goes in the "Promo code & details" pop-up, prices are
switched to USD for testers abroad, and a Free total asks only for email + phone.

Usage:
  python whop_checkout.py URL --out DIR --code TESTCODE [--submit] [--device desktop|mobile]
         [--email x-audit-MMDDb@mailinator.com] [--cta 'a[href="#order"]']

Without --submit: fills everything (code, Stripe test card, US address, phone) and reads the totals, but does
NOT click the pay button. Ad pixels are blocked.
With --submit: refuses unless the total is $0.00 after the code, then pays with the Stripe test card
4242 4242 4242 4242 and records the redirect URL, payment ID and every pixel event (including those sent from
inside the checkout iframe). This creates a real $0 order in Whop - list it in the report for clean-up.
Without a code a live checkout declines the test card; that is expected, not a bug.

For other checkouts (Stripe, GHL order forms, ClickFunnels) this script exits with a message; do those steps
manually with the Playwright browser tools, following the same rules.
Writes DIR/checkout-<page>-<device>.json.
"""
import argparse
import datetime
import os
import re
import sys
import time
import urllib.parse

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import launch, new_context, prepare, slug, write_json  # noqa: E402

TEST_CARD = ("4242424242424242", "1234", "123")
ADDRESS = {"name": "Funnel AuditTest", "line1": "1600 Pennsylvania Ave NW", "city": "Washington", "zip": "20500",
           "state": "District of Columbia"}
IFRAME_CLASSIC = 'iframe[src*="whop.com/embedded/checkout"]'
# Whop Elements checkout (cdn.whop.com/elements/amber/...): the promo code and totals sit in a separate
# "Order details" pop-up frame, and with a 100%-off code the checkout shows "Free" and asks only for email + phone.
IFRAME_ELEMENTS = 'iframe[src*="whop.com/elements/amber/checkout/checkout"]'
IFRAME = f"{IFRAME_CLASSIC}, {IFRAME_ELEMENTS}"
DETAILS_FRAME = "/checkout/checkoutDetails/"
PROMO_LINK = re.compile(r"(add|have a) (promo|discount|coupon) code", re.I)
# the checkout's own pay button ("Join", "Pay $997", "Complete order"...), not "Pay in USD instead" / "Pay with Crypto"
PAY_BUTTON = re.compile(r"^\s*(join|pay(?! (in|with)\b)|complete|subscribe|buy|purchase|start|get access|claim)", re.I)

VISIBLE_TEXT_JS = r"""
() => { const out = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  while ((n = w.nextNode())) { const el = n.parentElement; if (!el || el.closest('select,option')) continue;
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    if (r.width > 0 && r.height > 0 && s.visibility !== 'hidden') { const t = n.textContent.trim(); if (t) out.push(t); } }
  return out.join(' | '); }
"""


def text(frame):
    try:
        return frame.locator("body").evaluate(VISIBLE_TEXT_JS)
    except Exception:
        return ""


def total_of(t):
    m = re.search(r"Total due today \| (\$[\d,.]+)", t)
    return m.group(1) if m else None


PRICE_RE = re.compile(r"^(Free|[^\w\s|]{1,3}\s?\d[\d,]*(?:\.\d\d)?|(?:US)?\$\s?\d[\d,]*(?:\.\d\d)?)$")


def elements_total(t):
    """Price shown in a Whop Elements checkout (the first price-looking text: "Free", "$997.00", "₹113,233.15")."""
    for part in t.split(" | "):
        if PRICE_RE.match(part.strip()):
            return part.strip()
    return None


def is_free(total):
    return bool(total) and (total.lower() == "free" or not re.sub(r"[^1-9]", "", total))


def card_frame(page, part):
    for fr in page.frames:
        if part in fr.url:
            return fr
    return None


def set_phone_us(f, page):
    combo = f.locator("button[role=combobox]").filter(has_text=re.compile(r"\(\+\d"))
    if not combo.count():
        return False
    if re.search(r"\bUS \(\+1\)", combo.first.inner_text()):
        return True
    combo.first.click()
    page.wait_for_timeout(600)
    page.keyboard.type("United States", delay=35)
    page.wait_for_timeout(800)
    opts = f.locator("[role=option]")
    for i in range(opts.count()):
        t = opts.nth(i).inner_text().strip()
        if t.startswith("United States") and "(+1)" in t:
            # the list is long and only partly drawn: scroll the option into view, then fall back to a DOM click
            o = opts.nth(i)
            try:
                o.scroll_into_view_if_needed(timeout=3000)
                o.click(timeout=5000)
            except Exception:
                try:
                    o.evaluate("el => el.click()")
                except Exception:
                    page.keyboard.press("Enter")
            break
    page.wait_for_timeout(500)
    return bool(re.search(r"\bUS \(\+1\)", combo.first.inner_text()))


def details_frame(page):
    for fr in page.frames:
        if DETAILS_FRAME in fr.url:
            return fr
    return None


def elements_promo(page, f, code, res):
    """Whop Elements: open "Promo code & details", switch to USD (testers abroad see local money), apply the code,
    close the pop-up. Fills res with what it saw."""
    f.get_by_role("button", name=re.compile(r"promo code", re.I)).first.click()
    m = None
    for _ in range(10):
        page.wait_for_timeout(800)
        m = details_frame(page)
        if m and text(m):
            break
    if not m:
        res["promo_error"] = "Whop's 'Promo code & details' pop-up didn't open."
        return
    # while the pop-up is open the checkout behind it hides its text: read the totals in the pop-up
    usd = m.get_by_role("button", name=re.compile(r"pay in usd", re.I))
    if usd.count():
        res["total_local_currency"] = res.get("total_before_code")
        usd.first.click()
        res["switched_to_usd"] = True
        for _ in range(10):
            page.wait_for_timeout(800)
            if total_of(text(m)):
                break
    t = text(m)
    if total_of(t):
        res["total_before_code"] = total_of(t)
    tax = re.search(r"(Estimated tax[^|]*) \| ([^|]+)", t)
    if tax:
        res["tax_line"] = f"{tax.group(1).strip()}: {tax.group(2).strip()} (based on the tester's location)"
    if code:
        if not m.get_by_text(PROMO_LINK).count() and not m.locator("input[placeholder*=promo i]").count():
            res["promo_error"] = ("No promo-code box in the checkout. Whop shows it only when the plan has an active "
                                  "promo code; check the code exists for this plan in Whop.")
        else:
            box = m.locator("input[placeholder*=promo i], input[name=code]").first
            if not box.count() or not box.is_visible():
                m.get_by_text(PROMO_LINK).first.click()
                page.wait_for_timeout(1000)
            box.click(timeout=8000)
            box.press_sequentially(code, delay=60, timeout=8000)
            m.get_by_role("button", name=re.compile(r"^\s*apply\s*$", re.I)).first.click()
            t = ""
            for _ in range(15):
                page.wait_for_timeout(1500)
                t = text(m)
                if re.search(r"% off|\$ off|free|invalid|not found|expired|doesn.t exist|not valid", t, re.I):
                    break
            res["promo_line"] = (re.findall(r"[A-Z0-9_-]{3,} \| [^|]*(?:off|free)[^|]*", t, re.I) or [None])[0]
            err = re.findall(r"[^|]*(?:invalid|not found|expired|doesn.t exist|not valid)[^|]*", t, re.I)
            if err:
                res["promo_error"] = err[0].strip()
    # close the pop-up (Esc), then read the checkout's own total
    for _ in range(3):
        page.keyboard.press("Escape")
        page.wait_for_timeout(1200)
        pop = page.locator(f'iframe[src*="{DETAILS_FRAME}"]')
        if not pop.count() or not pop.first.is_visible():
            break
    res["total_after_code"] = elements_total(text(f)) if code else None


def run(browser, url, device, code, do_submit, email, cta):
    ctx, log = new_context(browser, device, tracking="observe" if do_submit else "block")
    page = ctx.new_page()
    prepare(page, url, scroll=False, wait_ms=2500)
    res = {"url": url, "device": device, "code": code, "submitted": False}
    if not page.locator(IFRAME).count():
        # pop-up checkouts only load the Whop iframe after the "Yes" click: click the CTA (or a common one) first
        for sel in (cta, 'a[href="#checkout"], a[href*="#checkout"], a[href*="#order"], a[href*="#buy"]'):
            try:
                btn = page.locator(sel).first
                if not btn.count():
                    continue
                btn.scroll_into_view_if_needed()
                (btn.tap if device == "mobile" else btn.click)()
                page.locator(IFRAME).first.wait_for(state="attached", timeout=8000)
                res["checkout_in_popup"] = True
                cta = sel
                break
            except Exception:
                continue
    if not page.locator(IFRAME).count():
        res["error"] = ("No Whop embedded checkout on this page. Test this checkout manually with the Playwright "
                        "browser tools (same rules: code first, $0 total, Stripe test card).")
        ctx.close()
        return res
    elements = page.locator(IFRAME_ELEMENTS).count() > 0
    res["checkout_type"] = "Whop Elements" if elements else "Whop embedded (classic)"
    IFR = IFRAME_ELEMENTS if elements else IFRAME_CLASSIC
    src = page.locator(IFR).first.get_attribute("src") or ""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(src).query)
    if elements:
        # the plan sits on the page element; the redirect after payment is set on the plan in Whop
        res["plan"] = page.evaluate("() => { const e = document.querySelector('[data-plan^=plan_], [data-plan-id^=plan_], "
                                    "[data-whop-checkout-plan-id]'); return e ? (e.dataset.plan || e.dataset.planId || "
                                    "e.dataset.whopCheckoutPlanId) : null; }")
        if not res["plan"]:   # or in the page code: WhopElements().checkout.create({ plan: "plan_..." })
            html = page.content()
            res["plan"] = (re.findall(r"plan\s*:\s*\\?[\"'](plan_\w+)", html) or re.findall(r"plan_\w{8,}", html) or [None])[0]
        res["payment_redirect_url"] = None
        res["redirect_note"] = "Set on the plan in Whop (not visible on the page): confirm it with the test purchase."
    else:
        res["plan"] = (re.findall(r"plan_\w+", src) or [None])[0]
        res["payment_redirect_url"] = (q.get("payment_redirect_url") or [None])[0]
    # click the main CTA like a visitor, then see if the checkout is on screen
    try:
        if res.get("checkout_in_popup"):
            page.wait_for_timeout(2500)   # already opened by the click above
        else:
            btn = page.locator(cta).first
            btn.scroll_into_view_if_needed()
            (btn.tap if device == "mobile" else btn.click)()
            page.wait_for_timeout(2500)
        top = page.locator(IFR).first.evaluate("e => Math.round(e.getBoundingClientRect().top)")
        res["checkout_top_after_cta"] = top
        res["checkout_on_screen_after_cta"] = top < page.viewport_size["height"] - 150
    except Exception as e:
        res["cta_error"] = str(e).splitlines()[0][:120]
    page.locator(IFR).first.scroll_into_view_if_needed()
    page.wait_for_timeout(1500)
    f = page.frame_locator(IFR)
    f.locator("input[name=email]").wait_for(timeout=45000)
    page.wait_for_timeout(1500)
    total = elements_total if elements else total_of
    res["total_before_code"] = total(text(f))
    # Whop's own pay button can be hidden (hide-submit-button) with a button on the page paying instead
    pay = f.get_by_role("button", name=PAY_BUTTON) if elements else f.locator("button[type=submit]")
    if not pay.count():
        pay = page.locator("#co-pay, button[data-pay], .co__pay, [class*=checkout] button[type=submit]")
        res["pay_button_on_page"] = True
    res["pay_button_text"] = (pay.last.inner_text(timeout=5000) or "").strip() if pay.count() else None
    if elements:
        elements_promo(page, f, code, res)
        code = None                      # done above
    elif code and not f.get_by_text(PROMO_LINK).count():
        res["promo_error"] = ("No promo-code box in the checkout. Whop shows it only when the plan has an active "
                              "promo code; check the code exists for this plan in Whop.")
        code = None
    if code:
        f.get_by_text(PROMO_LINK).first.click()
        # Whop sometimes re-draws the form right after the code box opens (the box is "detached"): wait, retry
        for attempt in range(4):
            try:
                page.wait_for_timeout(1200)
                box = f.locator("input[name=code]")
                box.click(timeout=8000)
                box.press_sequentially(code, delay=60, timeout=8000)
                if box.input_value(timeout=3000).lower() == code.lower():
                    break
            except Exception:
                if not f.locator("input[name=code]").count():
                    f.get_by_text(PROMO_LINK).first.click()
        f.get_by_role("button", name="Apply").click()
        t = ""
        for _ in range(15):
            page.wait_for_timeout(2000)
            t = text(f)
            if re.search(r"% off|\$ off|invalid|not found|expired|doesn.t exist", t, re.I) and total_of(t) != res["total_before_code"]:
                break
            if re.search(r"invalid|not found|expired|doesn.t exist", t, re.I):
                break
        res["promo_line"] = (re.findall(r"[A-Z0-9_-]{3,} \| [^|]*(?:off|free)[^|]*", t) or [None])[0]
        res["total_after_code"] = total_of(t)
        err = re.findall(r"[^|]*(?:invalid|not found|expired|doesn.t exist)[^|]*", t, re.I)
        if err:
            res["promo_error"] = err[0].strip()
    # fill buyer details (a Whop Elements checkout that is Free asks only for email + phone: no card, no address)
    f.locator("input[name=email]").fill(email)
    if elements and is_free(res.get("total_after_code")):
        res["card_fields"] = "not asked (total is Free)"
    else:
        num, exp, cvc = (card_frame(page, "card-number-element"), card_frame(page, "card-expiration-date-element"),
                         card_frame(page, "card-verification-code-element"))
        if num and exp and cvc:
            num.locator("input").first.press_sequentially(TEST_CARD[0], delay=25)
            exp.locator("input").first.press_sequentially(TEST_CARD[1], delay=25)
            cvc.locator("input").first.press_sequentially(TEST_CARD[2], delay=25)
        else:
            res["card_fields"] = "not found (Whop changed its card form?)"
        try:
            f.locator("input[name=name]").first.fill(ADDRESS["name"])
            f.locator("select[name=country]").first.select_option("US")
            page.wait_for_timeout(1200)
            f.locator("input[name=line1]").first.fill(ADDRESS["line1"])
            f.locator("input[name=city]").first.fill(ADDRESS["city"])
            f.locator("input[name=zip], input[name=postal_code]").first.fill(ADDRESS["zip"])
            f.locator("select[name=state]").first.select_option(label=ADDRESS["state"])
        except Exception as e:
            res["address_note"] = str(e).splitlines()[0][:120]
    tel = f.locator("input[type=tel]")
    if elements:
        # one international phone box ("+1 555 000 0000"): type the number with +1
        if tel.count():
            tel.first.fill("")
            tel.first.press_sequentially("+12025550123", delay=30)
        res["phone_country_set"] = bool(tel.count()) and re.sub(r"\D", "", tel.first.input_value()).startswith("1")
    else:
        res["phone_country_set"] = set_phone_us(f, page)
        if tel.count():
            tel.first.fill("2025550123")
    page.wait_for_timeout(800)
    res["total_ready"] = total(text(f))
    if not do_submit:
        res["note"] = "Dry run: stopped before the pay button. Use --submit to place the $0 test order."
        res["tracking"] = log.events
        ctx.close()
        return res
    if not is_free(res["total_ready"]):
        res["error"] = f"Refusing to submit: total is {res['total_ready']} (needs a 100%-off code so nothing is charged)."
        ctx.close()
        return res
    before = page.url
    t0 = time.time()
    if not pay.count():
        res["error"] = "No pay button found (neither inside the Whop checkout nor on the page)."
        ctx.close()
        return res
    pay.last.click()
    res["submitted"] = True
    t = ""
    for _ in range(40):
        page.wait_for_timeout(1500)
        if page.url.split("#")[0] != before.split("#")[0]:
            break
        t = text(f)
        if re.search(r"could not be processed|declined|required|invalid|error", t, re.I):
            break
    if page.url.split("#")[0] != before.split("#")[0]:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(6000)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
        res["result"] = {"redirect_url": page.url, "seconds": round(time.time() - t0, 1),
                         "payment_id": (qs.get("payment_id") or qs.get("receipt_id") or [None])[0],
                         "status": (qs.get("status") or [None])[0], "email": email,
                         "time": datetime.datetime.now().astimezone().isoformat(timespec="minutes")}
    else:
        res["result"] = {"error": "; ".join(x.strip() for x in re.findall(
            r"[^|]*(?:could not be processed|declined|required|invalid|error)[^|]*", t, re.I))[:300] or "no redirect"}
    res["tracking"] = log.events
    ctx.close()
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--code", default=None)
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--device", default="desktop", choices=["desktop", "mobile"])
    ap.add_argument("--email", default=f"funnel-audit-{datetime.date.today():%m%d}-{int(time.time()) % 1000}@mailinator.com")
    ap.add_argument("--cta", default='a[href="#order"], a[href*="#order"]', help="CSS selector of the main buy button")
    a = ap.parse_args()
    with sync_playwright() as p:
        browser = launch(p)
        res = run(browser, a.url, a.device, a.code, a.submit, a.email, a.cta)
        browser.close()
    path = write_json(os.path.join(a.out, f"checkout-{slug(a.url)}-{a.device}.json"), res)
    for k in ("checkout_type", "plan", "checkout_in_popup", "pay_button_on_page", "payment_redirect_url", "redirect_note",
              "checkout_top_after_cta", "checkout_on_screen_after_cta", "pay_button_text", "switched_to_usd",
              "total_local_currency", "tax_line", "total_before_code", "promo_line", "total_after_code", "promo_error", "card_fields", "phone_country_set",
              "total_ready", "note", "error", "result"):
        if res.get(k) is not None:
            print(f"  {k}: {res[k]}")
    ev = [e for e in res.get("tracking", []) if e["platform"] == "meta"]
    for e in ev:
        print(f"  meta {e['event']}: value={e['value']} {e['currency'] or ''} event_id={(e['event_id'] or '')[:40]} "
              f"{'(blocked)' if e['blocked'] else '(LIVE)'} page={(e['page'] or '')[:50]}")
    print("->", path)


if __name__ == "__main__":
    main()
