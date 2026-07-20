# quantnodes-strategy-research

通用策略自动研究框架 — Karpathy autoresearch 极简 + 多 Agent 增强 + 因子研发流水线

## 安装

```bash
# 开发模式安装
pip install -e ~/Public/QuantNodes/research/strategy-research

# 或作为 QuantNodes 的一部分
pip install -e ~/Public/QuantNodes
```

## 使用

### CLI 命令

```bash
# 初始化工作区
quantnodes-research init /path/to/workspace

# 查看状态
quantnodes-research status /path/to/workspace

# 复现实验
quantnodes-research reproduce /path/to/workspace run_0001
```

### 通过 QuantNodes CLI

```bash
# 如果安装了 QuantNodes
quantnodes research init /path/to/workspace
quantnodes research status /path/to/workspace
quantnodes research reproduce /path/to/workspace run_0001
```

## 工作区结构

```
/path/to/workspace/
├── README.md              # Agent 入口
├── config.yaml            # 工作区配置
├── data.duckdb            # 共享数据库
├── .git/
├── .prompts/              # Subagent 提示词
│   ├── researcher.md
│   ├── factor_analyst.md
│   ├── strategist.md
│   └── critic.md
└── strategies/
    └── {strategy_name}/
        ├── program.md     # 策略知识
        ├── prepare.py     # 目标函数
        ├── strategy.py    # Agent 修改的文件
        └── runs/          # 实验记录
```

## 设计理念

- **Karpathy 极简**: 框架提供工具和循环指引，不调 LLM
- **Skill/Harness 模式**: 外部 Agent 读 prompt 后自主决策
- **通用性**: 通过目标函数接口适配不同策略
- **实验可复现**: 每次实验保存快照，可随时复现

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查
ruff check .
```

## 许可证

MIT
