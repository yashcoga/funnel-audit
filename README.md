# Funnel Audit — Claude Code skill

Give Claude the first page of a funnel and get back a full pre-launch audit: everything to fix before ads go live,
with screenshots showing exactly where.

Claude walks the whole funnel the way a buyer would, on desktop and on mobile:

- **Flow:** opt-in → upsell → downsell → thank-you pages. Every button and link is clicked, the opt-in form is
  tested and submitted with a test lead, and a **real $0 test purchase** is placed on the upsell and the downsell
  using your 100%-off coupon code.
- **Copy & conversion:** spelling, wrong or past dates, text that differs between desktop and mobile, product names
  and prices that don't match across pages, claims and urgency that need to be true, and a conversion review of every
  page (headline, offer, proof, CTAs, objections, form, thank-you next steps).
- **Tracking:** which Meta / Google / TikTok events fire on each step, with value and currency, and duplicates.
- **Page speed:** Google PageSpeed scores (pagespeed.web.dev) for mobile and desktop, plus the exact images and
  scripts slowing each page down.
- **Basics before ads:** privacy policy, SMS consent, refund text, old pages still published.
- **Legal pages in the footer:** every Terms / Privacy / Refund / Disclaimer / Support page the funnel links to is
  opened and checked: does it exist, does it describe *this* funnel's offers and prices (no leftovers from another
  offer), brand name and contact details, "last updated" date, SMS carrier wording, and the clauses each page
  usually needs (flagged as "check with your lawyer", not legal advice).
- **Dates from GoHighLevel custom values:** every date and time must come from the custom values, not typed-in
  text, so the next class only needs the values changed. Claude swaps in a test date to prove it, and lists fixed
  calendar / Zoom links and wrong time-zone labels (EST in summer).
- **Switch-over from a live funnel:** if ads and automations already run on an old funnel, Claude compares the old
  and new opt-in (address, GHL funnel/step, form, form redirect, fields, Meta/Google/TikTok pixels) and says
  whether switching would break them, with the safe way to switch and a checklist for GHL workflows.
- **Watch it live:** every check runs in a visible Chrome window, so you can see what Claude is clicking.

**What you get:** one clickable **HTML report** saved in your folder (screenshots included, works offline, easy to
share): verdict, funnel map, ranked fix list (Critical → Medium) in "Now → Change to" format with **marked-up
screenshots**, copy fixes, copy & conversion review, tracking, dates from custom values, legal pages, page speed, and a test-purchase SOP listing the test
records to clean up.

Emails, SMS and CRM automations are out of scope.

---

## 1. Install the skill

