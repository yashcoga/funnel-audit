"""Test an opt-in form: validation, required SMS/consent boxes, and (optionally) a real test submission.

Usage:
  python optin_test.py URL --out DIR [--device desktop|mobile] [--submit]
         [--name "Funnel AuditTest"] [--email x-audit-MMDD@mailinator.com] [--phone 2025550123]

Without --submit (default) nothing is sent: the form-submit request and ad pixels are blocked.
Steps:
  1. submit empty            -> validation messages
  2. bad email + short phone -> validation messages
  3. valid fields, boxes unticked -> is a checkbox (SMS consent / terms) REQUIRED to register?
  4. --submit only: tick boxes, submit for real, record redirect URL, time to redirect and the pixel events that fired.
     Steps 1-3 never submit for real. If the form has an anti-bot check (Cloudflare Turnstile, reCAPTCHA),
     the real submit runs in a short-lived visible browser window, because those checks fail headless.
Writes DIR/optin-<page>-<device>.json.
"""
import argparse
import datetime
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (FORM_SUBMIT_RE, TrackingLog, device_opts, launch, new_context,  # noqa: E402
                    prepare, slug, write_json)

# Anti-bot tokens some builders (GHL with Cloudflare Turnstile, reCAPTCHA, hCaptcha) need before a submit is accepted.
# Without the token the form silently does nothing: no error, no redirect.
CAPTCHA_SEL = "input[name=cf-turnstile-response], textarea[name=g-recaptcha-response], textarea[name=h-captcha-response]"


class FormGate:
    """Blocks form-submission requests except while open, and records every one that goes out."""

    def __init__(self, context):
        self.open = False
        self.sent = []
        context.route(FORM_SUBMIT_RE, self._route)
        context.on("response", self._response)

    def _route(self, route):
        req = route.request
        self.sent.append({"url": req.url[:160], "allowed": self.open, "body": (req.post_data or "")[:1500]})
        route.continue_() if self.open else route.abort()

    def _response(self, resp):
        if FORM_SUBMIT_RE.search(resp.url):
            for s in reversed(self.sent):
                if s["url"] == resp.url[:160] and "status" not in s:
                    s["status"] = resp.status
                    break


def wait_for_captcha(page, timeout_ms=20000):
    """If the form has an anti-bot token field, wait until it holds a token. Returns None, 'ready' or 'missing'."""
    if not page.locator(CAPTCHA_SEL).count():
        return None
    try:
        page.wait_for_function(
            f"() => [...document.querySelectorAll({CAPTCHA_SEL!r})].some(e => e.value && e.value.length > 20)",
            timeout=timeout_ms)
        return "ready"
    except Exception:
        return "missing"


def sent_phone(gate):
    """The phone number exactly as the form sent it (shows the country code the builder added)."""
    for s in gate.sent:
        m = re.search(r'"phone"\s*:\s*"([^"]*)"', s["body"])
        if s["allowed"] and m:
            return m.group(1)
    return None

