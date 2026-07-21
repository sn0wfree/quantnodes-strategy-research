"""Alpha Zoo + factor_validate + backtest 综合冒烟测试。

用法:
    python3 tests/integration_test.py
"""

from __future__ import annotations

import warnings
import time
from collections import Counter

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def make_test_data(n=100, n_assets=3, seed=42, nan_pct=0.0):
    np.random.seed(seed)
    dates = pd.bdate_range("2024-01-01", periods=n)
    cols = [f"S{i}" for i in range(n_assets)]

    def mk(low, high):
        arr = np.random.uniform(low, high, (n, n_assets))
        if nan_pct > 0:
            mask = np.random.random((n, n_assets)) < nan_pct
            arr[mask] = np.nan
        return pd.DataFrame(arr, index=dates, columns=cols)

    close = mk(10, 50)
    high = close * (1 + np.abs(np.random.normal(0, 0.005, (n, n_assets))))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, (n, n_assets))))
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = mk(1e6, 1e8)
    amount = mk(1e7, 1e9)
    vwap = close * (1 + np.random.normal(0, 0.001, (n, n_assets)))

    data = {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "vwap": vwap,
    }
    for w in [5, 10, 20, 50, 60]:
        data[f"adv{w}"] = volume.rolling(w).mean().fillna(volume.mean())
    return data


def test_alpha_zoo_smoke():
    print("=" * 60)
    print("TEST 1: Alpha Zoo 烟雾测试 (460 alphas, 随机数据)")
    print("=" * 60)
    from strategy_research.core.alpha_zoo import compute_alpha, list_alphas

    data = make_test_data()
    alphas = list_alphas()

    fmt_count = Counter(a["format"] for a in alphas)
    for fmt, n in sorted(fmt_count.items()):
        print(f"  因子数: {fmt} = {n}")

    failures = []
    successes = 0
    t0 = time.time()
    for a in alphas:
        try:
            r = compute_alpha(a["id"], data)
            if isinstance(r, pd.DataFrame):
                successes += 1
            else:
                failures.append((a["id"], f"wrong type {type(r).__name__}"))
        except Exception as e:
            failures.append((a["id"], str(e)[:80]))
    dt = time.time() - t0

    pct = successes / len(alphas) * 100
    print(f"\n  通过: {successes}/{len(alphas)} ({pct:.1f}%)")
    print(f"  用时: {dt:.2f}s, {dt / len(alphas) * 1000:.1f}ms/alpha")

    err_types = Counter()
    for _, err in failures:
        err_types[err.split(":")[0][:50]] += 1
    if err_types:
        print(f"\n  失败分类:")
        for et, cnt in err_types.most_common():
            print(f"    [{cnt:3d}] {et}")
    print()
    return pct == 100.0


def test_alpha_zoo_edge_cases():
    print("=" * 60)
    print("TEST 2: Alpha Zoo 边界场景")
    print("=" * 60)
    from strategy_research.core.alpha_zoo import compute_alpha, list_alphas

    alphas = list_alphas()[:50]  # 50 alphas suffices for edge cases

    scenarios = [
        ("NaN 重 (5%)", make_test_data(nan_pct=0.05)),
        ("单标的", {k: v.iloc[:, :1] if isinstance(v, pd.DataFrame) else v
                    for k, v in make_test_data().items()}),
        ("长序列 1000d", make_test_data(n=1000, seed=44)),
        ("少资产 1d", make_test_data(n=1, n_assets=1, seed=55)),
    ]

    all_pass = True
    for name, data in scenarios:
        s = 0
        f = 0
        t0 = time.time()
        for a in alphas:
            try:
                r = compute_alpha(a["id"], data)
                if isinstance(r, pd.DataFrame) and not np.any(np.isinf(r.values)):
                    s += 1
                else:
                    f += 1
            except Exception:
                f += 1
        dt = time.time() - t0
        status = "OK" if s > f else "FAIL"
        print(f"  [{status}] {name:<15} {s}/{s+f} pass ({dt:.2f}s)")
        if s <= f:
            all_pass = False
    print()
    return all_pass


def test_cross_validation():
    print("=" * 60)
    print("TEST 3: YAML vs .py 交叉验证 (alpha101 前 30 号)")
    print("=" * 60)
    import importlib
    import yaml as yamllib
    from pathlib import Path

    from strategy_research.core.alpha_zoo_yaml import compute_alpha_from_yaml

    data = make_test_data(n=252, n_assets=5, seed=123)
    data["returns"] = data["close"].pct_change().fillna(0)

    matches = 0
    diffs = 0
    skip = 0
    details = []
    for i in range(1, 31):
        aid = f"alpha101_alpha_{i:03d}"
        yaml_path = Path(f"src/strategy_research/core/alpha_zoo/alpha101/alpha_{i:03d}.yaml")
        try:
            cfg = yamllib.safe_load(yaml_path.read_text())
            yaml_res = compute_alpha_from_yaml(cfg, data)
            py_mod = importlib.import_module(
                f"strategy_research.core.alpha_zoo.alpha101.alpha_{i:03d}"
            )
            py_res = py_mod.compute(data)
            a, b = yaml_res.values.flatten(), py_res.values.flatten()
            mask = ~(np.isnan(a) | np.isnan(b))
            if mask.sum() < 10:
                skip += 1
                continue
            diff = np.abs(a[mask] - b[mask])
            rel = diff.mean() / (np.abs(b[mask]).mean() + 1e-10)
            try:
                corr = np.corrcoef(a[mask], b[mask])[0, 1]
            except Exception:
                corr = float("nan")
            if corr > 0.99 and rel < 0.1:
                matches += 1
                status = "OK"
            else:
                diffs += 1
                status = "DIFF"
            details.append((aid, status, corr, rel))
        except Exception as e:
            skip += 1

    print(f"\n  Exact match (corr>0.99 & rel<0.1): {matches}/30")
    print(f"  Semantic difference:                {diffs}/30")
    print(f"  Skip (load failure):                {skip}/30")

    if diffs:
        print(f"\n  Differences:")
        for aid, st, c, r in details:
            if st == "DIFF":
                print(f"    {aid}: corr={c:.4f} rel={r:.2e}")
    print()
    return matches >= 25


