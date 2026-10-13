"""Check everything the funnel-audit scripts need. Only reports what's missing; never reinstalls what works.

Usage:  python check_setup.py [--json]
Exit code 0 = ready, 1 = something required is missing.
"""
import importlib
import json
import platform
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OS = platform.system()  # Windows / Darwin / Linux
PY = sys.executable


def run(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, shell=isinstance(cmd, str))
        return out.returncode == 0, (out.stdout or out.stderr).strip().splitlines()[:1]
    except Exception as e:
        return False, [str(e)[:120]]


def check_module(name):
    try:
        mod = importlib.import_module(name)
        return True, getattr(mod, "__version__", None) or getattr(mod, "VersionBind", None) or "installed"
    except Exception:
        return False, None


def check_browser():
    """The Playwright library is installed AND its Chromium launches."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True, "chromium launches"
    except Exception as e:
        msg = str(e).splitlines()[0][:160]
        return False, msg


results = []


def add(name, ok, detail, required, fix):
    results.append({"name": name, "ok": ok, "detail": detail, "required": required, "fix": None if ok else fix})


pip = f'"{PY}" -m pip install --upgrade'

ok, v = check_module("playwright")
add("Python Playwright library", ok, v, True, f"{pip} playwright")
if ok:
    bok, bdetail = check_browser()
    add("Playwright Chromium browser", bok, bdetail, True, f'"{PY}" -m playwright install chromium')
ok, v = check_module("PIL")
add("Pillow (images)", ok, v, True, f"{pip} pillow")
ok, v = check_module("pymupdf")
if not ok:
    ok, v = check_module("fitz")
add("PyMuPDF (only for the optional PDF)", ok, v, False, f"{pip} pymupdf")

node = shutil.which("node")
nok, nv = run(["node", "--version"]) if node else (False, None)
node_fix = {"Windows": "Install Node.js LTS from https://nodejs.org/ or run: winget install OpenJS.NodeJS.LTS",
            "Darwin": "Install Node.js LTS from https://nodejs.org/ or run: brew install node"}.get(
    OS, "Install Node.js LTS from https://nodejs.org/ (or your package manager)")
add("Node.js (Lighthouse)", nok, (nv or [None])[0], True, node_fix + " — then reopen the terminal")
if nok:
    xok, xv = run("npx --version")
    add("npx", xok, (xv or [None])[0], True, "npx comes with Node.js; reinstall Node.js LTS")

ready = all(r["ok"] for r in results if r["required"])

if "--json" in sys.argv:
    print(json.dumps({"ready": ready, "os": OS, "python": PY, "checks": results}, indent=2))
else:
    print(f"Python: {sys.version.split()[0]} ({PY})   OS: {OS}\n")
    for r in results:
        mark = "OK " if r["ok"] else ("MISSING" if r["required"] else "optional, not installed")
        print(f"  [{mark}] {r['name']}" + (f" — {r['detail']}" if r["ok"] and r["detail"] else ""))
    missing = [r for r in results if not r["ok"] and r["required"]]
    if missing:
        print("\nTo fix, run these (only the missing items):")
        for r in missing:
            print(f"  - {r['name']}: {r['fix']}")
    print("\nREADY" if ready else "\nNOT READY")
sys.exit(0 if ready else 1)
