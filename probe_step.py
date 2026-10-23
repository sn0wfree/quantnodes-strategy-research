from pathlib import Path
from strategy_research.core.agent.builtin_tools.backtest_tools import ConfigLoadStep
from strategy_research.core.agent.tools import ToolContext
import tempfile, os
td = tempfile.mkdtemp()
ws = Path(td)
(ws / "strategies" / "s1").mkdir(parents=True)
(ws / "strategies" / "s1" / "config.yaml").write_text("name: s1\n", encoding="utf-8")
ctx = ToolContext(workspace=ws)
out = ConfigLoadStep().execute(ctx=ctx, workspace=ws, strategy_name="s1", yaml_path=None)
print(type(out), repr(out)[:200])