def test_factor_validate():
    print("=" * 60)
    print("TEST 4: factor_validate 模块")
    print("=" * 60)
    from strategy_research.core.factor_validate import (
        compute_ic, compute_ic_decay, compute_mutual_ic,
        compute_6d_scores, validate_factor,
        score_coverage, score_monotonicity, score_turnover,
        score_stability, score_rank_ic, deduplicate_factors,
    )

    n = 504
    dates = pd.bdate_range("2022-01-01", periods=n)
    factor = pd.Series(np.random.randn(n), index=dates)
    rets = pd.Series(np.random.randn(n) * 0.01, index=dates)
    prices = pd.DataFrame(
        np.cumprod(1 + np.random.randn(n, 5) * 0.01, axis=0) * 10,
        index=dates,
        columns=list("ABCDE"),
    )

    tests = [
        ("compute_ic", lambda: compute_ic(factor, rets)),
        ("compute_ic_decay", lambda: compute_ic_decay(factor, prices)),
        ("compute_mutual_ic", lambda: compute_mutual_ic(factor, factor)),
        ("score_coverage", lambda: score_coverage(factor)),
        ("score_monotonicity", lambda: score_monotonicity(factor, rets)),
        ("score_turnover", lambda: score_turnover(factor)),
        ("score_stability", lambda: score_stability(factor)),
        ("score_rank_ic", lambda: score_rank_ic(0.05)),
        ("deduplicate_factors", lambda: deduplicate_factors([
            {"name": "f1", "ic_series": pd.Series(np.random.randn(n))},
            {"name": "f2", "ic_series": pd.Series(np.random.randn(n))},
        ])),
        ("validate_factor", lambda: validate_factor(
            "test", prices, forward_returns=rets, factor_values=factor
        )),
    ]

    fail = 0
    for name, fn in tests:
        try:
            r = fn()
            keys = list(r.keys()) if isinstance(r, dict) else type(r).__name__
            print(f"  [OK] {name:<25} -> {keys if isinstance(keys, str) else f'{{...}}'}")
        except Exception as e:
            print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
            fail += 1
    print()
    return fail == 0


def test_end_to_end():
    print("=" * 60)
    print("TEST 5: 端到端 - 用真实 alpha 计算 + IC 评估")
    print("=" * 60)
    from strategy_research.core.alpha_zoo import compute_alpha
    from strategy_research.core.factor_validate import compute_ic

    np.random.seed(7)
    n = 252
    dates = pd.bdate_range("2023-01-01", periods=n)
    n_stocks = 30

    prices = pd.DataFrame(
        np.cumprod(1 + np.random.randn(n, n_stocks) * 0.02, axis=0) * 10,
        index=dates,
        columns=[f"S{i}" for i in range(n_stocks)],
    )
    rets = prices.pct_change().fillna(0)

    data_panel = {
        "open": prices,
        "high": prices * 1.005,
        "low": prices * 0.995,
        "close": prices,
        "volume": pd.DataFrame(np.random.uniform(1e6, 1e8, (n, n_stocks)),
                               index=dates, columns=prices.columns),
        "amount": pd.DataFrame(np.random.uniform(1e7, 1e9, (n, n_stocks)),
                               index=dates, columns=prices.columns),
        "vwap": prices,
    }
    for w in [5, 10, 20]:
        data_panel[f"adv{w}"] = data_panel["volume"].rolling(w).mean().fillna(1e7)

    test_alpha_id = "alpha101_alpha_004"
    print(f"\n  测试 alpha: {test_alpha_id}")
    factor_df = compute_alpha(test_alpha_id, data_panel)
    print(f"  factor shape: {factor_df.shape}")

    # Cross-sectional mean per day
    factor_cross_mean = factor_df.mean(axis=1)
    fwd_ret = rets.shift(-1).mean(axis=1).dropna()
    aligned = factor_cross_mean.loc[fwd_ret.index].dropna()

    ic = compute_ic(aligned, fwd_ret.loc[aligned.index])
    print(f"  Daily cross-section IC: mean={ic['ic_mean']:.4f} std={ic['ic_std']:.4f}")
    print(f"  (this alpha isn't designed to be predictive on random data, so IC ~ 0 is expected)")

    print()
    return True


if __name__ == "__main__":
    results = {}
    print("\n" + "=" * 60)
    print("STRATEGY-RESEARCH 集成测试套件")
    print("=" * 60)
    print()

    results["smoke"] = test_alpha_zoo_smoke()
    results["edge"] = test_alpha_zoo_edge_cases()
    results["cross"] = test_cross_validation()
    results["validate"] = test_factor_validate()
    results["e2e"] = test_end_to_end()

    print("=" * 60)
    print("汇总")
    print("=" * 60)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n** {passed}/{total} tests passed **")
