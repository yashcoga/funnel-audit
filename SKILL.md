---
name: funnel-audit
description: Full pre-launch audit of a sales funnel (GoHighLevel, Whop, ClickFunnels or any page builder) from a single starting URL — walks every page (opt-in → upsell → downsell → thank-you pages), clicks every CTA and link on desktop and mobile, submits a test opt-in, places $0 test purchases with a test/promo code, checks Meta/Google/TikTok pixel events, does a full copy and conversion review on desktop and mobile, measures page speed, and delivers a clickable HTML report with marked-up screenshots and a ranked fix list. Uses bundled, tested scripts for the mechanical work. Use when the user says "audit this funnel", "check this funnel before ads", "funnel QA", "test the flow", "go through the funnel", gives a funnel URL and asks for a check, or says "do the funnel audit" — from any folder.
---

# Funnel audit

The user gives a **starting page** (usually the opt-in). Everything else is discovered by walking the funnel.
Optional inputs: a **test/promo code** (100% off) for the checkout, a **marketing sheet** (Google Sheet with event
dates, funnel links, Zoom links), and an earlier **fix list** in the project folder.

The user wants short, plain progress messages (one line per step, no jargon) and a finished report they can work
from and share with their team. Ask only when blocked (see "Stop and ask").

**Scripts.** All mechanical work is done by the scripts in this skill's `scripts/` folder. Use the base directory
shown when this skill loads (normally `~/.claude/skills/funnel-audit`); below, `$S` means `<that folder>/scripts`.
Run them with the same Python that passed the setup check. Every script prints a short summary, writes JSON, and
has `--help`. Don't rewrite what a script already does; only use the Playwright browser tools for things no script
covers (see "Manual fallback").

## Step 0: check the setup (every run, before anything else)

1. Run `python "$S/check_setup.py"` (try `python3` or `py` if `python` isn't found). It checks the Python Playwright
   library and its Chromium, Pillow and Node.js, and prints the exact install command for anything missing. It never
   reinstalls what already works, so people who already have Playwright are not affected.
2. If a Google Sheet link was given, check the Sheets connector with ToolSearch
   `select:mcp__claude_ai_Google_Sheets__get_values`.

If anything required is missing, **stop and guide the user** in plain language: list only what's missing, why it's
needed (one line), and the exact steps for their OS (`check_setup.py` prints them):

- **Python missing:** https://www.python.org/downloads/ (Windows: tick "Add python.exe to PATH", or
  `winget install Python.Python.3.12`; Mac: `brew install python`). Reopen the terminal.
- **Python packages / Chromium missing:** offer to run the printed commands
  (`python -m pip install --upgrade playwright pillow` and `python -m playwright install chromium`) and run them if
  they say yes. Re-run the check.
- **Node.js missing** (needed for Lighthouse): https://nodejs.org/ LTS (Windows: `winget install OpenJS.NodeJS.LTS`;
  Mac: `brew install node`). Reopen the terminal.
- **Sheets connector missing** (only when a sheet was given): claude.ai → Settings → Connectors → Google Sheets, then
  restart Claude Code. Or continue without the sheet and say so in the report.

When everything passes, and the user hasn't given a URL yet (or invoked the skill with no URL), show this once:

> **You're set up. To run a funnel audit, send:**
> Audit this funnel: `<starting page URL, usually the opt-in>`
> Test code: `<100%-off promo code for the checkout>` (needed for test purchases)
> Marketing sheet: `<Google Sheet link>` (optional — used to check dates and links)
> If you have an earlier fix list, put it in this folder and I'll re-check it.
>
> You'll get one clickable HTML report in this folder: verdict, funnel map, ranked fix list with marked-up
> screenshots, copy & conversion review, tracking, page speed, and a test-purchase SOP with the test records to
> clean up.

## Scope

In scope: page flow, every CTA/link, forms and validation, checkout, redirects, pixel events, copy (desktop +
mobile, accuracy and conversion), mobile layout, page speed, compliance basics (privacy policy, SMS consent,
claims, refund text).

