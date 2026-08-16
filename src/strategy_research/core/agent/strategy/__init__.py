"""LoopStrategy subpackage (P1-1).

Re-exports the core abstractions: ``LoopContext``, ``LoopStrategy``,
``LoopConfig``, ``StrategyFactory`` / ``create_strategy`` /
``register_strategy``, ``CustomStrategy``, ``ReActStrategyFactory``,
and the 9 step Protocols from ``protocol``.

v0.1 ships the *infrastructure* (types + factory + stubs); the actual
move of AgentLoop._run_loop_core onto LoopStrategy happens in a
follow-up patch once the no-op stubs above are wired into real
behaviour one step at a time.
"""

from .factory import (
    CustomStrategy,
    ReActStrategyFactory,
    StrategyFactory,
    create_strategy,
    register_strategy,
)
from .loop_context import LoopContext
from .loop_strategy import LoopConfig, LoopStrategy
from .protocol import (
    CompactionStep,
    ContinuationStep,
    FinalizationStep,
    LLMCallStep,
    PreRunStep,
    ProgressStep,
    ResilienceStep,
    Step,
    StopStep,
    ToolExecutionStep,
)

__all__ = [
    "CompactionStep",
    "ContinuationStep",
    "CustomStrategy",
    "FinalizationStep",
    "LLMCallStep",
    "LoopConfig",
    "LoopContext",
    "LoopStrategy",
    "PreRunStep",
    "ProgressStep",
    "ReActStrategyFactory",
    "ResilienceStep",
    "Step",
    "StopStep",
    "StrategyFactory",
    "ToolExecutionStep",
    "create_strategy",
    "register_strategy",
]
