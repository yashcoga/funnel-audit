---
name: funnel-audit
description: Full pre-launch audit of a sales funnel (GoHighLevel, Whop, ClickFunnels or any page builder) from a single starting URL — walks every page (opt-in → upsell → downsell → thank-you pages), clicks every CTA and link on desktop and mobile, submits a real test opt-in, runs the checkout with a test/promo code, checks the Meta/Google/TikTok pixel events, audits copy on desktop and mobile, and measures page speed. Produces a prioritised fix list. Use when the user says "audit this funnel", "check this funnel before ads", "funnel QA", "test the flow", "go through the funnel", gives a funnel URL and asks for a check, or says "do the funnel audit" — from any folder.
---

# Funnel audit

The user gives a **starting page** (usually the opt-in). Everything else is discovered by walking the funnel.
Optional inputs: a **test/promo code** for the checkout, a **marketing sheet** (Google Sheet with event dates,
funnel links, Zoom links), and any earlier **fix list** in the project folder.

The user wants short, plain progress messages and a finished report they can hand to the funnel builder.
Ask only when blocked (see "Stop and ask").

## Step 0: check the setup (every run, before anything else)

Run these checks silently and in parallel. If all pass, say nothing about setup and continue.

| Needed for | Check | Pass when |
|---|---|---|
| Browser testing (required) | `ToolSearch` query `select:mcp__playwright__browser_navigate` | the tool schema comes back |
| Screenshots + PDF (required) | `python -c "import PIL, pymupdf; print('ok')"` (try `python3` / `py` if `python` is missing) | prints `ok` |
| Lighthouse speed detail (required) | `node --version` and `npx --version` | both print a version |
| Marketing sheet (only if the user gives a Google Sheet link) | `ToolSearch` query `select:mcp__claude_ai_Google_Sheets__get_values` | the tool schema comes back |

If anything fails, **stop and guide the user** in plain language. List only what's missing, say why it's needed in
one line, and give the exact steps for their operating system (check it with `uname -s` / the platform info):

- **Python missing:** install from https://www.python.org/downloads/ (Windows: tick "Add python.exe to PATH" in the
  installer; or `winget install Python.Python.3.12`. Mac: `brew install python`). Then reopen the terminal.
- **Pillow / PyMuPDF missing (Python is there):** offer to run it for them —
  `python -m pip install --upgrade pillow pymupdf` — and run it if they say yes. Re-check afterwards.
- **Node.js missing:** install the LTS version from https://nodejs.org/ (Windows: `winget install OpenJS.NodeJS.LTS`;
  Mac: `brew install node`). Then reopen the terminal.
- **Playwright browser connection missing:** it's an MCP server that lets Claude drive a real browser. The user runs
  this once in a terminal (not inside the chat), then fully restarts Claude Code (or the VS Code window):
  `claude mcp add playwright --scope user -- npx @playwright/mcp@latest`
  Needs Node.js first. If the first browser action later says the browser isn't installed, call the Playwright
  `browser_install` tool (or have them run `npx playwright install chrome`).
- **Google Sheets connector missing** (only when a sheet was given): connect Google Sheets in claude.ai →
  Settings → Connectors, then restart Claude Code. Or skip the sheet: the audit still runs, it just can't compare
  dates and links against it — say so in the report.

After the missing pieces are installed (and Claude Code restarted where needed), re-run the checks, then show this
quick guide once and wait for their input if they haven't already given a URL:

> **You're set up. To run a funnel audit, send:**
> Audit this funnel: `<starting page URL, usually the opt-in>`
> Test code: `<100%-off promo code for the checkout>` (needed for test purchases)
> Marketing sheet: `<Google Sheet link>` (optional — used to check dates and links)
> If you have an earlier fix list, put it in this folder and I'll re-check it.
>
> You'll get a clickable PDF and HTML report in this folder: verdict, funnel map, ranked fix list with marked-up
> screenshots, tracking, page speed, and a test-purchase SOP with the test records to clean up.

If the user invoked the skill with no URL at all, show the same guide (after the setup check) and wait.

## Scope

In scope: page flow, every CTA/link, forms and validation, checkout, redirects, pixel events, copy (desktop + mobile),
mobile layout, page speed, compliance basics (privacy policy, SMS consent, refund text).