**Out of scope:** whether emails/SMS arrive and whether CRM automations fire. That belongs to the automation team,
not the funnel pages. Don't check inboxes or report on it.

## Safety rules (always)

- **Ad pixels are blocked by default** in every script (logged as "would have fired"). Only two things may fire live
  events: the one real test opt-in (`optin_test.py --submit`) and the $0 test purchases (`whop_checkout.py --submit`).
- **Test lead:** name `Funnel AuditTest`, a disposable `@mailinator.com` email, fictional phone `2025550123`
  (555-01xx range). Never the user's own email or a real-looking number. (These are the script defaults.)
- **Test purchases:** only with a 100%-off test code. `whop_checkout.py --submit` refuses unless the total is $0.00,
  then pays with the public Stripe test card `4242 4242 4242 4242` (12/34, CVC 123), a US address and the test
  phone. Use a different email per purchase (the default adds a random number). Never a real card. Without a code
  a live checkout declines the test card ("This payment could not be processed right now") — expected, not a bug.
  If no code was given, ask for one before any purchase.
- **Never send a purchase thank-you page to pagespeed.web.dev** (Google's test fires the live Purchase pixel).
  `pagespeed.py` refuses thank-you/confirmation URLs; use `lighthouse.py` for those.
- Do not change anything in the page builder (GoHighLevel etc.), Whop, Meta or the sheet. Findings go in the report.
- Keep all working files in a temp working folder `W` (the session scratchpad if there is one, e.g.
  `<scratchpad>/audit-<funnel-slug>`), never in the project folder. Only the final HTML report goes in the project
  folder.

## Workflow

Post one short line to the user as each step starts ("Scanning the opt-in on desktop and mobile…").

### 1. Context

- Marketing sheet (if given): list its tabs, read the ones with **event dates / Zoom links** and **funnel page
  links** (often "Marketing Plan" and "Funnel Testing & Links"). Note the next live date, cancelled sessions, the
  expected funnel URLs, old pages that may still be published.
- Earlier fix lists in the project folder (PDF/MD/HTML): note each item to re-check.
- Current date/time in the event's time zone (US events: ET) — stale dates are the most common critical issue.

### 2. Walk the funnel

For each page, in order (start page first):

```
python "$S/scan_page.py"  URL --out W/scans            # desktop + mobile: data, flags, screenshots
python "$S/test_ctas.py"  URL --out W/ctas             # every button/link, where it lands, desktop + mobile
```

- **Look at every screenshot part** the scan prints (`W/scans/<page>-desktop-part-N.png`, `-mobile-part-N.png`)
  with the Read tool. The flags catch a lot (invisible text, duplicates, sideways scroll, oversized images, dates,
  missing privacy link), but only looking catches layout and design problems.
- Read each `FLAG` line. `hidden_element_with_text` is usually the hidden twin of a mobile-only/desktop-only block
  (normal in GoHighLevel); report it only if it's a leftover (e.g. links to another business). Compare twins:
  copy_review.py lists every desktop/mobile wording difference.
- **Opt-in page:** `python "$S/optin_test.py" URL --out W/forms` (dry run: validation messages, whether the SMS
  consent box is required). Then once, the real test lead: `... --submit`. Its redirect URL is the next page.
- **Upsell / downsell with a Whop checkout:**
  `python "$S/whop_checkout.py" URL --out W/checkout --code CODE --device mobile` (dry run: plan, prices, code,
  whether the checkout is on screen after the "Yes" tap). Then one real `$0` purchase on the upsell and one on the
  downsell: `... --code CODE --submit` (desktop is fine). Record payment IDs, redirect URLs and the pixel events
  (look for duplicate Purchase/Lead events and whether Purchase carries value + currency).
- Follow every "No thanks" chain to its end with test_ctas.py results; scan each new page.
- Also scan old/unlisted pages from the sheet (e.g. a previous upsell) — are they still published?
- Other checkout types: see "Manual fallback".

### 3. Links, calendar invites, legal pages

`python "$S/check_links.py" --scans W/scans --out W` — status of every link, calendar invite date/time/Zoom link
(compare with the sheet), and whether /privacy-policy, /terms etc. really exist. Social sites (Facebook groups etc.)
block automated checks; the script says so — don't report those as broken.

### 4. Copy & conversion review (always, every page, desktop and mobile)

`python "$S/copy_review.py" --scans W/scans` then **read `W/scans/copy-review.md` in full** and review it against the
checklist below. Quote the exact current text and give exact replacement text. Small fixes go in the copy-fix
table; anything about persuasion goes in the copy & conversion review.

**Accuracy & consistency**
- Every date, time and time zone matches the live schedule (sheet) on every page *and* in the calendar invite
  (between March and November use ET/EDT, not EST).
- Prices, product names and what's included are identical across upsell → downsell → checkout → thank-you pages.
- Page titles, meta descriptions and share images match each page (not copied from another page).
- Desktop and mobile say the same thing (copy_review.py lists every difference).
- No leftovers from older offers, other clients or templates.

**Language**
- Spelling, grammar, punctuation, capitalisation, duplicated lines, broken bold/italic, missing full stops.
- Readability: short sentences, plain words; jargon explained; ALL CAPS only for short headlines.

**Compliance & trust**
- Results/income claims ("daily leads and sales", "six figures") are backed up, or softened; earnings disclaimer
  present where results are implied (also Meta ad-policy risk).
- Scarcity and urgency are true ("seats are limited", countdowns, "only on this page", "last chance").
- Privacy policy + terms linked; SMS consent optional; refund/legal text fits the page (no "all sales final" on a
  free opt-in).

**Conversion — opt-in**
- Headline states a specific outcome for a specific person; sub-headline says what it is (free, live, where, when,
  how long). Above the fold: headline, promise, date/time, CTA, and ideally a face.
- Social proof: testimonials with names and results, numbers, logos, credentials of the host. Missing proof is
  usually the biggest conversion leak for cold ad traffic.
- CTA text is specific and consistent; the form is reachable fast (popup or near the hero), few required fields.
- Objections answered (replay? length? is it a pitch? can't attend live?), clear who it's for / not for.
- Benefits over features; agenda items say what the attendee gets.

**Conversion — upsell / downsell**
- Bridges from the step before ("your seat is saved — now…"); one clear offer; price, what's included and value
  anchoring; proof; risk reversal or honest terms; the decline link is honest and readable.
- The downsell is clearly the same thing for less (or less for less) and its "last chance" is true.
- The checkout's own button and summary match the page (price, product name).

**Conversion — thank-you pages**
- Confirms the right date/time/link, tells buyers exactly how to access what they bought, gives the next steps in
  order, and gives a reason to show up live (show-up rate). No contradictory re-offers.

**Across the funnel**
- Message match with the ads (ask for ad copy if available; otherwise list it as a check before launch).
- One consistent promise and product name from ad → opt-in → offers → thank-you.

### 5. Page speed

```
python "$S/pagespeed.py"  <every non-thank-you page> --out W        # pagespeed.web.dev: scores + report links
python "$S/lighthouse.py" <thank-you pages + slowest pages> --out W # what exactly is slow, pixels blocked
```

`pagespeed.py` uses pagespeed.web.dev (free; a `PSI_API_KEY` environment variable switches to the free API).
Report mobile + desktop scores, LCP, TBT, CLS, the real-visitor Core Web Vitals result, the heaviest images and
blocking scripts (scan_page.py flags oversized images too). If testing from outside the US, mention regional
artefacts (e.g. TikTok is blocked in India and its pixel times out locally).

### 6. Marked-up screenshots (required for every visible fix)

Every fix that has to be *found* on a page gets a cropped screenshot with the spot boxed in red and a short label,
so anyone can see what to change without hunting. Skip only invisible things (pixel events, meta tags, speed).

Write `W/shots.json` (format in `annotate.py --help`): one entry per fix, with the page, device, how to find the
element (`{"selector": ...}` or `{"text": "regex"}`), an optional `extend` to include a second element, a short
label, padding, and `before` steps (e.g. click submit first to show errors, tap a CTA). For "where does the user
land" problems use `"viewport_only": true` with a `caption_bar`. Then:

`python "$S/annotate.py" W/shots.json --out W/shots` → **look at the contact sheets** it prints and re-take any
shot with a cut-off label or wrong crop (adjust `pad`, `label_pos` or the `find`).

### 7. Build the report

Write `W/findings.json` (every field is shown in `$S/report_example.json`), then:

`python "$S/build_report.py" W/findings.json --out "<project folder>"`

It writes one self-contained, clickable HTML file (screenshots embedded) named `<Funnel Name> - Funnel Audit.html`,
checks every jump link, and saves desktop and phone preview images. **Look at both previews**; fix and rebuild if
anything looks wrong. Add `--pdf` only if the user asks for a PDF.

Report content, in this order (the builder handles layout, index, links and "Back to all fixes"):

1. **Verdict** — ready for ads or not, the blocking issues, and what works.
2. **Funnel map** — each page, what every CTA/link does, where each path ends (✓ / ✗).
3. **Fix list** — ranked Critical (before ads) → High → Medium. Each fix: page, device, exactly where, a screenshot,
   "Now" (exact current text/state) → "Change to" (exact replacement).
4. **Copy fixes** — small wording fixes as Now → Change to rows.
5. **Copy & conversion review** — one card per point: what we see (quoted), recommendation (with example wording),
   priority.
6. **Tracking** — events per step, duplicates, missing value/currency.
7. **Page speed** — table with report links + real-visitor result.
8. **Re-check of the previous fix list** (if one exists).
9. **Test purchase SOP and clean-up** — exact steps that worked, payment IDs, every test record created (CRM
   contacts, checkout orders, pixel events) for the team to remove.
10. **Not tested and why**, if anything couldn't be tested.

Write for the person running the audit: plain language, short sentences, no developer jargon, exact text to paste.

### 8. Final message

A short summary: the verdict in one line, the top 3–5 fixes, the report's file link, test records to clean up,
and anything that needs the user (e.g. a decision). Don't repeat the whole report.

## Manual fallback (only when a script can't do it)

Some funnels use checkouts or forms the scripts don't support (GHL order forms, Stripe Checkout pages,
ClickFunnels, multi-step forms). `whop_checkout.py` / `optin_test.py` say so. Then use the Playwright MCP browser
tools (ToolSearch `select:mcp__playwright__browser_navigate,mcp__playwright__browser_run_code_unsafe,...`) with the
same rules: block pixels except for the one real test, code first, confirm $0.00, Stripe test card, record redirect
URL, payment ID and pixel events. If the Playwright MCP isn't installed, the user can add it once with
`claude mcp add playwright --scope user -- npx @playwright/mcp@latest` and restart Claude Code.

## Good to know (lessons from real audits)

- Pixels ignore "HeadlessChrome"; the scripts use a normal Chrome user agent so pixel behaviour is real.
- GoHighLevel keeps separate desktop-only and mobile-only copies of blocks; they drift apart (different wording,
  missing lines). Always compare.
- GHL forms re-render once after load and can wipe typed values; `optin_test.py` re-checks and retypes.
- Whop's phone country list is virtualised (type "United States" to find it); Whop requires a phone number;
  Whop sends its own Purchase (with value and event ID), so a URL-based Purchase rule on the thank-you page is a
  duplicate. URL-based Lead rules often match several pages (e.g. upsell and downsell).
- Things that only show in raw HTML (curl) but not in the browser are hidden elements — confirm in the browser
  before reporting them as visible problems.

## Stop and ask

- No test code was given (needed before any test purchase).
- The promo code is rejected or doesn't bring the total to $0.00.
- The marketing sheet and the pages disagree on which session is live and you can't tell which is right — report both.
