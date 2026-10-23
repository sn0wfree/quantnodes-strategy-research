# A.4 — validation API 签名漂移：适配层对齐真实后端

> 来源：`docs/pyright-triage-20261008.md` A.4 · `api/routers/validation.py:50-58`
> 问题：API 调用的是**想象中的** `run_validation(run_dir, run_monte_carlo, ...)`，真实签名是 `run_validation(config, equity_curve, trades, initial_capital, market)`

## 1. 现状对照

| 调用方 | 签名用法 | 状态 |
|---|---|---|
| `validation/cli.py` | `config=` + `equity_curve=` + `trades=` + `market=` | ✅ 正确 |
| `mcp/server.py` | 同上 | ✅ 正确 |
| **`api/routers/validation.py`** | `run_dir=` + `run_monte_carlo=` + `n_simulations=` … | ❌ **全错** |

后端 `run_validation`（`core/validation/runner.py:20`）读 `config["validation"]` 开关，强制要 `equity_curve`。

## 2. 设计决策

**API 是适配层，不是第二套后端。** 修调用方，不改 `run_validation`。

复用 CLI 已有 loader（`validation/cli.py`）：
- `_load_nav_from_duckdb` → `_load_nav_synthetic`（fallback）
- `_load_trades_from_artifacts` → `_build_synthetic_trades`（fallback）
- 请求布尔/整型 → `config["validation"]` dict

```
ValidateRunRequest
  run_dir / market / monte_carlo / n_simulations / bootstrap / n_bootstrap /
  walk_forward / n_windows
        │
        ▼
  [安全校验 run_dir ⊂ VALIDATE_ROOT]
        │
        ▼
  load equity_curve + trades   ◄── validation/cli.py helpers
        │
        ▼
  config = {"validation": {monte_carlo: {n}, bootstrap: {n}, walk_forward: {n}}}
        │
        ▼
  run_validation(config, equity_curve, trades, market=MarketType(...))
```

**不改**：`run_validation` 签名、CLI、MCP（已正确）。
**改**：仅 `api/routers/validation.py` 调用段。

## 3. 实现要点

1. import CLI helpers + `MarketType` + `run_validation`
2. 路径安全校验保持（`STRATEGY_RESEARCH_VALIDATE_ROOT`）
3. equity/trades 加载失败 → 404/422（可操作错误，非 500）
4. 请求字段 → config dict（True 时写入含 n 的 dict；False 时省略 key）
5. `market` 经 `MarketType(req.market)`；非法值 → 422

## 4. 验收

- [ ] pyright `reportCallIssue` 中 validation 相关清零
- [ ] 单测：mock `run_validation`，断言收到 `config`/`equity_curve`/`trades`/`market`
- [ ] 单测：路径逃逸仍 400；run_dir 不存在 404；无 equity 404
- [ ] 既有 validation CLI/MCP 测试不回归
