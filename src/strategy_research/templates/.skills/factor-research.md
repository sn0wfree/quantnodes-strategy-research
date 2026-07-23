---
name: factor-research
category: strategy
description: 因子研究方法论 — IC/IR 验证 + 6 维评分 + 单调性 + 衰减分析
tags: [factor, ic, ir, validation, alpha]
---

# Factor Research Framework

借鉴自 vibe-trading factor-research skill，适配 strategy-research 框架。

## IC/IR 标准

| 指标 | 阈值 | 含义 |
|------|------|------|
| IC mean | > 0.03 | 因子有基本预测力 |
| IC mean | > 0.05 | 因子有强预测力 |
| IC mean | > 0.10 | 异常高，检查前视偏差 |
| IR (IC mean / IC std) | > 0.5 | 因子稳定有效 |
| IR | > 1.0 | 极强，非常罕见 |
| IC > 0 占比 | > 55% | 因子方向稳定 |
| IC > 0 占比 | < 50% | 因子方向不稳定，不可用 |

## 因子验证流程

### Step 1: IC/IR 验证
```python
# 计算 IC (Pearson 或 Spearman)
ic_series = factor_values.groupby(level=0).apply(
    lambda g: g["factor"].corr(g["return"], method="spearman")
)
ic_mean = ic_series.mean()
ic_std = ic_series.std()
ir = ic_mean / ic_std if ic_std > 0 else 0
```

### Step 2: 6 维评分

| 维度 | 权重 | 指标 |
|------|------|------|
| Stability | 0.25 | IC 正比例 + IC 标准差 |
| Diversification | 0.20 | 截面标准差均值 |
| Turnover | 0.15 | 相邻日期 Rank 相关性 |
| Monotonicity | 0.20 | 5 分位收益单调性 |
| Coverage | 0.10 | 非空比例 |
| Rank IC | 0.10 | 基于 IC 绝对值 |

### Step 3: Mutual IC 去重
- 与已有因子计算 |Spearman corr|
- |corr| < 0.7 才保留

### Step 4: IC 衰减检查
- IC_5d >= 30% * IC_1d

## 因子组合方法

### 等权组合
```
复合因子 = Z(factor1) + Z(factor2) + ... + Z(factorN)
其中 Z() 是截面 Z-score 标准化
```

### IC 加权组合
```
weight_i = |IC_mean_i| / sum(|IC_mean_j|)
复合因子 = sum(weight_i * Z(factor_i))
```

### 正交化组合
```
1. 按 IC 从高到低排序因子
2. 保留第一个因子不变
3. 对后续每个因子，回归到所有先前因子上，用残差作为正交化因子
4. 等权组合正交化因子
```

## 分组回测解读

### 标准
- **单调性**: Group_1 到 Group_N 的最终净值应呈单调递增（或递减）
- **多空价差**: 最高组和最低组的净值差（long_short_spread），越大越好
- **非线性**: 如果只有顶底组差异明显，中间组相似，因子可能仅在尾部有效
- **稳定性**: 分组权益曲线应平滑；剧烈波动表示因子不稳定

### 警告信号
- 分组权益曲线无明显差异 → 因子无效
- 非单调模式（V 形或倒 V 形）→ 因子可能有非线性关系
- 某组净值持续下跌 → 因子可能可反向使用

## 常见陷阱

1. **前视偏差**: 因子值必须用 T 日及之前数据计算，收益必须用 T+1 到 T+N 数据
2. **偏态分布**: 某些因子（如市值、换手率）严重右偏，直接计算 IC 会被异常值主导
3. **行业效应**: 因子值在同一行业内可能高度相似，导致选股集中在少数行业
4. **样本不足**: 每个截面至少需要 5 个有效资产
5. **因子拥挤**: 经典因子（动量、价值）被广泛使用后超额收益可能衰减
6. **幸存者偏差**: 仅在仍存活的股票上回测会高估因子表现

## Alpha Zoo 因子库 (465+ 因子)

策略研究框架内置 465+ 预置因子，通过 `AlphaZooAdapter` 访问。

### 快速上手

```python
from strategy_research.core.alpha_zoo_adapter import AlphaZooAdapter

adapter = AlphaZooAdapter()

# 列出所有因子
all_alphas = adapter.list_alphas()

# 按主题筛选
momentum_alphas = adapter.list_alphas(theme="momentum")
reversal_alphas = adapter.list_alphas(theme="reversal")

# 按市场筛选
cn_alphas = adapter.list_alphas(universe="equity_cn")

# 计算单个因子 (宽 DataFrame 格式)
factor_wide = adapter.compute_as_wide("gtja191_001", prices)

# 计算单个因子 (Series 格式，兼容现有验证流程)
factor_series = adapter.compute_as_series("gtja191_001", prices)

# 批量计算
factors_df = adapter.compute_batch(
    ["gtja191_001", "alpha101_001", "qlib158_ma10"],
    prices
)

# 检查健康状态
health = adapter.health()
```

### 可用因子库

| 因子库 | 数量 | 主题 | 说明 |
|--------|------|------|------|
| alpha101 | 101 | momentum/reversal/volatility/volume | Kakushadze 公式化因子 |
| gtja191 | 191 | volume/reversal/momentum/volatility | 国泰君安 A 股截面因子 |
| qlib158 | 158 | momentum/volatility/volume | 微软 Qlib ML 因子 |
| academic | 11 | momentum/value | Fama-French + Carhart |
| fundamental | 4 | quality/value | ROE, 盈利收益率等 |

### 在 config.yaml 中使用

```yaml
factors:
  # 方式 1: 表达式因子 (现有)
  - name: momentum_20d
    code: ts_return(close, 20)
    weight: 0.5

  # 方式 2: Alpha Zoo 因子 (新增)
  - name: gtja191_001
    alpha_id: gtja191_001
    weight: 0.3

  # 方式 3: 批量组合
  - name: momentum_composite
    alpha_ids:
      - alpha101_001
      - gtja191_010
      - qlib158_ma10
    combination: ic_weighted
    weight: 0.4
```

### 主题说明

| 主题 | 说明 | 典型因子 |
|------|------|----------|
| momentum | 动量 | 过去 N 日收益、均线偏离 |
| reversal | 反转 | 短期反转、条件收益动量 |
| volatility | 波动率 | 已实现波动率、偏度、峰度 |
| volume | 量价 | 量价相关、换手率、成交额 |
| value | 价值 | PE、PB、股息率 |
| quality | 质量 | ROE、毛利率、现金流 |
| liquidity | 流动性 | Amihud 非流动性、换手率 |
| sentiment | 情绪 | 涨跌停、量比 |
| growth | 成长 | 收入增速、利润增速 |
| leverage | 杠杆 | 资产负债率、有息负债率 |
| microstructure | 微观结构 | 买卖价差、订单流 |

### 算子参考

Alpha Zoo 使用 17 个核心算子 (定义在 `alpha_zoo_ops.py`):

**截面算子**: rank, zscore, scale
**时序算子**: ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, ts_min, ts_argmax, ts_argmin
**滞后差分**: delta
**工具算子**: decay_linear, signed_power, safe_div, vwap
