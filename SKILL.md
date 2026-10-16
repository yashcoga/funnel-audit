---
name: funnel-audit
description: Full pre-launch audit of a sales funnel (GoHighLevel, Whop, ClickFunnels or any page builder) from a single starting URL — walks every page (opt-in → upsell → downsell → thank-you pages), clicks every CTA and link on desktop and mobile, submits a test opt-in, places free (total 0.00) test purchases with a 100%-off coupon code, checks that a new funnel replacing a live one keeps the old opt-in URL, form and pixels so running ads and automations don't break, checks Meta/Google/TikTok pixel events, audits every legal page linked in the footer (terms, privacy, refund, support), does a full copy and conversion review on desktop and mobile, measures page speed, and delivers a clickable HTML report with marked-up screenshots and a ranked fix list. Uses bundled, tested scripts for the mechanical work. Use when the user says "audit this funnel", "check this funnel before ads", "funnel QA", "test the flow", "go through the funnel", gives a funnel URL and asks for a check, or says "do the funnel audit" — from any folder.
---

# Funnel audit

**What this is for:** funnels the team runs paid ads to, shaped like **free opt-in → one or more upsells →
downsell(s) → confirmation / thank-you page(s)** (webinar, summit, challenge, workshop funnels). Any builder:
GoHighLevel, Whop, ClickFunnels or custom HTML. The question every audit answers: *is this funnel ready to receive
ad traffic, and what has to change first?*

The user gives a **starting page** (usually the opt-in). Everything else is discovered by walking the funnel.
Inputs:
- **Coupon code** (100% off) for the paid steps. **Always ask for it at the start of the run** if it wasn't given
  with the URL (one short question, then start scanning while you wait). Without it, no test purchase can be made.
- **Live funnel it replaces.** Ask at the start, in the same message as the coupon code: *"Is this new funnel
  replacing one that already has ads or automations running? If yes, send the old opt-in link."* If the user
  doesn't know, check the live domain's home page `/` and the marketing sheet for the opt-in ads point at today.
  This decides step 2b, which is the most important check when a live funnel exists.
- Optional: a **marketing sheet** (Google Sheet with event dates, funnel links, Zoom links), the **ad copy** (for
  message match), and an earlier **fix list** in the project folder.

The user wants short, plain progress messages (one line per step, no jargon) and a finished report they can work
from and share with their team. Ask only when blocked (see "Stop and ask").

**Scripts.** All mechanical work is done by the scripts in this skill's `scripts/` folder. Use the base directory
shown when this skill loads (normally `~/.claude/skills/funnel-audit`); below, `$S` means `<that folder>/scripts`.
Run them with the same Python that passed the setup check. Every script prints a short summary, writes JSON, and
has `--help`. Don't rewrite what a script already does; only use the Playwright browser tools for things no script
covers (see "Manual fallback").

**Visible browser (default).** The user wants to watch the checks live. Run every script with
`FUNNEL_AUDIT_HEADED=1` set (bash: `export FUNNEL_AUDIT_HEADED=1`; PowerShell: `$env:FUNNEL_AUDIT_HEADED='1'`) so each
one opens a visible Chrome window, and do the hands-on checks (opening pop-ups, the test opt-in, "No thanks" clicks,
coupon on mobile) in the Playwright MCP browser, which is visible too. Say which browser each result came from.

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
> Coupon code: `<100%-off coupon code for the upsells/downsells>` (needed for test purchases)
> Marketing sheet: `<Google Sheet link>` (optional — used to check dates and links)
> If you have an earlier fix list, put it in this folder and I'll re-check it.
>
> You'll get one clickable HTML report in this folder: verdict, funnel map, ranked fix list with marked-up
> screenshots, copy & conversion review, tracking, page speed, and a test-purchase SOP with the test records to
> clean up.

## Scope

In scope: page flow, every CTA/link, forms and validation, checkout, redirects, pixel events, copy (desktop +
mobile, accuracy and conversion), mobile layout, page speed, compliance basics (privacy policy, SMS consent,
claims, refund text), and **every legal page linked from the funnel** (footer Terms / Privacy / Refund / Disclaimer /
Support / Contact pages): they are part of the funnel and get the same scan, copy check and screenshots.

**Out of scope:** whether emails/SMS arrive and whether CRM automations fire. That belongs to the automation team,
not the funnel pages. Don't check inboxes or report on it.

