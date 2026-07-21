"""Alpha Zoo .py → .yaml 批量转换脚本。

分析每个因子 .py 文件的 compute() 函数，
尝试转换为 YAML AST 格式。
"""
from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path
from typing import Any

import yaml


# ============================================================
# Python AST 分析
# ============================================================

class ComputeFunctionAnalyzer(ast.NodeVisitor):
    """分析 compute() 函数的 AST，提取操作序列。"""

    def __init__(self):
        self.panel_refs: dict[str, str] = {}  # variable_name -> panel_key
        self.assignments: dict[str, Any] = {}  # variable_name -> expression
        self.return_expr: Any = None
        self.complexity = 0  # 0=simple, 1=medium, 2=complex

    def visit_Assign(self, node):
        """处理赋值语句: x = some_expression"""
        if len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                var_name = target.id
                value = self._analyze_expr(node.value)
                self.assignments[var_name] = value
        self.generic_visit(node)

    def visit_Return(self, node):
        """处理 return 语句"""
        if node.value:
            self.return_expr = self._analyze_expr(node.value)
        self.generic_visit(node)

    def _analyze_expr(self, node) -> Any:
        """分析表达式，返回简化表示。"""
        # panel 引用: panel["close"]
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "panel":
                if isinstance(node.slice, ast.Constant):
                    key = node.slice.value
                    return {"_type": "panel_ref", "key": key}
                elif isinstance(node.slice, ast.Index) and isinstance(node.slice.value, ast.Constant):
                    key = node.slice.value.value
                    return {"_type": "panel_ref", "key": key}

        # 方法调用: close.pct_change(fill_method=None)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            obj = self._analyze_expr(node.func.value)
            method = node.func.attr
            args = [self._analyze_expr(arg) for arg in node.args]
            # 忽略 fill_method 关键字参数
            for kw in node.keywords:
                if kw.arg != "fill_method":
                    args.append({"_type": "kwarg", "name": kw.arg, "value": self._analyze_expr(kw.value)})
            return {"_type": "method_call", "obj": obj, "method": method, "args": args}

        # float() / int() 类型转换
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("float", "int"):
            if len(node.args) == 1:
                arg = self._analyze_expr(node.args[0])
                # 如果参数是常量，直接返回转换后的值
                if isinstance(arg, dict) and arg.get("_type") == "value":
                    if node.func.id == "float":
                        return {"_type": "value", "value": float(arg["value"])}
                    else:
                        return {"_type": "value", "value": int(arg["value"])}
                # 否则返回参数本身（类型转换在 YAML 中不需要）
                return arg

        # 函数调用: ts_mean(close, 20)
        if isinstance(node, ast.Call):
            func_name = self._get_func_name(node)
            if func_name:
                # 处理关键字参数
                args = [self._analyze_expr(arg) for arg in node.args]
                for kw in node.keywords:
                    if kw.arg == "fill_method":
                        # 忽略 fill_method 参数
                        continue
                    # 其他关键字参数作为命名参数
                    args.append({"_type": "kwarg", "name": kw.arg, "value": self._analyze_expr(kw.value)})
                return {"_type": "call", "func": func_name, "args": args}

        # 变量引用: x
        if isinstance(node, ast.Name):
            return {"_type": "ref", "name": node.id}

        # 常量: 20, 0.5
        if isinstance(node, ast.Constant):
            return {"_type": "value", "value": node.value}

        # 一元负: -x
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            operand = self._analyze_expr(node.operand)
            return {"_type": "call", "func": "neg", "args": [operand]}

        # 二元运算: a + b, a * b
        if isinstance(node, ast.BinOp):
            left = self._analyze_expr(node.left)
            right = self._analyze_expr(node.right)
            op_map = {
                ast.Add: "add",
                ast.Sub: "sub",
                ast.Mult: "mul",
                ast.Div: "div",
                ast.Pow: "pow",
            }
            op_name = op_map.get(type(node.op))
            if op_name:
                return {"_type": "call", "func": op_name, "args": [left, right]}

        # 比较运算: a < b, a > b
        if isinstance(node, ast.Compare):
            if len(node.ops) == 1 and len(node.comparators) == 1:
                op = node.ops[0]
                left = self._analyze_expr(node.left)
                right = self._analyze_expr(node.comparators[0])
                op_map = {
                    ast.Lt: "lt",
                    ast.LtE: "lte",
                    ast.Gt: "gt",
                    ast.GtE: "gte",
                    ast.Eq: "eq",
                    ast.NotEq: "neq",
                }
                op_name = op_map.get(type(op))
                if op_name:
                    return {"_type": "call", "func": op_name, "args": [left, right]}

        # 复杂表达式
        self.complexity = max(self.complexity, 2)
        return {"_type": "complex", "source": ast.dump(node)}

    def _get_func_name(self, node) -> str | None:
        """获取函数调用的名称。"""
        if isinstance(node.func, ast.Name):
            return node.func.id
        # np.xxx() 调用
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "np":
                return node.func.attr
            return node.func.attr
        return None


