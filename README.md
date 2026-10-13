# Funnel Audit — Claude Code skill

Give Claude the first page of a funnel and get back a full pre-launch audit: everything to fix before ads go live, with screenshots showing exactly where.

Claude walks the whole funnel the way a buyer would, on desktop and on mobile:

- **Flow:** opt-in → upsell → downsell → thank-you pages. Every button and link is clicked, the opt-in form is
  submitted with a test lead, and a **real $0 test purchase** is placed on the upsell and the downsell using your
  100%-off test code.
- **Copy:** spelling, wrong or past dates, text that differs between desktop and mobile, inconsistent product names
  and prices across pages.
- **Tracking:** which Meta / Google / TikTok events fire on each step, with value and currency, and duplicates.
- **Page speed:** Google PageSpeed (pagespeed.web.dev) scores for mobile and desktop, plus the exact images and
  scripts slowing each page down.
- **Basics before ads:** privacy policy, SMS consent, refund text, old pages still published.

**What you get:** a clickable **PDF** and **HTML** report saved in your folder, with a verdict, a funnel map, a ranked
fix list (Critical → Polish) in "Now → Change to" format with **marked-up screenshots** of each problem, tracking,
page speed, and a test-purchase SOP listing the test records to clean up.

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

The skill checks these by itself every time it runs. If something is missing it stops, tells you what to install
and how, and offers to run the safe steps for you. You can also set everything up in advance:

| What | Why | How to install |
|---|---|---|
| **Node.js (LTS)** | Runs the browser connection and Lighthouse | [nodejs.org](https://nodejs.org/) · Windows: `winget install OpenJS.NodeJS.LTS` · Mac: `brew install node` |
| **Playwright browser connection** | Lets Claude open and click through the funnel in a real browser | In a terminal: `claude mcp add playwright --scope user -- npx @playwright/mcp@latest`, then restart Claude Code |
| **Python 3** | Screenshots and the PDF | [python.org](https://www.python.org/downloads/) (Windows: tick "Add python.exe to PATH") · Mac: `brew install python` |
| **Pillow + PyMuPDF** | Image handling and PDF checks | `python -m pip install --upgrade pillow pymupdf` |
| **Google Sheets connector** *(optional)* | Compare the funnel against your marketing sheet | claude.ai → Settings → Connectors → Google Sheets |

After installing Node.js or Python, close and reopen your terminal (or VS Code) so they're picked up.

## 3. Use it

Open Claude Code in the folder where you want the report saved, and send:

```
Audit this funnel: https://yourdomain.com/optin
Test code: YOURTESTCODE
Marketing sheet: https://docs.google.com/spreadsheets/d/...   (optional)
```

You can also type `/funnel-audit` followed by the URL. Phrases like "audit this funnel", "check this funnel before
ads" or "funnel QA" with a link load the skill automatically.

- **Starting page** (required): usually the opt-in. Claude finds the upsell, downsell and thank-you pages itself.
- **Test code** (needed for test purchases): a promo code that takes the checkout to **$0.00**. Claude pays with the
  public Stripe test card `4242 4242 4242 4242`. A live checkout declines the test card unless the total is $0, so
  without a code Claude will ask for one before any purchase.
- **Marketing sheet** (optional): lets Claude catch wrong or cancelled dates and missing pages.
- **Earlier fix list** (optional): put the PDF in the folder and Claude will check which items were done.

A full audit takes about 30–60 minutes. Claude posts short progress updates as it goes.

## Good to know

- **Test data:** the audit creates one test lead (`Funnel AuditTest`, a `@mailinator.com` email, phone
  202-555-0123) and two $0 test orders. They fire real Lead / Purchase events in your ad accounts once. Everything
  created is listed in the report so you can delete it.
- **Nothing is changed** in your page builder, checkout, ad accounts or sheet. Findings go in the report only.
- When Claude revisits thank-you pages or runs speed tests, it blocks the ad pixels so no fake purchases are counted.
- Best times to run it: once the pages are final but before ads start, and again after your fixes.
