"""Scheduled Research CLI commands."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from .cron_parser import next_cron_trigger, validate_cron
from .models import JobStatus, ScheduledResearchJob
from .store import ScheduledResearchStore


def cmd_schedule_create(args) -> int:
    """Create a new scheduled research job."""
    store = ScheduledResearchStore()

    workspace = str(Path(args.workspace).resolve())
    cron_expr = args.cron or ""
    interval_ms = args.interval * 1000 if args.interval else 0

    if not cron_expr and not interval_ms:
        print("错误: 必须指定 --cron 或 --interval")
        return 1

    if cron_expr and not validate_cron(cron_expr):
        print(f"错误: 无效的 cron 表达式: {cron_expr}")
        return 1

    # Compute next_run_at
    if cron_expr:
        next_run = next_cron_trigger(cron_expr)
    else:
        next_run = time.time() + interval_ms / 1000

    job = ScheduledResearchJob(
        workspace=workspace,
        strategy_name=args.strategy,
        prompt=args.prompt or "",
        cron=cron_expr,
        interval_ms=interval_ms,
        next_run_at=next_run,
        max_rounds=args.max_rounds or 1,
    )

    store.add(job)
    print(f"✓ 已创建定时任务: {job.id}")
    print(f"  工作区: {workspace}")
    print(f"  策略: {job.strategy_name}")
    if cron_expr:
        print(f"  Cron: {cron_expr}")
    else:
        print(f"  间隔: {args.interval}s")
    print(f"  下次执行: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(next_run))}")
    if job.prompt:
        print(f"  提示: {job.prompt[:80]}")

    return 0


def cmd_schedule_list(args) -> int:
    """List scheduled research jobs."""
    store = ScheduledResearchStore()
    workspace = args.workspace if hasattr(args, "workspace") and args.workspace else None
    jobs = store.list_jobs(workspace=workspace)

    if not jobs:
        print("暂无定时任务")
        return 0

    print(f"=== 定时任务 (共 {len(jobs)} 个) ===")
    for j in jobs:
        status_icon = {
            "pending": "⏳",
            "running": "🔄",
            "completed": "✓",
            "failed": "✗",
            "cancelled": "🚫",
        }.get(j.status.value, "?")

        schedule = j.cron if j.cron else f"every {j.interval_ms // 1000}s"
        next_run = time.strftime("%m-%d %H:%M", time.localtime(j.next_run_at)) if j.next_run_at else "—"

        print(f"  {status_icon} {j.id[:16]:16s}  {schedule:20s}  next: {next_run}  {j.strategy_name}")

    return 0


def cmd_schedule_show(args) -> int:
    """Show details of a scheduled job."""
    store = ScheduledResearchStore()
    job = store.get(args.job_id)

    if not job:
        print(f"任务 '{args.job_id}' 不存在")
        return 1

    print("=== 定时任务详情 ===")
    print(f"  ID:        {job.id}")
    print(f"  工作区:    {job.workspace}")
    print(f"  策略:      {job.strategy_name}")
    print(f"  状态:      {job.status.value}")
    if job.cron:
        print(f"  Cron:      {job.cron}")
    if job.interval_ms:
        print(f"  间隔:      {job.interval_ms / 1000}s")
    if job.prompt:
        print(f"  提示:      {job.prompt}")
    print(f"  最大轮数:  {job.max_rounds}")
    print(f"  创建时间:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job.created_at))}")
    if job.last_run_at:
        print(f"  上次执行:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job.last_run_at))}")
    if job.next_run_at:
        print(f"  下次执行:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job.next_run_at))}")
    if job.last_run_id:
        print(f"  上次Run:   {job.last_run_id}")
    if job.config.get("last_error"):
        print(f"  最后错误:  {job.config['last_error'][:200]}")

    return 0


def cmd_schedule_cancel(args) -> int:
    """Cancel a scheduled job."""
    store = ScheduledResearchStore()
    job = store.get(args.job_id)

    if not job:
        print(f"任务 '{args.job_id}' 不存在")
        return 1

    job.status = JobStatus.CANCELLED
    store.update(job)
    print(f"✓ 已取消任务 {job.id}")
    return 0


def cmd_schedule_delete(args) -> int:
    """Delete a scheduled job."""
    store = ScheduledResearchStore()
    ok = store.delete(args.job_id)

    if ok:
        print(f"✓ 已删除任务 {args.job_id}")
    else:
        print(f"任务 '{args.job_id}' 不存在")
        return 1

    return 0


def cmd_schedule_run(args) -> int:
    """Immediately run a scheduled job once."""
    from .executor import ScheduledResearchExecutor

    store = ScheduledResearchStore()
    executor = ScheduledResearchExecutor(store)
    ok = executor.run_once(args.job_id)

    if ok:
        print(f"✓ 已触发任务 {args.job_id}")
    else:
        print(f"任务 '{args.job_id}' 不存在")
        return 1

    return 0


def cmd_schedule_start(args) -> int:
    """Start the scheduler."""
    from .executor import ScheduledResearchExecutor

    store = ScheduledResearchStore()
    executor = ScheduledResearchExecutor(store)

    recovered = store.recover_stale_running()
    if recovered:
        print(f"恢复了 {recovered} 个中断的任务")

    jobs = store.list_jobs(status=JobStatus.PENDING)
    print("=== 启动调度器 ===")
    print(f"  待执行任务: {len(jobs)} 个")
    print(f"  Tick 间隔:  {args.tick}s")
    print("  按 Ctrl+C 停止")
    print()

    executor.start()

    try:
        import signal
        signal.signal(signal.SIGINT, lambda *_: executor.stop())
        signal.signal(signal.SIGTERM, lambda *_: executor.stop())
        # Keep main thread alive
        while executor._running:
            time.sleep(1)
    except KeyboardInterrupt:
        executor.stop()
        print("\n调度器已停止")

    return 0


def add_schedule_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Add schedule subcommands to the main CLI parser."""
    schedule_parser = subparsers.add_parser("schedule", help="定时研究")
    schedule_sub = schedule_parser.add_subparsers(dest="schedule_command", help="定时任务命令")

    # schedule create
    create_p = schedule_sub.add_parser("create", help="创建定时任务")
    create_p.add_argument("--workspace", "-w", required=True, help="工作区路径")
    create_p.add_argument("--strategy", "-s", required=True, help="策略名称")
    create_p.add_argument("--cron", "-c", help="Cron 表达式 (5字段)")
    create_p.add_argument("--interval", "-i", type=int, help="间隔秒数")
    create_p.add_argument("--prompt", "-p", help="研究提示")
    create_p.add_argument("--max-rounds", "-m", type=int, default=1, help="每次最大轮数")

    # schedule list
    list_p = schedule_sub.add_parser("list", help="列出定时任务")
    list_p.add_argument("--workspace", "-w", help="按工作区筛选")

    # schedule show
    show_p = schedule_sub.add_parser("show", help="显示任务详情")
    show_p.add_argument("job_id", help="任务 ID")

    # schedule cancel
    cancel_p = schedule_sub.add_parser("cancel", help="取消任务")
    cancel_p.add_argument("job_id", help="任务 ID")

    # schedule delete
    delete_p = schedule_sub.add_parser("delete", help="删除任务")
    delete_p.add_argument("job_id", help="任务 ID")

    # schedule run
    run_p = schedule_sub.add_parser("run", help="立即执行一次")
    run_p.add_argument("job_id", help="任务 ID")

    # schedule start
    start_p = schedule_sub.add_parser("start", help="启动调度器")
    start_p.add_argument("--tick", type=int, default=60, help="检查间隔 (秒, 默认60)")