def analyze_compute_function(py_path: Path) -> dict:
    """分析 .py 文件中的 compute() 函数。

    Returns:
        dict: {
            "meta": {...},
            "analyzer": ComputeFunctionAnalyzer,
            "complexity": int,
            "error": str or None
        }
    """
    try:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as e:
        return {"meta": {}, "analyzer": None, "complexity": 2, "error": str(e)}

    # 提取 __alpha_meta__
    meta = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__alpha_meta__":
                    try:
                        meta = ast.literal_eval(node.value)
                    except Exception:
                        pass

    # 查找 compute() 函数
    analyzer = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute":
            analyzer = ComputeFunctionAnalyzer()
            analyzer.visit(node)
            break

    if analyzer is None:
        return {"meta": meta, "analyzer": None, "complexity": 2, "error": "No compute() found"}

    return {"meta": meta, "analyzer": analyzer, "complexity": analyzer.complexity, "error": None}


# ============================================================
# 表达式转换: Python AST -> YAML AST
# ============================================================

def python_ast_to_yaml_ast(expr: dict, panel_keys: set[str], func_refs: dict = None) -> dict | None:
    """将 Python AST 分析结果转换为 YAML AST。

    Args:
        expr: Python AST 分析结果
        panel_keys: 可用的 panel 键集合
        func_refs: 函数引用映射 (var_name -> helper function name)

    Returns:
        YAML AST 节点，或 None (如果无法转换)
    """
    if func_refs is None:
        func_refs = {}

    # 使用内部函数进行递归调用，自动传递 func_refs
    def _convert(e):
        return python_ast_to_yaml_ast(e, panel_keys, func_refs)
    if expr["_type"] == "panel_ref":
        key = expr["key"]
        if key in panel_keys:
            return {"column": key}
        return None

    if expr["_type"] == "value":
        return {"value": expr["value"]}

    if expr["_type"] == "ref":
        name = expr["name"]
        # 忽略 numpy 前缀
        if name == "np":
            return None
        # 检查是否是函数引用
        if name in func_refs:
            helper_name = func_refs[name]
            # 映射到正确的算子名
            op_map = {
                "_rolling_sum": "ts_sum",
                "_rolling_mean": "ts_mean",
                "_rolling_std": "ts_std",
                "_rolling_var": "ts_var",
                "_rolling_min": "ts_min",
                "_rolling_max": "ts_max",
                "_rolling_rank": "ts_rank",
                "_rolling_corr": "ts_corr",
                "_rolling_cov": "ts_cov",
                "_where_ternary": "where",
                "_sma": "ts_mean",
                "_make_one": "fill_null",
            }
            return {"op": op_map.get(helper_name, helper_name)}
        # 处理辅助函数引用
        helper_map = {
            "_delay": {"op": "delay"},
            "_make_one": {"op": "fill_null"},
            "_rolling_sum": {"op": "ts_sum"},
            "_rolling_mean": {"op": "ts_mean"},
            "_rolling_std": {"op": "ts_std"},
            "_rolling_var": {"op": "ts_var"},
            "_rolling_min": {"op": "ts_min"},
            "_rolling_max": {"op": "ts_max"},
            "_rolling_rank": {"op": "ts_rank"},
            "_rolling_corr": {"op": "ts_corr"},
            "_rolling_cov": {"op": "ts_cov"},
            "_where_ternary": {"op": "where"},
            "_sma": {"op": "ts_mean"},
            "_ind_neutralize": None,
            "_bench_close": None,
            "panel": None,
            "pick": None,
        }
        if name in helper_map:
            return helper_map[name]
        return {"ref": name}

    # 处理 rolling/ewm 节点
    if expr["_type"] == "rolling":
        # rolling 节点作为参数时，尝试转换为对应的算子
        obj = expr.get("obj")
        window = expr.get("window", {"value": 20})
        if obj:
            obj_yaml = _convert(obj)
            if obj_yaml is not None:
                # 返回一个占位符，实际的合并会在 method_call 中处理
                return {"_type": "rolling", "obj": obj_yaml, "window": window}
        return None

    if expr["_type"] == "ewm":
        # ewm 节点作为参数时，尝试转换为对应的算子
        obj = expr.get("obj")
        span = expr.get("span", {"value": 20})
        if obj:
            obj_yaml = _convert(obj)
            if obj_yaml is not None:
                # 返回一个占位符，实际的合并会在 method_call 中处理
                return {"_type": "ewm", "obj": obj_yaml, "span": span}
        return None

    if expr["_type"] == "method_call":
        obj = expr["obj"]
        method = expr["method"]
        args = expr["args"]

        # 特殊处理 np.xxx() 调用 (numpy 前缀)
        if isinstance(obj, dict) and obj.get("_type") == "ref" and obj.get("name") == "np":
            # np.log(volume) -> log(volume)
            yaml_args = []
            for arg in args:
                arg_yaml = _convert(arg)
                if arg_yaml is None:
                    return None
                yaml_args.append(arg_yaml)
            return {"op": method, "args": yaml_args}

        # 特殊处理 rolling 方法
        if method == "rolling":
            window = {"value": 20}
            for kw in args:
                if isinstance(kw, dict) and kw.get("_type") == "kwarg":
                    if kw.get("name") == "window":
                        window = kw.get("value")
                elif isinstance(kw, dict) and kw.get("_type") == "value":
                    window = kw
            return {"_type": "rolling", "obj": obj, "window": window}

        if method == "ewm":
            span = {"value": 20}
            for kw in args:
                if isinstance(kw, dict) and kw.get("_type") == "kwarg":
                    if kw.get("name") == "span":
                        span = kw.get("value")
                elif isinstance(kw, dict) and kw.get("_type") == "value":
                    span = kw
            return {"_type": "ewm", "obj": obj, "span": span}

        # 链式调用合并: 先转换 obj，再检查是否为 rolling/ewm
        converted_obj = _convert(obj)
        if isinstance(converted_obj, dict) and converted_obj.get("_type") in ("rolling", "ewm"):
            rolling_info = converted_obj
            rolling_type = rolling_info["_type"]
            inner_obj = rolling_info["obj"]

            rolling_method_map = {
                "mean": "ts_mean" if rolling_type == "rolling" else "ewm_mean",
                "std": "ts_std" if rolling_type == "rolling" else "ewm_std",
                "sum": "ts_sum" if rolling_type == "rolling" else None,
                "min": "ts_min" if rolling_type == "rolling" else None,
                "max": "ts_max" if rolling_type == "rolling" else None,
                "rank": "ts_rank" if rolling_type == "rolling" else None,
                "corr": "ts_corr" if rolling_type == "rolling" else "ewm_corr",
                "cov": "ts_cov" if rolling_type == "rolling" else None,
                "skew": "ts_skew" if rolling_type == "rolling" else None,
                "kurt": "ts_kurt" if rolling_type == "rolling" else None,
                "var": "ts_var" if rolling_type == "rolling" else None,
                "median": "ts_median" if rolling_type == "rolling" else None,
                "quantile": None,
                "apply": None,
            }

            if method in rolling_method_map and rolling_method_map[method]:
                op_name = rolling_method_map[method]
                yaml_args = []
                if inner_obj is None:
                    return None
                yaml_args.append(inner_obj)

                if method == "corr":
                    if args:
                        arg_yaml = _convert(args[0])
                        if arg_yaml is None:
                            return None
                        yaml_args.append(arg_yaml)
                    yaml_args.append(rolling_info["window"])
                elif method == "cov":
                    if args:
                        arg_yaml = _convert(args[0])
                        if arg_yaml is None:
                            return None
                        yaml_args.append(arg_yaml)
                    yaml_args.append(rolling_info["window"])
                else:
                    yaml_args.append(rolling_info["window"])

                return {"op": op_name, "args": yaml_args}

            if method == "quantile":
                q_val = _convert(args[0]) if args else {"value": 0.5}
                return {"op": "ts_rank", "args": [inner_obj, rolling_info["window"]]}

            if method == "apply":
                return None

        # 其他方法调用
        yaml_args = []
        if converted_obj is None:
            return None
        yaml_args.append(converted_obj)
        for arg in args:
            if isinstance(arg, dict) and arg.get("_type") == "kwarg":
                continue
            arg_yaml = _convert(arg)
            if arg_yaml is None:
                return None
            yaml_args.append(arg_yaml)
        return {"op": method, "args": yaml_args}

    if expr["_type"] == "call":
        func = expr["func"]
        args = expr["args"]

        # 检查是否是函数引用
        if func in func_refs:
            helper_name = func_refs[func]
            op_map = {
                "_rolling_sum": "ts_sum",
                "_rolling_mean": "ts_mean",
                "_rolling_std": "ts_std",
                "_rolling_var": "ts_var",
                "_rolling_min": "ts_min",
                "_rolling_max": "ts_max",
                "_rolling_rank": "ts_rank",
                "_rolling_corr": "ts_corr",
                "_rolling_cov": "ts_cov",
                "_where_ternary": "where",
                "_sma": "ts_mean",
                "_make_one": "fill_null",
            }
            func = op_map.get(helper_name, helper_name)

        # 处理参数 (过滤关键字参数)
        yaml_args = []
        for arg in args:
            if isinstance(arg, dict) and arg.get("_type") == "kwarg":
                # 跳过关键字参数
                continue
            yaml_arg = _convert(arg)
            if yaml_arg is None:
                return None
            yaml_args.append(yaml_arg)

        # 特殊处理 _cross_sectional_zscore -> zscore
        if func == "_cross_sectional_zscore":
            func = "zscore"
        # 特殊处理 _delay -> delay
        elif func == "_delay":
            func = "delay"
        # 特殊处理 _rolling_sum -> ts_sum
        elif func == "_rolling_sum":
            func = "ts_sum"
        # 特殊处理 _rolling_mean -> ts_mean
        elif func == "_rolling_mean":
            func = "ts_mean"
        # 特殊处理 _rolling_std -> ts_std
        elif func == "_rolling_std":
            func = "ts_std"
        # 特殊处理 _rolling_var -> ts_var
        elif func == "_rolling_var":
            func = "ts_var"
        # 特殊处理 _rolling_min -> ts_min
        elif func == "_rolling_min":
            func = "ts_min"
        # 特殊处理 _rolling_max -> ts_max
        elif func == "_rolling_max":
            func = "ts_max"
        # 特殊处理 _rolling_rank -> ts_rank
        elif func == "_rolling_rank":
            func = "ts_rank"
        # 特殊处理 _rolling_corr -> ts_corr
        elif func == "_rolling_corr":
            func = "ts_corr"
        # 特殊处理 _rolling_cov -> ts_cov
        elif func == "_rolling_cov":
            func = "ts_cov"
        # 特殊处理 _where_ternary -> where
        elif func == "_where_ternary":
            func = "where"
        # 特殊处理 _sma -> ts_mean (drop min_periods parameter)
        elif func == "_sma":
            func = "ts_mean"
            # _sma(x, n, m) -> ts_mean(x, n) - drop the m parameter
            if len(yaml_args) > 2:
                yaml_args = yaml_args[:2]
        # 特殊处理 _ind_neutralize -> 忽略行业中心化
        elif func == "_ind_neutralize":
            # 返回第一个参数
            if yaml_args:
                return yaml_args[0]
            return None
        # 特殊处理 _bench_close -> 忽略基准收盘价
        elif func == "_bench_close":
            # 返回第一个参数
            if yaml_args:
                return yaml_args[0]
            return None
        # 特殊处理 sum -> ts_sum
        elif func == "sum":
            func = "ts_sum"
        # 特殊处理 mean -> ts_mean
        elif func == "mean":
            func = "ts_mean"
        # 特殊处理 std -> ts_std
        elif func == "std":
            func = "ts_std"
        # 特殊处理 var -> ts_var
        elif func == "var":
            func = "ts_var"
        # 特殊处理 min -> ts_min
        elif func == "min":
            func = "ts_min"
        # 特殊处理 max -> ts_max
        elif func == "max":
            func = "ts_max"
        # 特殊处理 median -> ts_median
        elif func == "median":
            func = "ts_median"
        # 特殊处理 skew -> ts_skew
        elif func == "skew":
            func = "ts_skew"
        # 特殊处理 kurt -> ts_kurt
        elif func == "kurt":
            func = "ts_kurt"
        # 特殊处理 quantile -> ts_rank
        elif func == "quantile":
            func = "ts_rank"
        # 特殊处理 cumsum -> expanding_sum
        elif func == "cumsum":
            func = "expanding_sum"
        # 特殊处理 cumprod -> 忽略
        elif func == "cumprod":
            if yaml_args:
                return yaml_args[0]
            return None
        # 特殊处理 log1p -> log
        elif func == "log1p":
            func = "log"
        # 特殊处理 pow -> signed_power
        elif func == "pow":
            func = "signed_power"
        # 特殊处理 power -> signed_power
        elif func == "power":
            func = "signed_power"
        # 特殊处理 minimum -> ts_min
        elif func == "minimum":
            func = "ts_min"
        # 特殊处理 maximum -> ts_max
        elif func == "maximum":
            func = "ts_max"
        # 特殊处理 shift -> delay
        elif func == "shift":
            func = "delay"
        # 特殊处理 diff -> delta
        elif func == "diff":
            func = "delta"
        # 特殊处理 pct_change -> ts_return
        elif func == "pct_change":
            func = "ts_return"
        # 特殊处理 fillna -> fill_null
        elif func == "fillna":
            func = "fill_null"
        # 特殊处理 replace -> 忽略
        elif func == "replace":
            if yaml_args:
                return yaml_args[0]
            return None
        # 特殊处理 to_numpy -> 忽略
        elif func == "to_numpy":
            if yaml_args:
                return yaml_args[0]
            return None

        return {"op": func, "args": yaml_args}

    if expr["_type"] == "complex":
        return None

    return None


