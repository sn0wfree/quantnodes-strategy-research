"""Screenshot the architecture diagram to verify rendering."""
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765/architecture-diagram.html"
OUT = Path("docs/architecture-diagram.png")

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=EDGE)
    page = browser.new_page(viewport={"width": 1720, "height": 1400}, device_scale_factor=1)
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL, wait_until="networkidle")
    page.wait_for_timeout(600)
    # sanity: did the SVG render with expected element counts?
    stats = page.evaluate("""() => {
      const svg = document.querySelector('svg');
      if (!svg) return {svg:false};
      return {svg:true, rects:svg.querySelectorAll('rect').length,
              texts:svg.querySelectorAll('text').length,
              paths:svg.querySelectorAll('path').length,
              svgW:svg.getBoundingClientRect().width,
              svgH:svg.getBoundingClientRect().height,
              h2:document.querySelectorAll('h2').length,
              tables:document.querySelectorAll('table').length,
              bodyH:document.body.scrollHeight};
    }""")
    print("stats:", stats)
    page.screenshot(path=str(OUT), full_page=True)
    print("screenshot ->", OUT, OUT.stat().st_size, "bytes")
    if errors:
        print("console errors:", errors[:5])
    browser.close()
