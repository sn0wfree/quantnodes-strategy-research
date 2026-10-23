"""Export the inline SVG from architecture-diagram.html as a standalone .svg
(GitHub renders .svg in-place, so the diagram stays reviewable in-repo)."""
from pathlib import Path
import re

HTML = Path("docs/architecture-diagram.html")
OUT = Path("docs/architecture-diagram.svg")

src = HTML.read_text(encoding="utf-8")
m = re.search(r"<svg.*?</svg>", src, re.S)
if not m:
    raise SystemExit("no <svg> found in the HTML")
svg = m.group(0)

# standalone XML: needs the xmlns (already present) and a title/desc for a11y
title = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 1170" width="1600" height="1170" '
    'role="img" aria-labelledby="t d">\n'
    '<title id="t">目标架构 v2 · 对照讨论版</title>\n'
    '<desc id="d">六层（接入/编排/引擎/能力/数据+账本/横切）· 四中枢（ContextHub / CapabilityRegistry / '
    'EventStore / InvariantLayer）· 运行期环境 RunContext 带 · 三层接缝规范 · 三层基因组（RSI）。'
    '① 反向依赖 ② SSE 双轨 ③ 持久化无契约 ④ 上下文无仲裁 ⑤ 记忆断裂 ⑥ 导演层缺失 ⑦ 引擎层反向依赖 AgentLoop。</desc>\n'
)
# strip the original opening <svg ...> tag, keep its inner content
inner = re.sub(r"^<svg[^>]*>", "", svg, count=1)
inner = re.sub(r"</svg>\s*$", "", inner, count=1)
OUT.write_text(title + inner + "\n</svg>\n", encoding="utf-8")
print("wrote", OUT, OUT.stat().st_size, "bytes")

# sanity: no HTML tags inside <text> (would break HTML-embedded rendering)
bad = re.findall(r"<text[^>]*>[^<]*<(?!/?text|/?tspan)[a-zA-Z]+[ >]", OUT.read_text(encoding="utf-8"))
print("risky tags in <text>:", len(bad))