# ============================================================
# YAML 生成
# ============================================================

def convert_py_to_yaml(py_path: Path) -> dict | None:
    """将 .py 因子转换为 YAML 配置。

    Returns:
        dict: YAML 配置，或 None (如果无法转换)
    """
    result = analyze_compute_function(py_path)
    if result["error"] or result["analyzer"] is None:
        return None

    analyzer = result["analyzer"]
    meta = result["meta"]

    # 提取 panel 键引用
    panel_keys = set()
    for var_name, expr in analyzer.assignments.items():
        if expr["_type"] == "panel_ref":
            panel_keys.add(expr["key"])

    # 收集所有需要的列
    columns_required = meta.get("columns_required", list(panel_keys))

    # 辅助函数映射 (用于识别函数引用)
    helper_names = {
        "_delay", "_make_one", "_rolling_sum", "_rolling_mean", "_rolling_std",
        "_rolling_var", "_rolling_min", "_rolling_max", "_rolling_rank",
        "_rolling_corr", "_rolling_cov", "_where_ternary", "_sma",
        "_ind_neutralize", "_bench_close", "_cross_sectional_zscore",
    }

    # 将 assignments 转换为 steps
    # 首先识别函数引用 (如 rolling_sum = _rolling_sum)
    func_refs = {}  # var_name -> helper function name
    for var_name, expr in analyzer.assignments.items():
        if expr["_type"] == "ref" and expr["name"] in helper_names:
            func_refs[var_name] = expr["name"]

    steps = []
    for var_name, expr in analyzer.assignments.items():
        # 跳过函数引用 (如 rolling_sum = _rolling_sum)
        if var_name in func_refs:
            continue

        yaml_expr = python_ast_to_yaml_ast(
            expr, panel_keys | {k for k in analyzer.assignments}, func_refs
        )
        if yaml_expr is None:
            return None  # 无法转换的复杂表达式

        # 规范化表达式
        yaml_expr = _normalize_yaml(yaml_expr)
        if yaml_expr is None:
            return None

        steps.append({
            "name": var_name,
            "expr": yaml_expr,
        })

    # 将 return 转换为 final
    if analyzer.return_expr is None:
        return None

    final_expr = python_ast_to_yaml_ast(
        analyzer.return_expr, panel_keys | {k for k in analyzer.assignments}, func_refs
    )
    if final_expr is None:
        return None

    # 规范化 final 表达式
    final_expr = _normalize_yaml(final_expr)
    if final_expr is None:
        return None

    # 构建 YAML 配置
    config = {
        "id": meta.get("id", py_path.stem),
        "zoo": meta.get("zoo", py_path.parent.name),
        "nickname": meta.get("nickname", ""),
        "theme": meta.get("theme", []),
        "formula_latex": meta.get("formula_latex", ""),
        "columns_required": columns_required,
        "universe": meta.get("universe", []),
        "frequency": meta.get("frequency", []),
        "decay_horizon": meta.get("decay_horizon", 0),
        "min_warmup_bars": meta.get("min_warmup_bars", 0),
        "steps": steps,
        "final": final_expr,
    }

    return config


