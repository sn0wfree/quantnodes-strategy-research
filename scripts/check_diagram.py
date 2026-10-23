from pathlib import Path
import re
import xml.etree.ElementTree as ET

p = Path("docs/architecture-diagram.html")
t = p.read_text(encoding="utf-8")
print("size", len(t.encode("utf-8")), "bytes")
m = re.search(r"<svg.*?</svg>", t, re.S)
if not m:
    print("NO SVG FOUND")
else:
    svg = m.group(0)
    try:
        root = ET.fromstring(svg)
        print("SVG well-formed. viewBox =", root.get("viewBox"))
    except ET.ParseError as e:
        print("SVG PARSE ERROR:", e)
    print("rects:", svg.count("<rect"), "texts:", svg.count("<text"), "lines:", svg.count("<line"), "paths:", svg.count("<path"))
print("external refs:", re.findall(r'(?:src|href)="(?!#)([^"]+)', t))
print("script tags:", t.count("<script"))
print("anchors:", len(re.findall(r"<li>", t)))