FIELDS_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const email = [...document.querySelectorAll('input')].find(i => vis(i) && (i.type === 'email' || /email/i.test(i.name + i.placeholder + i.autocomplete)));
  if (!email) return null;
  // the form container = nearest ancestor that also holds a visible button
  let box = email;
  for (let i = 0; i < 10 && box.parentElement; i++) { box = box.parentElement;
    if ([...box.querySelectorAll('button, input[type=submit]')].some(vis) && box.querySelectorAll('input').length >= 1) break; }
  box.setAttribute('data-fa-form', '1');
  const fields = [];
  [...box.querySelectorAll('input, select, textarea')].forEach((el, i) => {
    if (el.type === 'hidden' || (!vis(el) && el.type !== 'checkbox')) return;
    el.setAttribute('data-fa-field', String(i));
    const label = (el.labels && el.labels[0] ? el.labels[0].innerText : '') || (el.closest('label') ? el.closest('label').innerText : '')
      || (el.parentElement ? el.parentElement.innerText : '');
    const hint = (el.name + ' ' + el.placeholder + ' ' + el.autocomplete + ' ' + el.type + ' ' + label).toLowerCase();
    let kind = 'text';
    if (el.type === 'checkbox') kind = 'checkbox';
    else if (el.type === 'radio') kind = 'radio';
    else if (el.tagName === 'SELECT') kind = 'select';
    else if (el.type === 'email' || /email/.test(hint)) kind = 'email';
    else if (el.type === 'tel' || /phone|mobile|tel/.test(hint)) kind = 'phone';
    else if (/first/.test(hint)) kind = 'first_name';
    else if (/last/.test(hint)) kind = 'last_name';
    else if (/name/.test(hint)) kind = 'name';
    fields.push({i, kind, type: el.type, name: el.name || null, placeholder: el.placeholder || null, required: el.required,
      label: (label || '').trim().replace(/\s+/g, ' ').slice(0, 300)});
  });
  const btn = [...box.querySelectorAll('button, input[type=submit]')].filter(vis).pop();
  btn.setAttribute('data-fa-submit', '1');
  return {fields, submit_text: (btn.innerText || btn.value || '').trim()};
}
"""

ERRORS_JS = r"""
() => {
  const box = document.querySelector('[data-fa-form]') || document.body;
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const msgs = new Set();
  box.querySelectorAll('[class*=error i], [class*=invalid i], [role=alert], [aria-live]').forEach(e => {
    const t = (e.innerText || '').trim(); if (t && t.length < 200 && vis(e)) msgs.add(t); });
  box.querySelectorAll('input, select, textarea').forEach(e => { if (e.validationMessage && !e.checkValidity()) msgs.add(`${e.name || e.type}: ${e.validationMessage}`); });
  return [...msgs];
}
"""


def _values(mode, name, email, phone):
    first, _, last = name.partition(" ")
    if mode == "bad":
        return {"email": "not-an-email", "phone": "123"}, "x"
    return {"email": email, "phone": phone, "first_name": first, "last_name": last or "Test", "name": name}, "Test"


def fill(page, fields, mode, name, email, phone):
    """Fill text fields and verify they kept their values (some builders re-render the form once after load)."""
    vals, default = _values(mode, name, email, phone)
    for attempt in range(4):
        missing = 0
        for f in fields:
            k = f["kind"]
            if k in ("checkbox", "radio"):
                continue
            loc = page.locator(f'[data-fa-field="{f["i"]}"]').first
            try:
                if k == "select":
                    if loc.locator("option").count() > 1 and not loc.input_value():
                        loc.select_option(index=1)
                    continue
                want = vals.get(k, default)
                have = loc.input_value()
                digits = re.sub(r"\D", "", have)
                ok = (digits.endswith(re.sub(r"\D", "", want)) and digits) if k == "phone" else have == want
                if ok:
                    continue
                missing += 1
                loc.fill("")
                if k == "phone":
                    loc.press_sequentially(want, delay=30)
                else:
                    loc.fill(want)
            except Exception:
                missing += 1
        if not missing:
            return True
        page.wait_for_timeout(1500)
    return False


EMAIL_SEL = "input[type=email], input[name*=email i], input[placeholder*=email i]"


def load_form(page, reload=False):
    """(Re)load the page and wait until the form's email field is visible, then tag the fields."""
    if reload:
        page.reload(wait_until="domcontentloaded")
    page.locator(EMAIL_SEL).first.wait_for(state="visible", timeout=30000)
    page.wait_for_timeout(1200)
    return page.evaluate(FIELDS_JS)


def submit(page):
    btn = page.locator('[data-fa-submit="1"]').first
    btn.scroll_into_view_if_needed()
    btn.click()
    page.wait_for_timeout(2500)
    errs = page.evaluate(ERRORS_JS)
    if not errs:                       # some builders validate only on the second click
        btn.click()
        page.wait_for_timeout(2000)
        errs = page.evaluate(ERRORS_JS)
    return errs