def yaml_to_string(config: dict) -> str:
    """将配置转换为 YAML 字符串。"""
    config = _normalize_yaml(config)
    return yaml.dump(
        config,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )


def _normalize_yaml(node):
    """递归规范化 YAML AST 节点，去除内部 _type 标记。"""
    if isinstance(node, dict):
        if "_type" in node:
            t = node["_type"]
            if t == "value":
                return node.get("value", 0)
            if t == "column":
                return {"column": node.get("key", "")}
            if t == "ref":
                return {"ref": node.get("name", "")}
            if t == "call":
                func = node.get("func", "")
                args = [_normalize_yaml(a) for a in node.get("args", [])]
                return {"op": func, "args": args}
            if t == "method_call":
                method = node.get("method", "")
                obj = _normalize_yaml(node.get("obj"))
                args = [_normalize_yaml(a) for a in node.get("args", [])]
                return {"op": method, "args": [obj] + args}
            if t == "panel_ref":
                return {"column": node.get("key", "")}
            if t == "rolling":
                return None
            if t == "ewm":
                return None
            if t == "kwarg":
                return _normalize_yaml(node.get("value"))
            if t == "complex":
                return None
        # 规范化 op/args 结构
        result = {}
        for k, v in node.items():
            if k == "args" and isinstance(v, list):
                result[k] = [_normalize_yaml(a) for a in v]
            elif isinstance(v, dict):
                result[k] = _normalize_yaml(v)
            else:
                result[k] = v
        return result
    if isinstance(node, list):
        return [_normalize_yaml(item) for item in node]
    return node


