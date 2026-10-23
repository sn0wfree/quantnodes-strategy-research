from pathlib import Path
import re

p = Path("docs/architecture-v2-summary.html")
t = p.read_text(encoding="utf-8")
print("size", len(t.encode("utf-8")), "bytes")
refs = re.findall(r'(?:src|href)="(?!#)([^"]+)', t)
print("external refs:", refs)
print("script tags:", t.count("<script"))
print("has doctype:", t.startswith("<!DOCTYPE html>"))
print("closes html:", t.rstrip().endswith("</html>"))
print("h2 sections:", re.findall(r"<h2>(.*?)</h2>", t))
