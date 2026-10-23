"""Can the agent originate research direction? (hypothesis/objective/directive/next-round)"""
from pathlib import Path
import re

root = Path("src/strategy_research")
files = [p for p in root.rglob("*.py") if "__pycache__" not in str(p)]

print("==== tools that can CREATE research direction")
for p in files:
    t = p.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r'^\s{4}name = "([a-z_]+)"', t, re.M):
        name = m.group(1)
        if any(k in name for k in ("hypothes", "goal", "directive", "objective", "plan", "next", "task")):
            print("  ", name, "  <-", p.name)

print("\n==== can the agent create a hypothesis? (hypothesis registry API)")
for rel in ["core/hypothesis/registry.py", "core/hypothesis/store.py", "core/hypothesis/__init__.py"]:
    q = root / rel
    if q.exists():
        t = q.read_text(encoding="utf-8", errors="ignore")
        print(f"  {rel}: defs", re.findall(r"^    def (\w+)", t, re.M)[:14])

print("\n==== does the study loop originate the NEXT round's work?")
p = root / "core/study/runner.py"
t = p.read_text(encoding="utf-8", errors="ignore")
for kw in ["directive", "objective", "hypothesis", "next_round", "_flush", "propose", "plan_next"]:
    print(f"  {kw:14s} {t.count(kw)}")
print("  -- directive producers:")
for m in re.finditer(r"^    def (\w*directive\w*)", t, re.M):
    print("     ", m.group(1))

print("\n==== goal: who can start / update it?")
for rel in ["core/goal/store.py", "core/agent/builtin_tools/goal_tools.py", "cli/commands/slash_goal.py"]:
    q = root / rel
    if q.exists():
        t = q.read_text(encoding="utf-8", errors="ignore")
        print(f"  {rel}")
        for m in re.finditer(r"^    def (\w*(?:start|create|update|replace|set)\w*)", t, re.M):
            print("     ", m.group(1))

print("\n==== phase_engine: what drives the next phase?")
p = root / "core/study/phase_engine.py"
if p.exists():
    t = p.read_text(encoding="utf-8", errors="ignore")
    print("  defs:", re.findall(r"^def (\w+)", t, re.M)[:18])
    print("  agent-driven prompts:", len(re.findall(r"run_.*_phase", t)))