def submit_context(p, browser, device, captcha_on_page):
    """Browser context for the one real test lead. Anti-bot checks (Cloudflare Turnstile etc.) never pass in a
    headless browser or with a user agent that doesn't match the engine, so then use a short-lived visible window
    with the browser's own user agent (mobile: an Android Chrome agent with the real version)."""
    if not captcha_on_page:
        ctx, log = new_context(browser, device, tracking="observe")
        return None, ctx, log
    b = launch(p, headless=False)
    opts = dict(device_opts(device))
    if device == "mobile":
        major = b.version.split(".")[0]
        opts["user_agent"] = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                              f"Chrome/{major}.0.0.0 Mobile Safari/537.36")
    else:
        opts.pop("user_agent", None)
    ctx = b.new_context(**opts)
    return b, ctx, TrackingLog(ctx, mode="observe")


def run(p, browser, url, device, out, do_submit, name, email, phone):
    # Checks 1-3 never submit for real (form submits and pixels blocked), so they can't create an extra lead or
    # walk into the next page. Only the one real test lead at the end goes through, with live pixels.
    ctx, log = new_context(browser, device, tracking="block")
    gate = FormGate(ctx)
    page = ctx.new_page()
    prepare(page, url, scroll=False)
    try:
        info = load_form(page)
    except Exception:
        info = None
    if not info:
        ctx.close()
        return {"url": url, "device": device, "error": "No form with an email field found on this page."}
    res = {"url": url, "device": device, "fields": info["fields"], "submit_text": info["submit_text"], "steps": {}}
    res["steps"]["empty_submit"] = submit(page)
    fill(page, info["fields"], "bad", name, email, phone)
    res["steps"]["invalid_values"] = submit(page)
    # valid values, every checkbox unticked
    info = load_form(page, reload=True)
    fill(page, info["fields"], "good", name, email, phone)
    for f in info["fields"]:
        if f["kind"] == "checkbox":
            page.locator(f'[data-fa-field="{f["i"]}"]').first.set_checked(False, force=True)
    has_captcha = page.locator(CAPTCHA_SEL).count() > 0
    res["anti_bot_check"] = has_captcha
    tried_before = len(gate.sent)
    errs = submit(page)
    # The submit request itself is blocked here, so "registration blocked by a box" = the form never even tried to send.
    blocked = len(gate.sent) == tried_before
    boxes = [f for f in info["fields"] if f["kind"] == "checkbox"]
    res["steps"]["valid_values_boxes_unticked"] = errs
    res["checkboxes"] = [{"label": f["label"], "required_to_register": blocked} for f in boxes]
    sms = [b for b in res["checkboxes"] if re.search(r"sms|text message|messag|consent", b["label"], re.I)]
    if sms and blocked:
        res.setdefault("findings", []).append(
            "SMS consent checkbox is REQUIRED to register. Under TCPA / A2P 10DLC, consent to texts can't be a condition "
            "of signing up: make it optional.")
    events = list(log.events)
    ctx.close()
    if do_submit:
        b2, ctx, log = submit_context(p, browser, device, has_captcha)
        try:
            res["submission"] = real_submit(ctx, url, name, email, phone)
            res["submission"]["browser"] = "visible window (anti-bot check)" if b2 else "headless"
        finally:
            events += log.events
            ctx.close()
            if b2:
                b2.close()
        res["form_requests"] = res["submission"].pop("form_requests")
    res["tracking"] = events
    # summary = what really reached the ad platforms (with --submit) / what would have fired (dry run)
    log.events = [e for e in events if not e["blocked"]] if do_submit else events
    res["tracking_summary"] = log.summary()
    return res


