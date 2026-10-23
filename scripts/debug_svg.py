"""Why is the SVG truncated in the browser? Compare served HTML vs parsed DOM."""
from pathlib import Path
import re

import urllib.request
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765/architecture-diagram.html"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

raw = urllib.request.urlopen(URL, timeout=5).read().decode("utf-8")
print("served bytes:", len(raw))
print("source <rect:", raw.count("<rect"), " <text:", raw.count("<text"), " <path:", raw.count("<path"))
svg_src = re.search(r"<svg.*?</svg>", raw, re.S)
print("svg block bytes:", len(svg_src.group(0)) if svg_src else "NO SVG MATCH")
if svg_src:
    s = svg_src.group(0)
    print("inside svg -> rect:", s.count("<rect"), " text:", s.count("<text"), " path:", s.count("<path"))

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EDGE)
    page = b.new_page(viewport={"width": 1720, "height": 1200})
    page.goto(URL, wait_until="networkidle")
    info = page.evaluate("""() => {
      const svgs = [...document.querySelectorAll('svg')];
      return {
        count: svgs.length,
        each: svgs.map(s => ({w: s.getBoundingClientRect().width,
                              h: s.getBoundingClientRect().height,
                              rects: s.querySelectorAll('rect').length,
                              texts: s.querySelectorAll('text').length})),
        htmlHasSvgText: document.documentElement.innerHTML.includes('loop_stream 261'),
        lastTextSample: (() => {const t=[...document.querySelectorAll('text')].map(x=>x.textContent);
                                return t.slice(-4);})()
      };
    }""")
    print("DOM:", info)
    b.close()
