"""Shared helpers for the funnel-audit scripts.

Browser contexts (desktop / mobile), ad-pixel blocking and logging, pixel-request
decoding, page preparation (lazy-load scrolling) and small output helpers.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Requests that send ad / analytics conversions. Blocked by default so audits don't pollute ad data.
TRACKING_RE = re.compile(
    r"facebook\.com/tr|google\.com/(rmkt|ccm|pagead)|googleadservices\.com|analytics\.tiktok\.com"
    r"|t\.whop\.tw/conversions|google-analytics\.com/g/collect|analytics\.google\.com/g/collect"
    r"|bat\.bing\.com/action|ct\.pinterest\.com|snap\.licdn\.com|px\.ads\.linkedin\.com"
)
# Form-submission endpoints of common page builders. Blocked whenever a form must not really submit.
FORM_SUBMIT_RE = re.compile(
    r"backend\.leadconnectorhq\.com/forms/submit|msgsndr\.com/forms/submit|/api/v\d+/forms?/submit"
    r"|clickfunnels\.com/.*/(optin|contacts)|api\.convertkit\.com|api\.hsforms\.com/submissions"
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0.0.0 Safari/537.36")
# A normal Chrome user agent: Meta's pixel ignores "HeadlessChrome", which would hide real pixel behaviour.
DESKTOP = dict(viewport={"width": 1440, "height": 900}, user_agent=UA)
MOBILE = dict(
    viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
    user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"),
)


def device_opts(device):
    return MOBILE if device == "mobile" else DESKTOP


def slug(url_or_text, maxlen=60):
    s = urllib.parse.urlparse(url_or_text).path.strip("/") or "home" if "://" in url_or_text else url_or_text
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return (s or "page")[:maxlen]


def write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- pixel decoding

def _params(request):
    """Query-string + form-body parameters of a request as a flat dict."""
    url = request.url
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query, keep_blank_values=True))
    body = None
    try:
        body = request.post_data
    except Exception:
        body = None
    if body and "Content-Disposition: form-data" in body:
        # multipart/form-data (Meta's sendBeacon)
        for name, value in re.findall(r'name="([^"]+)"\r?\n\r?\n(.*?)\r?\n--', body, re.S):
            params[name] = value
    elif body and "=" in body and not body.lstrip().startswith(("{", "[")):
        params.update(dict(urllib.parse.parse_qsl(body, keep_blank_values=True)))
    return params, body


def decode_tracking(request):
    """Turn a tracking request into {platform, pixel, event, value, currency, event_id, page}."""
    url = request.url
    try:
        params, body = _params(request)
    except Exception:
        params, body = {}, None
    ev = {"platform": None, "pixel": None, "event": None, "value": None, "currency": None,
          "event_id": None, "page": None, "frame_url": None}
    try:
        ev["frame_url"] = request.frame.url[:120] if request.frame else None
    except Exception:
        pass
    if "facebook.com/tr" in url:
        ev.update(platform="meta", pixel=params.get("id"), event=params.get("ev"),
                  value=params.get("cd[value]"), currency=params.get("cd[currency]"),
                  event_id=params.get("cd[eventID]") or params.get("eid"), page=params.get("dl"))
    elif "googleadservices" in url or re.search(r"google\.com/(rmkt|ccm|pagead)", url):
        ev.update(platform="google_ads", pixel=params.get("tid") or (re.findall(r"/(\d{6,})/", url) or [None])[0],
                  event=params.get("en") or params.get("data", "")[:40] or "hit",
                  value=params.get("value"), currency=params.get("currency_code"), page=params.get("dl") or params.get("url"))
    elif "google-analytics.com" in url or "analytics.google.com" in url:
        ev.update(platform="ga4", pixel=params.get("tid"), event=params.get("en"), page=params.get("dl"))
    elif "analytics.tiktok.com" in url:
        ev["platform"] = "tiktok"
        try:
            data = json.loads(body or "{}")
            ev.update(event=data.get("event"), pixel=(data.get("context") or {}).get("pixel", {}).get("code"))
        except Exception:
            ev["event"] = "hit"
    elif "t.whop.tw" in url:
        ev.update(platform="whop", event="conversion")
    else:
        ev.update(platform="other", event=url[:80])
    if ev["page"]:
        ev["page"] = ev["page"][:160]
    return ev


class TrackingLog:
    """Records every tracking request in a browser context.

    mode="block": requests are recorded then aborted (nothing reaches the ad platforms).
    mode="observe": requests go through and are recorded (only for the real test lead / test purchase).
    """

    def __init__(self, context, mode="block", block_forms=False):
        self.events = []
        self.mode = mode
        if mode == "block":
            context.route(TRACKING_RE, self._block)
        else:
            context.on("request", self._observe)
        if block_forms:
            context.route(FORM_SUBMIT_RE, lambda route: route.abort())

    def _record(self, request, blocked):
        ev = decode_tracking(request)
        ev["blocked"] = blocked
        ev["t"] = round(time.time(), 2)
        self.events.append(ev)

    def _block(self, route):
        try:
            self._record(route.request, True)
        finally:
            route.abort()

    def _observe(self, request):
        if TRACKING_RE.search(request.url):
            self._record(request, False)

    def summary(self):
        out = []
        for e in self.events:
            if e["platform"] == "meta":
                out.append(f"meta:{e['event']}" + (f" value={e['value']} {e['currency'] or ''}" if e["value"] else ""))
            else:
                out.append(f"{e['platform']}:{e['event']}")
        return out


# ---------------------------------------------------------------- browser helpers

def launch(p, headless=True):
    return p.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled"])


def new_context(browser, device="desktop", tracking="block", block_forms=False):
    ctx = browser.new_context(**device_opts(device))
    log = TrackingLog(ctx, mode=tracking, block_forms=block_forms)
    return ctx, log


def prepare(page, url, wait_ms=2000, scroll=True, timeout=60000):
    """Open a page, wait, scroll to the bottom in steps (loads lazy images), return to top."""
    t0 = time.time()
    resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    page.wait_for_timeout(wait_ms)
    if scroll:
        height = page.evaluate("document.body.scrollHeight")
        step = 500
        y = 0
        while y < height + 1000 and y < 40000:
            page.evaluate("y => window.scrollTo(0, y)", y)
            page.wait_for_timeout(90)
            y += step
            height = page.evaluate("document.body.scrollHeight")
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(900)
    return {"status": resp.status if resp else None, "final_url": page.url, "open_ms": int((time.time() - t0) * 1000)}


def fetch_size(url, timeout=20):
    """Download a URL (as a browser would) and return (bytes, content_type, status)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/avif,image/webp,image/*,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return len(data), r.headers.get("Content-Type"), r.status
    except Exception as e:
        return None, None, str(e)[:80]


def resolve(url, timeout=20):
    """Follow redirects; return {status, final_url, title, content_type} or an error."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(400000).decode("utf-8", errors="replace")
            m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
            return {"status": r.status, "final_url": r.geturl(), "title": (m.group(1).strip()[:120] if m else None),
                    "content_type": r.headers.get("Content-Type"), "body": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final_url": url, "title": None, "error": str(e)[:100]}
    except Exception as e:
        return {"status": None, "final_url": url, "title": None, "error": str(e)[:100]}


# JS used by annotate.py and others to draw a red box + label around a page region.
MARK_JS = r"""
({box, label, pos}) => {
  const d = document.createElement('div'); d.className = '__fa_mark';
  Object.assign(d.style, {position:'absolute', left:(box.x-6)+'px', top:(box.y-6)+'px', width:(box.w+12)+'px',
    height:(box.h+12)+'px', border:'4px solid #e11d48', borderRadius:'8px', zIndex:2147483646,
    pointerEvents:'none', boxSizing:'border-box'});
  document.body.appendChild(d);
  let lab = null;
  if (label) {
    lab = document.createElement('div'); lab.className = '__fa_mark'; lab.textContent = label;
    Object.assign(lab.style, {position:'absolute', background:'#e11d48', color:'#fff', font:'700 15px Arial, sans-serif',
      padding:'5px 10px', borderRadius:'6px', zIndex:2147483647, whiteSpace:'nowrap', maxWidth:'none'});
    document.body.appendChild(lab);
    const lw = lab.offsetWidth, lh = lab.offsetHeight;
    const docW = document.documentElement.clientWidth;
    let top = pos === 'below' || (pos !== 'above' && box.y - lh - 12 < 0) ? box.y + box.h + 10 : box.y - lh - 10;
    let left = Math.min(Math.max(4, box.x - 6), Math.max(4, docW - lw - 4));
    Object.assign(lab.style, {top: top+'px', left: left+'px'});
    const r = lab.getBoundingClientRect();
    return {x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height};
  }
  return null;
}
"""

CLEAR_MARKS_JS = "() => document.querySelectorAll('.__fa_mark').forEach(e => e.remove())"

# JS: find the smallest visible element whose innerText matches a regex (case-insensitive).
FIND_TEXT_JS = r"""
({pattern, nth}) => {
  const re = new RegExp(pattern, 'i'); const hits = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.classList && el.classList.contains('__fa_mark')) continue;
    const r = el.getBoundingClientRect(); if (r.width < 4 || r.height < 4) continue;
    const st = getComputedStyle(el); if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (!re.test(el.innerText || '')) continue;
    hits.push({el, area: r.width * r.height});
  }
  // keep elements that don't contain another hit (innermost), then order by document position
  const inner = hits.filter(h => !hits.some(o => o !== h && h.el.contains(o.el)));
  const el = (inner[nth || 0] || {}).el; if (!el) return null;
  const r = el.getBoundingClientRect();
  return {x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height};
}
"""