You need [Claude Code](https://claude.com/claude-code) (terminal, desktop app or VS Code extension).

**Mac / Linux**

```bash
git clone https://github.com/yashcoga/funnel-audit.git ~/.claude/skills/funnel-audit
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/yashcoga/funnel-audit.git "$env:USERPROFILE\.claude\skills\funnel-audit"
```

No git? Download the repo as a ZIP (green **Code** button → **Download ZIP**), unzip it, and put the folder at
`~/.claude/skills/funnel-audit` (Windows: `C:\Users\<you>\.claude\skills\funnel-audit`). The folder must contain
`SKILL.md` directly.

Restart Claude Code after installing.

**To update later:** `git -C ~/.claude/skills/funnel-audit pull` (Windows:
`git -C "$env:USERPROFILE\.claude\skills\funnel-audit" pull`).

## 2. Setup the skill needs

You don't have to do this by hand: the first time you run the skill, Claude checks everything, tells you exactly
what's missing and offers to install the Python parts for you. Anything you already have (for example Playwright)
is detected and left alone.

| What | Why | How to install |
|---|---|---|
| **Python 3.9+** | Runs the audit scripts | [python.org](https://www.python.org/downloads/) (Windows: tick "Add python.exe to PATH") · Mac: `brew install python` |
| **Playwright + Pillow** (Python packages) | Drives the browser, takes and marks screenshots | `python -m pip install -r requirements.txt` (in the skill folder) |
| **Chromium for Playwright** | The browser the scripts use | `python -m playwright install chromium` |
| **Node.js (LTS)** | Lighthouse speed details | [nodejs.org](https://nodejs.org/) · Windows: `winget install OpenJS.NodeJS.LTS` · Mac: `brew install node` |
| **Google Sheets connector** *(optional)* | Compare the funnel against your marketing sheet | claude.ai → Settings → Connectors → Google Sheets |

Check it yourself any time: `python ~/.claude/skills/funnel-audit/scripts/check_setup.py`

## 3. Use it

Open Claude Code in the folder where you want the report saved, and send:

```
Audit this funnel: https://yourdomain.com/optin
Coupon code: YOURCOUPONCODE
Marketing sheet: https://docs.google.com/spreadsheets/d/...   (optional)
```

You can also type `/funnel-audit` followed by the URL. Phrases like "audit this funnel", "check this funnel before
ads" or "funnel QA" with a link load the skill automatically.

- **Starting page** (required): usually the opt-in. Claude finds the upsell, downsell and thank-you pages itself.
- **Coupon code** (needed for test purchases): a 100%-off coupon that takes the checkout to **$0.00**. Claude asks
  for it at the start of every audit if you didn't send it. On Whop checkouts Claude pays with the public Stripe
  test card `4242 4242 4242 4242`. GoHighLevel order forms still ask for a card at $0.00 and refuse the test card
  on a live Stripe account; Claude stops there and asks whether someone will finish the free order with a real
  card (charged $0.00).
- **Marketing sheet** (optional): lets Claude catch wrong or cancelled dates and old pages still published.
- **Earlier fix list** (optional): put it in the folder and Claude will check which items were done.

A full audit usually takes 30–60 minutes. Claude posts a short update as each step starts.

## What's inside

| File | What it does |
|---|---|
| `SKILL.md` | The audit playbook Claude follows (steps, safety rules, copy & conversion checklist, report format) |
| `scripts/check_setup.py` | Checks Python packages, Chromium and Node.js; prints install steps for anything missing |
| `scripts/scan_page.py` | Scans a page on desktop + mobile: links, buttons, images, text, dates, invisible text, duplicates, sideways scroll, heavy images, pixels, screenshots |
| `scripts/test_ctas.py` | Clicks every button and link and records where it lands (incl. "is the checkout on screen?") |
| `scripts/check_links.py` | Checks every link, calendar invites (date, Zoom link) and whether privacy/terms pages exist |
| `scripts/compare_live_funnel.py` | Switch-over check: compares the live (old) opt-in with the new one so ads and automations keep working |
| `scripts/check_dynamic_dates.py` | Checks that every date, time and calendar link comes from GHL custom values (swap test + match test) |
| `scripts/optin_test.py` | Tests the opt-in form (validation, required SMS box) and submits the test lead |
| `scripts/whop_checkout.py` | Tests a Whop checkout (price, code, mobile position) and places the $0 test purchase |
| `scripts/copy_review.py` | Collects all copy by section, desktop/mobile differences, prices, dates, readability and conversion signals |
| `scripts/pagespeed.py` | Google PageSpeed scores via pagespeed.web.dev (refuses thank-you pages) |
| `scripts/lighthouse.py` | Lighthouse details (heaviest images, blocking scripts), safe for thank-you pages |
| `scripts/annotate.py` | Takes the marked-up screenshots (red box + label) from a simple list |
| `scripts/build_report.py` | Builds the clickable HTML report (optional `--pdf`) |
| `scripts/report_example.json` | Example of every report field |

## Good to know

- **Test data:** the audit creates one test lead (`Funnel AuditTest`, a `@mailinator.com` email, phone
  202-555-0123) and two $0 test orders. They fire real Lead / Purchase events in your ad accounts once. Everything
  created is listed in the report so you can delete it.
- **Nothing is changed** in your page builder, checkout, ad accounts or sheet. Findings go in the report only.
- Every other page visit and speed test runs with the ad pixels blocked, so no fake leads or purchases are counted.
- Best times to run it: once the pages are final but before ads start, and again after your fixes.