# ============================================================
# 批量转换
# ============================================================

def convert_zoo(zoo_dir: Path, output_dir: Path | None = None) -> dict:
    """转换整个 zoo 目录。

    Args:
        zoo_dir: zoo 目录路径
        output_dir: 输出目录 (默认为 zoo_dir 同级的 yaml 目录)

    Returns:
        dict: {success: int, failed: int, skipped: int, details: [...]}
    """
    if output_dir is None:
        output_dir = zoo_dir.parent / f"{zoo_dir.name}_yaml"

    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {"success": 0, "failed": 0, "skipped": 0, "details": []}

    for py_file in sorted(zoo_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        try:
            config = convert_py_to_yaml(py_file)
            if config is None:
                stats["skipped"] += 1
                stats["details"].append({"file": py_file.name, "status": "skipped"})
                continue

            yaml_str = yaml_to_string(config)
            yaml_file = output_dir / f"{py_file.stem}.yaml"
            yaml_file.write_text(yaml_str, encoding="utf-8")
            stats["success"] += 1
            stats["details"].append({"file": py_file.name, "status": "success"})

        except Exception as e:
            stats["failed"] += 1
            stats["details"].append({"file": py_file.name, "status": "failed", "error": str(e)})

    return stats


def convert_all_zoos(alpha_zoo_dir: Path) -> dict:
    """转换所有 zoo 目录。"""
    all_stats = {}
    for zoo_dir in sorted(alpha_zoo_dir.iterdir()):
        if zoo_dir.is_dir() and not zoo_dir.name.startswith("_"):
            print(f"转换 {zoo_dir.name}...")
            stats = convert_zoo(zoo_dir)
            all_stats[zoo_dir.name] = stats
            print(f"  成功: {stats['success']}, 跳过: {stats['skipped']}, 失败: {stats['failed']}")

    return all_stats


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import sys

    alpha_zoo_dir = Path(__file__).parent / "alpha_zoo"
    if len(sys.argv) > 1:
        alpha_zoo_dir = Path(sys.argv[1])

    print(f"Alpha Zoo 目录: {alpha_zoo_dir}")
    stats = convert_all_zoos(alpha_zoo_dir)

    # 汇总
    total_success = sum(s["success"] for s in stats.values())
    total_failed = sum(s["failed"] for s in stats.values())
    total_skipped = sum(s["skipped"] for s in stats.values())
    print(f"\n总计: 成功 {total_success}, 跳过 {total_skipped}, 失败 {total_failed}")
