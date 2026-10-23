from pathlib import Path
import urllib.request
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765/architecture-diagram.html"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EDGE)
    page = b.new_page(viewport={"width": 1720, "height": 1200})
    page.goto(URL, wait_until="networkidle")
    info = page.evaluate("""() => {
      const svg = document.querySelector('svg');
      const texts = [...svg.querySelectorAll('text')].map(t => (t.textContent||'').slice(0,22));
      const rects = [...svg.querySelectorAll('rect')].map(r => [r.getAttribute('x'), r.getAttribute('y'), r.getAttribute('width')]);
      return {domLen: svg.outerHTML.length, nText: texts.length, texts, rects};
    }""")
    print("dom svg outerHTML len:", info["domLen"], " (source block was 18113)")
    print("\nparsed texts (%d):" % info["nText"])
    for i, t in enumerate(info["texts"]):
        print("  %2d %s" % (i, t))
    print("\nparsed rects (%d):" % len(info["rects"]))
    for r in info["rects"]:
        print("  ", r)
    b.close()