**Out of scope:** whether emails/SMS arrive and whether CRM automations fire. That belongs to the automation team,
not the funnel build. Don't check inboxes or report on it.

## Safety rules (always)

- **Opt-in test lead:** use a clearly fake name (`Funnel AuditTest`), a disposable email
  (`<funnel>-audit-<MMDD>@mailinator.com`) and a fictional phone (`202-555-0123`, the 555-01xx range).
  Never use the user's own email or a real-looking phone number.
- **Test purchases (user-approved SOP):** apply the 100%-off test code first, confirm the total reads $0.00, then pay
  with the Stripe test card `4242 4242 4242 4242`, expiry `12/34`, CVC `123`, a US billing address and the 555-01xx
  phone (Whop requires a phone; set its country picker to US (+1) by typing "United States" — the list is virtualised).
  Use a different disposable email per purchase. Never use a real card. Without the code a live checkout declines the
  test card ("This payment could not be processed right now") — that confirms the checkout is live, not a bug.
  If no test code is given, ask for one before attempting a purchase.
- **Don't pollute ad data.** Only the one real test opt-in may fire live pixel events. For every other visit — thank-you
  pages reached without a real purchase, Lighthouse runs, mobile re-checks — block the beacons with Playwright
  `context.route(...)` and *log* what would have fired:
  `facebook\.com/tr|google\.com/(rmkt|ccm|pagead)|googleadservices|analytics\.tiktok|t\.whop\.tw/conversions|google-analytics\.com/g/collect`
  (add `backend\.leadconnectorhq\.com/forms/submit` when re-testing a form you don't want to submit).
- Do not change anything in the funnel builder, Whop, Meta or the sheet. Findings go in the report.

## Setup

1. Load Playwright tools with ToolSearch (`browser_navigate`, `browser_run_code_unsafe`, `browser_evaluate`,
   `browser_network_requests`, `browser_network_request`, `browser_resize`, `browser_snapshot`, `browser_tabs`).
2. Save screenshots and scratch files in a temp folder (the session scratchpad if there is one, `shots/` inside it),
   never in the project folder — only the final PDF and HTML go there.
   Playwright drops logs in `<cwd>/.playwright-mcp/`; move any request-body files you save out of the project.
3. If a marketing sheet is given: read the tabs that list **event dates** and **funnel links**
   (often a "Marketing Plan" tab with dates and Zoom links and a "Funnel Testing & Links" tab with page URLs; list the
   sheet's tabs first and pick the matching ones). Note the next live date, cancelled sessions, the expected funnel
   URLs and Zoom links.
4. Look in the project folder for earlier fix lists (PDF/MD) and note which items to re-verify.
5. Check the current date/time in the event's time zone (ET for US events) — date copy is the #1 thing that goes stale.

## Walk the funnel (desktop 1440×900 first, then mobile 390×844 iPhone UA, `isMobile`, `hasTouch`)

For **each page**:

- Scroll to the bottom in steps (triggers lazy images), back to top, then take a full-page screenshot. Split tall
  screenshots into ~1800 px strips (desktop) or tile 3–4 strips side by side (mobile) with PIL, and **look at them** —
  invisible headings (white on white), duplicated paragraphs, empty gaps, broken images only show up visually.
- `evaluate` to collect: title, meta description, og:image, every `<a>` (text, href, target, visible), every
  `<button>` (text, id, visible), iframes, images (naturalWidth 0 = broken, alt text, natural vs displayed size),
  full `innerText` for the copy review, nav timing (TTFB, FCP), and on mobile `scrollWidth` + elements whose
  right edge is past the viewport (sideways scroll).
- **Click every CTA.** Record where it goes (URL change, scroll to form/#order, popup). On mobile, after tapping a
  "buy" CTA, check the checkout form is actually on screen (`iframe.getBoundingClientRect().top` < ~600) —
  page builders often stack the order summary above the form on mobile.
- Open FAQ accordions; check they expand.
- Resolve every external link with `curl -sIL` (calendar links: also read the event date/Zoom ID inside and compare
  with the sheet; community links: 200?).

### Opt-in form

1. Submit empty → note each validation message. 2. Bad email + short phone → note messages.
3. Check whether the SMS-consent checkbox is **required** (it must be optional — TCPA/A2P 10DLC) and whether the
   error text references terms that don't exist.
4. Submit the test lead (safety rules above). Record the redirect URL and the pixel events fired
   (read `facebook.com/tr` POST bodies: `ev=` name, `cd[value]`, `cd[currency]`, `eid`).

### Upsell / downsell / checkout

- Note checkout platform, plan IDs and `payment_redirect_url` from the iframe `src`.
- Check the displayed total matches the page price. Apply the test code; confirm the new total.
- **Complete one real test purchase on the upsell and one on the downsell** (safety rules SOP). Record the payment ID
  and the redirect URL. Log every `facebook.com/tr` request during the purchase — including those from inside the
  checkout iframe (`page.on('request')` sees subframes): many checkouts (Whop) send their own Purchase with value +
  event ID, so a page-URL Purchase rule on the thank-you page is a **duplicate**. Decode bodies with
  `urllib.parse.parse_qsl` and report `ev`, `cd[value]`, `cd[currency]`, `cd[eventID]`/`eid` per event.
- List every test record created (CRM contacts, checkout orders/memberships, pixel events) in the report so the team
  can clean them up.
- Click every "No thanks" link and follow the chain to the end (downsell → GA thank-you).
- Visit purchase thank-you pages **with beacons blocked**; log whether `Purchase` would fire and whether it carries
  `value` + `currency`, and whether different products share one thank-you page (value can't be told apart).

### Also check

- Old/unused pages from the sheet still published (e.g. a previous upsell) — broken order forms, links to other
  clients' domains.
- `/privacy-policy`, `/privacy`, `/terms`, `/terms-and-conditions` and a nonsense path — does a privacy policy exist,
  and is it linked in the footer? (Meta requires one for lead ads/landing pages that collect data.)
- Refund/disclaimer text matches the product (e.g. "in-person events" on a free virtual class).

## Copy review (desktop and mobile)

Read the `innerText` of every page and the screenshots. Flag: wrong/past dates or time zones (EST vs EDT/ET),
dates that differ from the sheet, spelling/grammar, duplicated lines, text that differs between desktop and mobile
versions of the same block (GHL keeps separate mobile/desktop elements), product names inconsistent across upsell →
downsell → thank-you, prices inconsistent across pages (e.g. thank-you re-offers $27 after a $17 decline), page
titles copied from another page, image text typos, leftover "VIP"/old-offer wording, hidden leftover sections.

## Page speed

1. **Primary: pagespeed.web.dev in Playwright.** Open one tab per page at
   `https://pagespeed.web.dev/analysis?url=<encoded page URL>&form_factor=mobile` (all tabs in parallel), wait for
   "Diagnose performance issues", then read the visible tab panel for both the Mobile and Desktop tabs: the
   Performance score (the first number before "Performance" in the lab section — don't confuse it with the
   Accessibility score), FCP, LCP, TBT, CLS, Speed Index, and the real-user "Core Web Vitals Assessment"
   (Passed/Failed, field LCP/CLS). Keep each tab's final URL — it's a shareable report link for the PDF.
   **Never send a purchase thank-you page to pagespeed.web.dev** — Google's test browser fires the live Purchase
   pixel. Run those pages with local Lighthouse (step 2) with beacons blocked.
   The PageSpeed API (`runPagespeed?...`) needs a free API key from Google Cloud (no paid tier); without one the
   shared quota is usually exhausted (429).
2. Local Lighthouse for thank-you pages and for the per-image/per-script detail, in the background, beacons blocked:
   `npx -y lighthouse@12.2.1 <url> [--preset=desktop] --only-categories=performance --output=json
   --output-path=<scratch>/lh/<page>-<strategy>.json --chrome-flags="--headless=new"
   --blocked-url-patterns=*facebook.com/tr* --blocked-url-patterns=*analytics.tiktok.com* …`
3. Report score, FCP, LCP, TBT, CLS, Speed Index, page weight, request count, and the top opportunities.
4. Measure image weight directly (`curl -s -o /dev/null -w "%{size_download}"`) — GHL serves original uploads; a
   1080 px PNG shown at 100 px is a common, easy win. Compare natural vs displayed size.
5. If testing from outside the US, note regional artefacts (e.g. TikTok is blocked in India, so its pixel times out
   and inflates "load" time — not a problem for US visitors).

## Annotated screenshots (required, every audit)

Every fix the designer has to *find* on a page gets a cropped screenshot with the exact spot marked, so anyone can
see what to change without hunting. Skip screenshots only for things that aren't visible (pixel events, meta tags,
speed numbers).

1. Capture after all findings are known, in fresh Playwright contexts (desktop 1440×900; mobile 390×844 at
   `deviceScaleFactor: 2`) with the ad beacons **and** the form-submit endpoint blocked, so no events or leads fire.
2. Scroll each page top to bottom first so lazy images load.
3. Mark the spot by injecting an absolutely positioned `div` (4px solid `#e11d48` border, rounded) around the
   element's document box (`getBoundingClientRect()` + `scrollX/scrollY`), plus a red label chip with a short
   plain-language caption ("Duplicate paragraph", "SMS consent is required to register"). Put the label below the box
   when the box is near the top of the page, and keep it inside the crop.
4. Screenshot with `fullPage: true` and a `clip` around the marked element with enough context to recognise where it
   is (about 40–260 px around it). For mobile issues use the mobile context. For "where does the user land" issues
   (e.g. a CTA that scrolls above the checkout) take the viewport after the tap and add a fixed caption bar.
5. Wrap each capture in try/catch so one failure doesn't stop the rest. Look at every capture (contact sheet with PIL)
   and re-take any with a cut-off label or wrong crop.
6. Things that only show in raw HTML (curl) but not in the browser are hidden elements — confirm in the browser
   before reporting them as visible problems.
7. Embed in the report as JPEG data URIs (quality ~80, max 1100 px wide; mobile shown at ~280 px) in a `<figure>` with a
   caption naming the page and device, placed just before that fix's "Now / Change to" box. In print CSS, keep each
   figure on one page (`break-inside: avoid`, `max-height: 120mm`) but let long fix cards break.

## Report

One report per funnel, written for the funnel builder / team:

1. **Verdict** — ready for ads or not, in one line, with the blocking issues.
2. **Funnel map** — each page URL, what each CTA/link does, where each path ends (tested ✓ / ✗).
3. **Fix list**, ranked: *Critical (before ads)* → *High* → *Medium* → *Polish*. Each item: page, device, exactly
   where, what's there now, what to change it to. Use the same "current → change to" format as earlier fix lists.
4. **Tracking** — events per page, what's missing (value/currency, Lead vs custom events, duplicates).
5. **Speed** — table per page × mobile/desktop + top fixes.
6. **Re-check of the previous fix list** (if one exists): done / not done per item.
7. **Test purchase SOP and clean-up** — the exact steps that worked (code, test card, required fields), payment IDs,
   and every test record to remove (CRM contacts, checkout orders/memberships, pixel events that fired).
8. **Not tested and why**, if anything couldn't be tested.

**Format: a PDF plus the same report as an HTML file, both saved in the project folder** as
`<Funnel Name> - Funnel Audit.pdf` / `.html` (the user asked for files, not an Artifact). Build one HTML file
(light theme for print, `@page` A4, white print background, `break-inside: avoid` on figures, tables rows and
"Now / Change to" boxes but not on whole fix cards, which may be taller than a page), then render it
with Playwright `page.pdf({ printBackground: true, displayHeaderFooter: true })` with a page-number footer.
**Everything must be clickable:** every funnel URL, external link (calendar, community, Zoom, the marketing sheet),
each PageSpeed report link, a top nav, an "All fixes at a glance" index that jumps to each fix, and a
"Back to all fixes ↑" link on every fix. After rendering, check with `pymupdf` that the page count and link count
look right, and look at 1–2 rendered pages.

## Stop and ask

- No test code was given (needed before any test purchase).
- The promo code is rejected or doesn't bring the total to $0.00.
- The marketing sheet and the page disagree on which session is live and you can't tell which is right — report both.