## Safety rules (always)

- **Ad pixels are blocked by default** in every script (logged as "would have fired"). Only two things may fire live
  events: the one real test opt-in (`optin_test.py --submit`) and the free test purchases (`whop_checkout.py --submit`, or the manual checkout test).
- **Test lead:** name `Funnel AuditTest`, a disposable `@mailinator.com` email, fictional phone `2025550123`
  (555-01xx range). Never the user's own email or a real-looking number. (These are the script defaults.)
- **Test purchases:** only with a 100%-off coupon code. `whop_checkout.py --submit` refuses unless the total is 0.00,
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
  If it reports "No redirect", read its `diagnosis` line before reporting anything: "sent nothing" or HTTP 429
  usually means an anti-bot or rate-limit problem on our side, not a broken form. Re-run once with a new `--email`
  (after 10–15 min for a 429); only report the form as broken if a normal browser fails too. Every lead the
  script or you create goes on the clean-up list.
- **Upsell / downsell with a Whop checkout:**
  `python "$S/whop_checkout.py" URL --out W/checkout --code CODE --device mobile` (dry run: plan, prices, code,
  whether the checkout is on screen after the "Yes" tap). It also handles checkouts that only load inside a pop-up
  after the "Yes" click (`checkout_in_popup`) and pages that hide Whop's own pay button and pay from a button on the
  page (`pay_button_on_page`). `promo_error: No promo-code box` means Whop isn't showing the code field: it appears
  only when the plan has an active promo code, so check the code exists for that plan before a purchase run.
  Then one real free (0.00) purchase on the upsell and one on the
  downsell: `... --code CODE --submit` (desktop is fine). Record payment IDs, redirect URLs and the pixel events
  (look for duplicate Purchase/Lead events and whether Purchase carries value + currency).
- **Upsell / downsell with a GoHighLevel order form** (the "Yes" button opens a 2-step popup; `whop_checkout.py`
  says "No Whop embedded checkout"): see "Manual fallback". Read the price, order bumps and coupon field, apply
  the coupon, and confirm the total reads 0.00 before anything else.
- Follow every "No thanks" chain to its end with test_ctas.py results; scan each new page.
- Find the **buyer's** thank-you page too, not only the decline path. If the test purchase can't complete, try
  common slugs (`/confirmation`, `/vip-confirmation`, `/thank-you-vip`, `/order-confirmation`) with curl. GHL
  sends unknown slugs to `/`, so check where each one really lands.
- Also scan old/unlisted pages from the sheet (e.g. a previous upsell) and the domain's home page `/`: are they
  still published, and do they still match the live offer? Ads and shared links often point at them.
- Other checkout types: see "Manual fallback".

### 2a. Funnel steps, wrong-page links and SEO meta tags (always, after test_ctas.py has run on every page)

`python "$S/check_funnel_steps.py" <start URL> --out W` (reads `W/ctas`)

- **Steps:** the funnel's own step list from GHL. Scan every published step, including ones nothing links to
  (a second opt-in, test pages). An unpublished step goes back to the first step.
- **Wrong-page links:** a link that "navigates" is NOT automatically right. Every link/button on a funnel page that
  lands on the same domain but outside this funnel's steps (typically an old upsell or thank-you page from the
  previous funnel, with the old design) is a fix: **Critical** if it's a "No thanks"/"Yes" link or the form
  redirect, High otherwise. Also look at the screenshot of every destination: same design and same offer as the
  rest of the funnel? Never report a link as fixed until you've opened the page it lands on.