def real_submit(ctx, url, name, email, phone):
    """Submit the one real test lead and record where it lands (or why it didn't)."""
    gate = FormGate(ctx)
    page = ctx.new_page()
    prepare(page, url, scroll=False)
    info = load_form(page)
    fill(page, info["fields"], "good", name, email, phone)
    for f in info["fields"]:
        if f["kind"] == "checkbox":
            page.locator(f'[data-fa-field="{f["i"]}"]').first.set_checked(True, force=True)
    captcha = wait_for_captcha(page)
    fill(page, info["fields"], "good", name, email, phone)   # re-check: the form may re-render while waiting
    gate.open = True
    t0 = time.time()
    before = page.url
    page.locator('[data-fa-submit="1"]').first.click()
    lead = {"name": name, "email": email, "phone": phone,
            "time": datetime.datetime.now().astimezone().isoformat(timespec="minutes")}
    try:
        page.wait_for_url(lambda u: u.split("#")[0] != before.split("#")[0], timeout=30000, wait_until="commit")
        redirect_ms = int((time.time() - t0) * 1000)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(6000)
        res = {"redirect_url": page.url, "redirect_ms": redirect_ms, "next_title": page.title(), "test_lead": lead}
    except Exception:
        real = [s for s in gate.sent if s["allowed"]]
        if real and all(200 <= s.get("status", 0) < 300 for s in real):
            why = ("The form WAS saved (server said OK) but the page did not move on: check the form's "
                   "on-submit action (redirect / message) in the builder.")
            res = {"error": "No redirect within 30 s", "diagnosis": why, "test_lead": lead,
                   "page_text": page.evaluate("() => document.body.innerText.slice(0, 400)")}
        elif real:
            st = real[-1].get("status")
            why = f"The form submit was rejected (HTTP {st})."
            if st == 429:
                why += (" 429 = too many submits from this browser/number in a short time, or the anti-bot check "
                        "failed. Wait 10-15 min and re-run once with a new --email before reporting it.")
            res = {"error": "No redirect within 30 s", "diagnosis": why, "test_lead": lead}
        else:
            why = "The click sent nothing to the server"
            why += (" (the anti-bot token never arrived: re-run once; if it repeats, check by hand)."
                    if captcha == "missing" else ": re-run once; if it repeats, check by hand.")
            res = {"error": "No redirect within 30 s", "diagnosis": why}
        res["errors_on_page"] = page.evaluate(ERRORS_JS)
    res["captcha"] = captcha
    res["phone_sent"] = sent_phone(gate)
    res["form_requests"] = [{k: v for k, v in s.items() if k != "body"} for s in gate.sent]
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="desktop", choices=["desktop", "mobile"])
    ap.add_argument("--submit", action="store_true", help="really submit the test lead (fires live pixels once)")
    ap.add_argument("--name", default="Funnel AuditTest")
    ap.add_argument("--email", default=f"funnel-audit-{datetime.date.today():%m%d}@mailinator.com")
    ap.add_argument("--phone", default="2025550123")
    a = ap.parse_args()
    with sync_playwright() as p:
        browser = launch(p)
        res = run(p, browser, a.url, a.device, a.out, a.submit, a.name, a.email, a.phone)
        browser.close()
    path = write_json(os.path.join(a.out, f"optin-{slug(a.url)}-{a.device}.json"), res)
    if res.get("error"):
        print(res["error"])
        return
    print(f"Form fields: {[f['kind'] for f in res['fields']]}   submit button: {res['submit_text']!r}")
    for k, v in res["steps"].items():
        print(f"  {k}: {v}")
    for c in res["checkboxes"]:
        print(f"  checkbox required={c['required_to_register']}: {c['label'][:110]}")
    for f in res.get("findings", []):
        print("  FINDING:", f)
    if res.get("submission"):
        print("  SUBMISSION:", res["submission"])
        ph = res["submission"].get("phone_sent")
        if ph and a.phone == "2025550123" and not ph.startswith("+1"):
            print(f"  NOTE: the form saved the US test number as {ph}: the builder picks the country from the "
                  "tester's location. US visitors get +1; mention it only if the audit runs from the US.")
    print(f"  tracking ({'live' if a.submit else 'blocked'}): {', '.join(res['tracking_summary']) or 'none'}")
    print("->", path)


if __name__ == "__main__":
    main()
