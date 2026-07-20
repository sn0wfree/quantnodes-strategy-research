# Research Workspace

## 快速开始
1. 读取 `strategies/{strategy}/program.md` 了解策略
2. 读取 `strategies/{strategy}/strategy.py` 了解当前配置
3. 读取 `strategies/{strategy}/runs/results.tsv` 了解历史
4. 开始实验循环

## Subagent
可用的 subagent 提示词在 `.prompts/` 目录：

| Subagent | 文件 | 用途 |
|----------|------|------|
| Researcher | `.prompts/researcher.md` | 评估因子池，决策行动 |
| Factor Analyst | `.prompts/factor_analyst.md` | 发现并验证因子 |
| Strategist | `.prompts/strategist.md` | 集成因子到策略 |
| Critic | `.prompts/critic.md` | 评估结果，风控检查 |

何时 spawn：
- 需要因子发现/验证 → spawn Factor Analyst
- 需要评估结果 → spawn Critic
- 其他 → 主 Agent 直接执行

## 实验循环
LOOP FOREVER:
1. 读取当前状态 (strategy.py + results.tsv)
2. 决策下一步行动
3. 执行 (修改 strategy.py + 运行回测)
4. 保存到 runs/run_XXXX/
5. 更新 results.tsv
6. git commit 或 reset

## 复现实验
进入 `runs/run_XXXX/`，用 `strategy.py` 替换当前配置，运行即可。

## 工作区配置
详见 `config.yaml`。
