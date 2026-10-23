# Pyright batch 2 — B 机械注解对齐清单

> 来源：`docs/pyright-current.json` · 规则：['reportOperatorIssue', 'reportOptionalContextManager', 'reportOptionalIterable', 'reportOptionalSubscript', 'reportReturnType']
> 总 errors 951 · 本批 107 · 文件 25

## 按文件

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\compute_factor.py` (39)

  52:12  reportReturnType  Type "Any | DataFrame | Series" is not assignable to return type "Series"
  57:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  62:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  67:12  reportReturnType  Type "Series | DataFrame" is not assignable to return type "Series"
  77:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown | Any" is not assignable to return type "Series"
  82:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown | Any" is not assignable to return type "Series"
  87:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown | Self@DataFrame | Any | ndarray[_AnyShape, dtype[Any]]" is not assignable 
  92:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown | Self@DataFrame | Any | ndarray[_AnyShape, dtype[Any]]" is not assignable 
  97:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  102:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  111:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  116:12  reportReturnType  Type "Series* | Unknown | DataFrame | Series | Self@DataFrame | Any | ndarray[_AnyShape, dtype[Any]]" is not assignable 
  121:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  126:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  131:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  136:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  159:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  164:12  reportReturnType  Type "DataFrame | Series" is not assignable to return type "Series"
  173:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown" is not assignable to return type "Series"
  178:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown" is not assignable to return type "Series"
  183:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown" is not assignable to return type "Series"
  188:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown" is not assignable to return type "Series"
  197:12  reportReturnType  Type "DataFrame | Series" is not assignable to return type "Series"
  207:12  reportReturnType  Type "DataFrame | Series" is not assignable to return type "Series"
  313:12  reportReturnType  Type "DataFrame | Series | Any" is not assignable to return type "DataFrame"
  353:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  358:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  366:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  373:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  406:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  429:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  444:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  473:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "Series"
  588:12  reportReturnType  Type "NDArray[Incomplete]" is not assignable to return type "Series"
  593:12  reportReturnType  Type "NDArray[Incomplete]" is not assignable to return type "Series"
  598:12  reportReturnType  Type "NDArray[Incomplete]" is not assignable to return type "Series"
  664:12  reportReturnType  Type "NDArray[float64]" is not assignable to return type "Series"
  1124:24  reportReturnType  Type "Series | Unknown | Self@DataFrame" is not assignable to return type "Series"
  1240:16  reportReturnType  Type "Series | Unknown | Self@DataFrame" is not assignable to return type "Series | float | int"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\alpha_zoo_ops.py` (9)

  88:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "DataFrame"
  94:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "DataFrame"
  99:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown | Self@DataFrame | Any | ndarray[_AnyShape, dtype[Any]]" is not assignable 
  104:12  reportReturnType  Type "Any | DataFrame | Series" is not assignable to return type "DataFrame"
  109:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown | Any" is not assignable to return type "DataFrame"
  114:12  reportReturnType  Type "DataFrame | Series | Series* | Unknown | Any" is not assignable to return type "DataFrame"
  179:12  reportReturnType  Type "DataFrame | Series | Any | Unknown" is not assignable to return type "DataFrame"
  184:12  reportReturnType  Type "NDArray[float64]" is not assignable to return type "DataFrame"
  212:12  reportReturnType  Type "DataFrame | None" is not assignable to return type "DataFrame"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\alpha_zoo_convert.py` (6)

  133:12  reportReturnType  Type "dict[str, int] | Unknown | dict[Unknown, Unknown] | None" is not assignable to return type "dict[Unknown, Unknown]
  145:12  reportReturnType  Type "dict[str, int] | Unknown | dict[Unknown, Unknown] | None" is not assignable to return type "dict[Unknown, Unknown]
  286:16  reportReturnType  Type "object" is not assignable to return type "dict[Unknown, Unknown] | None"
  304:20  reportReturnType  Type "object" is not assignable to return type "dict[Unknown, Unknown] | None"
  310:20  reportReturnType  Type "object" is not assignable to return type "dict[Unknown, Unknown] | None"
  446:16  reportReturnType  Type "object" is not assignable to return type "dict[Unknown, Unknown] | None"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\study\runner.py` (6)

  430:34  reportOperatorIssue  Operator "-" not supported for types "float" and "float | None"
  635:20  reportReturnType  Type "str" is not assignable to return type "ShutdownReason | None"
  638:57  reportOperatorIssue  Operator ">=" not supported for types "int" and "int | None"
  641:20  reportReturnType  Type "Literal['max_rounds']" is not assignable to return type "ShutdownReason | None"
  659:24  reportReturnType  Type "Literal['early_stopped']" is not assignable to return type "ShutdownReason | None"
  769:24  reportReturnType  Type "ShutdownReason" is not assignable to return type "str"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\data_source\akshare_loader.py` (5)

  112:16  reportReturnType  Type "Series | DataFrame | Unknown" is not assignable to return type "DataFrame | None"
  142:16  reportReturnType  Type "Series | DataFrame | Unknown" is not assignable to return type "DataFrame | None"
  170:16  reportReturnType  Type "Series | DataFrame | Unknown" is not assignable to return type "DataFrame | None"
  205:16  reportReturnType  Type "Series | DataFrame | Unknown" is not assignable to return type "DataFrame | None"
  234:16  reportReturnType  Type "Series | DataFrame | Unknown" is not assignable to return type "DataFrame | None"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\tools\data_transforms.py` (5)

  110:12  reportReturnType  Type "Series | DataFrame | Unknown" is not assignable to return type "DataFrame"
  197:12  reportReturnType  Type "Hashable | str" is not assignable to return type "str"
  231:12  reportReturnType  Type "Series | Unknown | Self@DataFrame" is not assignable to return type "DataFrame"
  258:12  reportReturnType  Type "Series | Unknown | Self@DataFrame" is not assignable to return type "DataFrame"
  293:12  reportReturnType  Type "Series | Unknown | Self@DataFrame" is not assignable to return type "DataFrame"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\api\session\projection_store.py` (4)

  227:53  reportOptionalIterable  Object of type "None" cannot be used as iterable value
  228:32  reportOptionalIterable  Object of type "None" cannot be used as iterable value
  257:32  reportOptionalIterable  Object of type "None" cannot be used as iterable value
  261:50  reportOptionalIterable  Object of type "None" cannot be used as iterable value

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\agent\builtin_tools\backtest_analysis_tools.py` (4)

  285:20  reportReturnType  Type "str | NDArray[Any]" is not assignable to return type "str"
  406:20  reportReturnType  Type "str | NDArray[Any]" is not assignable to return type "str"
  412:20  reportReturnType  Type "str | None" is not assignable to return type "str"
  413:24  reportOptionalSubscript  Object of type "None" is not subscriptable

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\workflow\store.py` (4)

  190:16  reportReturnType  Type "list[dict[str, Any] | None]" is not assignable to return type "list[dict[str, Any]]"
  249:16  reportReturnType  Type "list[dict[str, Any] | None]" is not assignable to return type "list[dict[str, Any]]"
  273:16  reportReturnType  Type "list[dict[str, Any] | None]" is not assignable to return type "list[dict[str, Any]]"
  327:16  reportReturnType  Type "list[dict[str, Any] | None]" is not assignable to return type "list[dict[str, Any]]"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\agent\builtin_tools\factor_tools.py` (3)

  184:10  reportOptionalSubscript  Object of type "None" is not subscriptable
  184:20  reportOptionalSubscript  Object of type "None" is not subscriptable
  1113:20  reportReturnType  Type "str | None" is not assignable to return type "str"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\agent\sandbox\protocol.py` (3)

  31:47  reportReturnType  Function with declared return type "tuple[bool, str]" must return value on all code paths
  40:47  reportReturnType  Function with declared return type "Path" must return value on all code paths
  47:46  reportReturnType  Function with declared return type "Path" must return value on all code paths

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\alpha_zoo_yaml.py` (3)

  138:16  reportReturnType  Type "str | Unknown" is not assignable to return type "DataFrame | float | int"
  151:20  reportReturnType  Type "dict[str, DataFrame]" is not assignable to return type "DataFrame | float | int"
  155:20  reportReturnType  Type "Module("pandas")" is not assignable to return type "DataFrame | float | int"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\api\session\service.py` (2)

  196:20  reportReturnType  Type "dict[str, str | int]" is not assignable to return type "dict[str, str]"
  282:16  reportReturnType  Type "dict[str, str | MessageId | AttemptId | int | None]" is not assignable to return type "dict[str, str]"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\cli\__init__.py` (2)

  197:25  reportOptionalSubscript  Object of type "None" is not subscriptable
  824:12  reportReturnType  Type "tuple[ArgumentParser, dict[str, ArgumentParser]]" is not assignable to return type "ArgumentParser"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\validation\walk_forward.py` (2)

  61:16  reportOperatorIssue  Operator "<=" not supported for types "Unknown | tuple[Unknown, ...] | MultiIndex | Self@RangeIndex | int | Index" and "
  61:29  reportOperatorIssue  Operator "<=" not supported for types "datetime" and "Unknown | tuple[Unknown, ...] | MultiIndex | Self@RangeIndex | int

### `d:\Github\quantnodes-strategy-research\src\strategy_research\cli\theme.py` (1)

  301:12  reportReturnType  Type "_GeneratorContextManager[Unknown, None, None]" is not assignable to return type "Iterator[Unknown]"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\cli\tui\widgets\transcript.py` (1)

  414:39  reportOperatorIssue  Operator "-" not supported for types "int" and "int | None"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\agent\context_injector.py` (1)

  388:12  reportReturnType  Type "list[GoalContextInjector | MemoryRetrieverInjector | TodosInjector | GoalContinuationInjector]" is not assignable 

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\config_runner.py` (1)

  172:16  reportReturnType  Type "Series | Unknown | Self@DataFrame" is not assignable to return type "DataFrame"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\data_source\fred_loader.py` (1)

  196:16  reportReturnType  Type "Series | DataFrame | Unknown" is not assignable to return type "DataFrame | None"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\portfolio\cli.py` (1)

  24:20  reportReturnType  Type "Series | Unknown | Self@DataFrame" is not assignable to return type "Series | None"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\study\bootstrap.py` (1)

  473:12  reportReturnType  Type "StudyRecord | None" is not assignable to return type "StudyRecord"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\utils\backtest_metrics.py` (1)

  248:25  reportOperatorIssue  Operator "-" not supported for types "Unknown | tuple[Unknown, ...] | MultiIndex | RangeIndex | int | Index" and "Unknow

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\utils\backtest_utils.py` (1)

  96:12  reportReturnType  Type "list[Timestamp | NaTType]" is not assignable to return type "list[Timestamp]"

### `d:\Github\quantnodes-strategy-research\src\strategy_research\core\utils\covariance.py` (1)

  62:20  reportOperatorIssue  Operator "*" not supported for types "ArrayLike" and "Literal[252]"