- **SEO / share meta tags:** per step — title, meta description, og:title / og:description / og:image, canonical,
  favicon, noindex. Copied titles/descriptions (e.g. the opt-in's title on the upsell), no share image,
  auto-generated slugs (`/optin-page-1109`) and indexable offer/thank-you pages go in the fix list (High for the
  opt-in's own tags and for copied titles; Medium for the rest), with exact replacement text for every title and
  description. Report the table in the `funnel_steps` section.

### 2b. Switch-over from the live funnel (whenever ads or automations run on an old funnel)

Ads point at the old opt-in URL and optimise on its pixel; GHL workflows (tags, emails, SMS, Zoom/calendar
invites) are triggered by the old **form** and the old **funnel step**. A new page with a new form, a new funnel
or no pixel silently breaks all of them. The safe switch is almost always: **put the new design into the OLD
funnel's opt-in step, keeping its URL, form and pixels**, and only then link the new upsell/downsell/thank-you steps.

`python "$S/compare_live_funnel.py" --old <old opt-in URL> --new <new opt-in URL> --out W`

It compares, without submitting anything: address, GHL funnel / step IDs, form ID + form name, where the form
sends people, form fields, Meta pixel IDs, Google tags and TikTok pixels, and prints SAME / DIFFERENT / MISSING /
CHECK with the fix. It writes `W/switch-over.json`; copy its `switch_over` block into findings.json.

- A different form, funnel step, address or a missing Meta pixel = **Critical** ("Not safe to switch as built"),
  and the first blocker in the verdict. Put the fix in the fix list as C1.
- `CHECK` (e.g. the old form's fields load only on click) → tell the user exactly what to compare in GHL.
- GHL workflows can't be read from outside. Always include the script's GHL checklist in the report (find every
  workflow triggered by the old form/step, then one test sign-up after the switch to confirm the same tags and
  messages fire).
- No live funnel (brand-new launch)? Skip this step and say so under "Not tested and why".

### 3. Legal pages in the footer (always, every funnel)

`python "$S/check_legal_pages.py" --scans W/scans --out W` — run it after the funnel pages are scanned and before
`check_links.py` / `copy_review.py`. It finds every legal/policy link on every funnel page (Terms, Privacy, Refund,
Disclaimer, Cookies, Support, Contact), scans each one into `W/scans` (desktop + mobile screenshots, so the link and
copy checks include them), and writes `W/legal.json` with:

- **Footer matrix:** which legal links each funnel page shows. Every page needs at least Privacy + Terms; checkout
  pages also need Refund. A missing link is High (Critical on the opt-in).
- **Per page:** HTTP status / redirect (a "legal" link that lands on `/` or a 404 is Critical), page title, length
  (stub pages), "Last updated" date (missing / in the future), placeholder text, prices and offer words that appear
  nowhere in the funnel (leftovers from another client or offer, e.g. a "$49 VIP tier" on a funnel selling a $27
  bundle), brand-name casing, company name and support email vs the funnel, justified text on phones, and the clauses
  each page type normally needs (Privacy: SMS carrier wording, cookies & pixels, rights; Terms: liability, governing
  law, results disclaimer, "not financial advice" in finance niches; Refund: a clear rule for what this funnel sells
  and how to ask; Support: a way to reach a person and a response time).

Then **read every legal page yourself** (the `-desktop-part-N.png` / `-mobile-part-N.png` screenshots and the text in
`copy-review.md`):
- Does it describe **this** funnel's offers, prices, product names and checkout? Quote every sentence that doesn't.
  Leftovers from a template or an earlier offer are the most common legal-page problem.
- Do the Privacy page's SMS section and the opt-in form's consent checkbox agree, and does the checkbox text carry
  the carrier wording (STOP / HELP, message frequency, "message and data rates may apply", consent not a condition
  of purchase) plus a link to the Privacy Policy and Terms? Carriers reject A2P 10DLC texting campaigns without these.
- Does the Refund policy agree with what the offer pages promise (e.g. "money-back guarantee" on the page vs "all
  sales final" in the policy)?
- Missing clauses are "check with the client / their lawyer" items: say so, never write legal advice.

Report the results in the `legal_pages` section of findings.json (start from `legal.json` → `report_block`, add an
"action" per row with exact replacement text), and put every High/Critical legal fix in the fix list too.

### 3a. Links, calendar invites

`python "$S/check_links.py" --scans W/scans --out W` — status of every link, calendar invite date/time/Zoom link
(compare with the sheet), and whether /privacy-policy, /terms etc. really exist. Social sites (Facebook groups etc.)
block automated checks; the script says so — don't report those as broken.

### 3b. Dates and times come from GHL custom values (always)

Every date, day, time and per-class link must come from GoHighLevel custom values, never typed into the page, so
the next class only needs the custom values changed. The team's **four standard custom values** (the tags never
change; check them on every page that shows any of them):

| Tag | Used for |
|---|---|
| `{{custom_values.masterclass_date}}` | the date (opt-in, confirmation pages, countdowns) |
| `{{custom_values.masterclass_day}}` | the weekday shown ("Monday") |
| `{{custom_values.masterclass_time}}` | the time (and the CT/PT conversions) |
| `{{custom_values.masterclass_addevent}}` | every "Add to My Calendar" button / link |

`python "$S/check_dynamic_dates.py" <every page URL> --source <folder with the GHL paste files> --out W`

- **Source check** (`--source`): for every GHL paste file (.html) in the project, which of the four tags it uses,
  which it should use but doesn't (date/day/time on any page with dates; addevent on any page with a calendar
  button), typed calendar links (`addevent.com/event/…`) and typed dates in the page text. This is the only way to
  prove which tag each page uses — the live page only shows the filled-in values. When a file is missing a tag, the
  fix is a code change in that file: the date script reads `RAW_DATE`, `RAW_TIME`, `RAW_DAY`, `RAW_ADDEVENT` from the
  tags in its small merge-tag script, and the calendar button reads `MC_EVENT.calendarUrl`. If the user asks you to
  update the code, make the change, keep a backup, and test the file locally with `?masterclass_date=…&masterclass_day=…`
  `&masterclass_addevent=…` (no errors, day and link follow the values) before handing it over.
- **Swap test** (live pages with a date script, `window.MC_EVENT`): reloads with test values for all four
  (`?masterclass_date=…&masterclass_time=…&masterclass_day=Monday&masterclass_addevent=<test link>`); anything that
  doesn't change is hard-coded → fix (High; Critical if it's the main date on the opt-in). It also reports whether
  the weekday comes from `masterclass_day` (or is worked out from the date) and whether the calendar button follows
  `masterclass_addevent`. A `masterclass_day` that doesn't match the date's real weekday is a Critical copy error.
- **Match test** (all other pages, e.g. native GHL thank-you pages): `OK?` = the text equals the custom value,
  `FIX?` = extra typed words (a weekday the value doesn't have) or no match. GHL fills tags in on its server, so
  these can't be proven from outside: list them as "confirm in the GHL editor that this text is the
  `{{ custom_values.… }}` tag".
- A visible `{{ custom_values.x }}` on the page = the tag isn't being filled (wrong name / empty value) → Critical.
- `FIXED LINK` = calendar invite or Zoom link stored in the page (href or page code). It won't follow a date change:
  it must use `{{custom_values.masterclass_addevent}}`.
- The custom value text itself must be right: "7:00 PM EST" between March and November should be "7:00 PM ET".

Report it as its own section in the report ("Dates from custom values": one row per page with OK / Fix, the exact
text, and what to change) and put the fixes in the fix list.

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
1a. **Funnel steps & SEO** — every step with its title/description/share-image issues and every link that lands
   outside the funnel (build_report.py: the `funnel_steps` section, straight from `funnel-steps.json`).
1b. **Switch-over from the live funnel** (when one exists) — old vs new table and the GHL checklist
   (build_report.py: the `switch_over` section, straight from `switch-over.json`).
2. **Funnel map** — each page, what every CTA/link does, where each path ends (✓ / ✗).
3. **Fix list** — ranked Critical (before ads) → High → Medium. Each fix: page, device, exactly where, a screenshot,
   "Now" (exact current text/state) → "Change to" (exact replacement).
4. **Copy fixes** — small wording fixes as Now → Change to rows.
5. **Copy & conversion review** — one card per point: what we see (quoted), recommendation (with example wording),
   priority.
6. **Tracking** — events per step, duplicates, missing value/currency.
6b. **Dates from custom values** — per page: dynamic / hard-coded / confirm in GHL, fixed calendar & Zoom links
   (build_report.py: the `dynamic_dates` section).
6c. **Legal pages in the footer** — footer matrix (which page links to which policy) and one row per legal page:
   result, what we found, what to do (build_report.py: the `legal_pages` section; start from `legal.json`).
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
same rules: block pixels except for the one real test, coupon first, confirm the total is 0.00, Stripe test card, record redirect
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
- **"Invisible text" on GHL sections with a background image:** GHL paints section backgrounds on a separate
  layer (`div.bg.bgCover`), often with `background-attachment: fixed` (parallax). Full-page screenshots can't draw
  fixed backgrounds, so the section comes out as its plain base colour (e.g. black) and looks like black-on-black.
  The scripts now handle both (`prepare()` un-fixes those backgrounds; the contrast check skips text over an image
  layer). Still, before reporting text as invisible or a section as a "strange colour block", scroll to it in a
  normal browser window, wait a few seconds and look again (viewport screenshot, not full-page).
- **Cloudflare Turnstile on GHL forms:** many GHL forms carry a hidden `cf-turnstile-response` token. It never
  arrives in a headless browser, and without it the click silently sends nothing (or the server answers 429).
  `optin_test.py` detects this and does the one real submit in a short-lived visible browser window. That's
  expected, not a bug.
- **GHL order forms ask for a card even when the coupon makes the total 0.00** (they save the card for later
  one-click upsells). On a live Stripe account the public test card is then declined ("live mode, but used a known
  test card"), and GHL still creates a pending order to clean up. Not a funnel bug. Stop and ask the user whether
  someone will finish the free order with a real card (it's charged 0.00), or whether to leave the purchase path
  as "Not tested". Never type a real card yourself.
- **GHL phone fields pick the country from the tester's location** (outside the US the test number is saved
  as +91…/+44…). US visitors get +1; mention it only as a regional artefact.
- **GHL evergreen countdowns** restart for every new visitor and quietly disappear at zero while the offer stays.
  Check by opening the page in a fresh browser context and again after it expires.
- **Legal pages are often copied from another funnel.** Look for prices, tiers ("VIP", "General Admission"),
  "ticket" wording or product names that this funnel doesn't sell, and for the brand written in a different case.
  `check_legal_pages.py` flags these; confirm by reading the page.
- **Pop-up checkouts:** some upsell pages only load the Whop checkout after the "Yes" click and hide Whop's pay
  button (`data-whop-checkout-hide-submit-button`), paying from a button on the page instead. `whop_checkout.py`
  handles both. Whop adds an estimated tax line based on the tester's location (e.g. 18% from India), so a total
  above the page price outside the US is a regional artefact; note it and ask for a US check.
- **GHL "Submission in progress" box:** after an unfinished form attempt GHL shows its own blue "Start over /
  Continue" box on the next visit (real visitors who leave mid-form see it too). Click "Start over" in tests.
- **Calendar invites:** check the start and end of each day. A two-day event saved as one block (day 1 start →
  day 2 end) is a common mistake; `check_links.py` prints the invite's start/end.

- **Replacing a live funnel breaks automations quietly.** In one real audit the new opt-in was
  built as a new funnel with a new form (e.g. `Masterclass October 2026`) and no Meta pixel, while ads,
  two pixels and every workflow ran on the old funnel's form (e.g. `Masterclass 8-2024`). Nothing errors:
  sign-ups simply stop getting tags, emails and SMS. `compare_live_funnel.py` catches this; run it every time.
- **"The link works" ≠ "the link is right".** In the Coach K re-audit the upsell's "No thanks" was set to a page
  from the OLD funnel (old design), and it was first reported as fixed because it navigated somewhere. Always open
  the destination and compare it with the funnel (check_funnel_steps.py flags pages outside the step list).
- **GHL form element "On submit: none"** (shows the form's own thank-you message) leaves sign-ups on the opt-in.
  When a form is shared with another funnel, fix it on the page element (Action on submit → Go to next step), not
  in the form itself. compare_live_funnel.py flags it; confirm with the real test sign-up.
- **Old switch-over snapshot:** once the old opt-in is replaced, compare with the snapshot from before the switch:
  `compare_live_funnel.py --old-json <earlier switch-over.json> --new <url>`. Save that file before the switch.
- **Custom-code pop-ups grab the GHL form by its element ID** (e.g. `var GHL_FORM_ID = 'form-7A_EwDg6Cc';` in pages
  made with html-to-ghl). When the design moves into the old funnel's step, that ID must change to the old step's
  form element (e.g. `form-OILX7myDoz`), or the pop-up shows "Registration form not found". Say so in the fix.

## Stop and ask

- It's unclear whether a live funnel with ads or automations is being replaced, or which page is the old opt-in.
  Ask once at the start (with the coupon question); if the answer is "no live funnel", skip step 2b.

- No coupon code was given. Ask at the start of the run, not when you reach the checkout.
- The coupon code is rejected or doesn't bring the total to 0.00.
- The checkout needs a real card to finish a free order (see the GHL order-form note above).
- The marketing sheet and the pages disagree on which session is live and you can't tell which is right — report both.
