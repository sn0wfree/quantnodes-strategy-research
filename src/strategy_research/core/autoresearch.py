"""Autoresearch 循环辅助函数。

Main Process = Orchestrator + Main Agent,串行 spawn 每个 Subagent via Task tool。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def build_agent_prompt(
    agent_name: str,
    prompts_dir: Path,
    current_state: dict[str, Any],
    previous_outputs: list[dict[str, Any]] | None = None,
) -> str:
    """构造 Agent prompt,用于 Task tool spawn。

    Args:
        agent_name: Agent 名称 (如 "researcher", "data_quality")
        prompts_dir: .prompts/ 目录路径
        current_state: 当前状态 (strategy_py, best_calmar, recent_runs)
        previous_outputs: 之前的 Agent 输出列表

    Returns:
        完整的 prompt 字符串
    """
    # 1. 角色定义 (从 .prompts/*.md 读取)
    prompt_file = prompts_dir / f"{agent_name}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
    role_def = prompt_file.read_text(encoding="utf-8")

    # 2. 当前状态
    state_info = f"""
## 当前状态

- strategy.py:
```python
{current_state.get('strategy_py', '')}
```

- 最佳 Calmar: {current_state.get('best_calmar', 0)}
- 当前 Calmar: {current_state.get('current_calmar', 0)}
- 总轮数: {current_state.get('total_runs', 0)}

### 最近 10 轮结果
```
{current_state.get('recent_runs', '')}
```
"""

    # 3. 上一个 Agent 的输出
    prev_output = ""
    if previous_outputs:
        last_output = previous_outputs[-1]
        prev_output = f"""
## 上一个 Agent 的输出

```json
{json.dumps(last_output, indent=2, ensure_ascii=False)}
```
"""

    # 4. 输出格式要求
    output_format = """
## 输出要求

**必须返回纯 JSON,不要包含任何其他文本、解释或 markdown 代码块标记。**

直接以 { 开头,以 } 结尾。

示例格式:
{"key": "value", "number": 123}
"""

    return role_def + state_info + prev_output + output_format


def save_agent_record(
    run_dir: Path,
    agent_name: str,
    step: int,
    input_data: dict[str, Any],
    output_data: dict[str, Any],
    duration_ms: int = 0,
) -> Path:
    """保存 Agent 记录到 runs/run_XXXX/agents/。

    Args:
        run_dir: run 目录路径 (如 runs/run_0013/)
        agent_name: Agent 名称
        step: 步骤号 (1-6)
        input_data: 输入数据
        output_data: 输出数据
        duration_ms: 执行耗时 (毫秒)

    Returns:
        保存的文件路径
    """
    agents_dir = run_dir / "agents"
    agents_dir.mkdir(exist_ok=True)

    record = {
        "agent": agent_name,
        "timestamp": datetime.now().isoformat(),
        "step": step,
        "input": input_data,
        "output": output_data,
        "duration_ms": duration_ms,
    }

    filepath = agents_dir / f"{agent_name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    return filepath


def read_current_state(
    workspace_path: Path,
    strategy_name: str,
) -> dict[str, Any]:
    """读取当前状态 (strategy.py + results.tsv)。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称

    Returns:
        当前状态字典
    """
    strategy_dir = workspace_path / "strategies" / strategy_name

    # 读取 strategy.py
    strategy_py_path = strategy_dir / "strategy.py"
    strategy_py = strategy_py_path.read_text(encoding="utf-8") if strategy_py_path.exists() else ""

    # 读取 results.tsv
    results_path = strategy_dir / "runs" / "results.tsv"
    if results_path.exists():
        lines = results_path.read_text(encoding="utf-8").strip().split("\n")
        header = lines[0] if lines else ""
        recent_runs = "\n".join(lines[-10:]) if len(lines) > 1 else ""
        
        # 解析最佳 Calmar
        best_calmar = 0.0
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) >= 4:
                try:
                    calmar = float(parts[3])
                    best_calmar = max(best_calmar, calmar)
                except ValueError:
                    pass
    else:
        header = ""
        recent_runs = ""
        best_calmar = 0.0

    return {
        "strategy_py": strategy_py,
        "best_calmar": best_calmar,
        "current_calmar": best_calmar,
        "total_runs": len(lines) - 1 if lines else 0,
        "recent_runs": recent_runs,
    }


def parse_agent_output(raw_output: str) -> dict[str, Any]:
    """解析 Agent 输出,自动处理 markdown 包裹。

    Args:
        raw_output: Agent 原始输出字符串

    Returns:
        解析后的字典,如果解析失败返回 {"error": "parse_failed", "raw": raw_output}
    """
    if not raw_output or not raw_output.strip():
        return {"error": "empty_output"}

    # 1. 尝试直接 JSON 解析
    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        pass

    # 2. 尝试提取 ```json ... ``` 中的内容
    json_match = re.search(r"```json\s*\n?(.*?)\n?\s*```", raw_output, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 尝试提取 ``` ... ``` 中的内容 (可能是其他格式)
    code_match = re.search(r"```\s*\n?(.*?)\n?\s*```", raw_output, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass

    # 4. 尝试提取 { ... } 或 [ ... ] 中的内容
    brace_match = re.search(r"(\{.*\})", raw_output, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(1))
        except json.JSONDecodeError:
            pass

    bracket_match = re.search(r"(\[.*\])", raw_output, re.DOTALL)
    if bracket_match:
        try:
            return json.loads(bracket_match.group(1))
        except json.JSONDecodeError:
            pass

    # 5. 所有尝试都失败
    return {"error": "parse_failed", "raw": raw_output[:1000]}


def retry_agent_spawn(
    spawn_fn,
    agent_name: str,
    max_retries: int = 3,
    retry_delay: float = 5.0,
) -> dict[str, Any]:
    """重试 Agent spawn,直到成功或达到最大重试次数。

    Args:
        spawn_fn: spawn 函数,返回原始字符串
        agent_name: Agent 名称 (用于日志)
        max_retries: 最大重试次数
        retry_delay: 重试间隔 (秒)

    Returns:
        解析后的字典
    """
    for attempt in range(max_retries):
        try:
            raw_output = spawn_fn()
            parsed = parse_agent_output(raw_output)

            # 检查是否解析成功
            if "error" not in parsed:
                return parsed

            # 解析失败,记录警告
            print(f"[autoresearch] {agent_name} 解析失败 (attempt {attempt + 1}/{max_retries}): {parsed.get('error')}")

        except Exception as e:
            print(f"[autoresearch] {agent_name} 执行异常 (attempt {attempt + 1}/{max_retries}): {e}")

        # 等待后重试
        if attempt < max_retries - 1:
            time.sleep(retry_delay)

    # 所有重试都失败
    return {"error": "max_retries_exceeded", "agent": agent_name, "attempts": max_retries}


def get_cooldown_seconds(base_cooldown: float = 30.0, jitter: float = 10.0, min_cooldown: float = 1.0) -> float:
    """计算带随机抖动的 cooldown 时间。

    Args:
        base_cooldown: 基础 cooldown (秒)
        jitter: 随机抖动范围 (±秒)
        min_cooldown: 最小 cooldown (秒)

    Returns:
        实际 cooldown 时间 (秒)
    """
    import random
    actual = base_cooldown + random.uniform(-jitter, jitter)
    return max(min_cooldown, actual)
