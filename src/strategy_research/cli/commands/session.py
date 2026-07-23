"""Extracted from cli.py — session management commands.

Contains:
- cmd_session_stats
- cmd_session_list
- cmd_session_show
- cmd_session_search
- cmd_session_delete
"""

from __future__ import annotations


def cmd_session_stats(args) -> int:
    """查看写入统计。"""
    from strategy_research.core.session import SessionDB

    db = SessionDB()
    stats = db.metrics_logger.get_stats()

    if not stats or stats.get("total_writes", 0) == 0:
        print("暂无写入记录")
        return 0

    print("=== Session 写入统计 ===")
    print(f"  总写入次数: {stats.get('total_writes', 0)}")
    print(f"  总消息数:   {stats.get('total_messages', 0)}")
    print(f"  成功率:     {stats.get('success_rate', 0):.1%}")
    print(f"  平均速率:   {stats.get('avg_rate', 0):.0f} 条/秒")
    print(f"  最大速率:   {stats.get('max_rate', 0):.0f} 条/秒")
    print(f"  最小速率:   {stats.get('min_rate', 0):.0f} 条/秒")
    print(f"  平均耗时:   {stats.get('avg_duration', 0):.3f}s")
    print(f"  总耗时:     {stats.get('total_duration', 0):.3f}s")

    # 显示最近记录
    recent = db.metrics_logger.get_recent(n=args.recent)
    if recent:
        print(f"\n=== 最近 {len(recent)} 条记录 ===")
        for r in reversed(recent):
            status = "✓" if r["ok"] else "✗"
            print(f"  {status} {r['count']} 条, {r['duration']:.3f}s, {r['rate']:.0f} 条/秒")

    return 0


def cmd_session_list(args) -> int:
    """列出会话。"""
    from strategy_research.core.session import SessionDB

    db = SessionDB()
    sessions = db.list_sessions(limit=args.limit)

    if not sessions:
        print("暂无会话")
        return 0

    print(f"=== 会话列表 (共 {len(sessions)} 个) ===")
    for s in sessions:
        from datetime import datetime
        created = datetime.fromtimestamp(s.created_at).strftime("%Y-%m-%d %H:%M")
        print(f"  {s.id[:16]:16s}  {created}  {s.workspace or '(global)'}")

    return 0


def cmd_session_show(args) -> int:
    """显示会话详情。"""
    from strategy_research.core.session import SessionDB

    db = SessionDB()
    session = db.get_session(args.session_id)
    if not session:
        print(f"会话 {args.session_id} 不存在")
        return 1

    from datetime import datetime
    created = datetime.fromtimestamp(session.created_at).strftime("%Y-%m-%d %H:%M:%S")
    updated = datetime.fromtimestamp(session.updated_at).strftime("%Y-%m-%d %H:%M:%S")

    print("=== 会话详情 ===")
    print(f"  ID:        {session.id}")
    print(f"  创建时间:  {created}")
    print(f"  更新时间:  {updated}")
    print(f"  工作区:    {session.workspace or '(global)'}")
    if session.metadata_json:
        print(f"  元数据:    {session.metadata_json[:200]}")

    messages = db.get_messages(args.session_id, limit=50)
    if messages:
        print(f"\n=== 消息 (共 {len(messages)} 条) ===")
        for msg in messages:
            ts = datetime.fromtimestamp(msg.timestamp).strftime("%H:%M:%S")
            content = (msg.content or "")[:120]
            print(f"  [{ts}] {msg.role}: {content}")
    else:
        print("\n(无消息)")

    return 0


def cmd_session_search(args) -> int:
    """搜索消息。"""
    from strategy_research.core.session import SessionDB

    db = SessionDB()
    results = db.search_messages(args.query, limit=args.limit)

    if not results:
        print(f"未找到匹配 '{args.query}' 的消息")
        return 0

    print(f"=== 搜索结果 (共 {len(results)} 条, 关键词: {args.query}) ===")
    for r in results:
        session_id = r.get("session_id", "?")[:12]
        role = r.get("role", "?")
        content = (r.get("content", "") or "")[:120]
        print(f"  [{session_id}] {role}: {content}")

    return 0


def cmd_session_delete(args) -> int:
    """删除会话。"""
    from strategy_research.core.session import SessionDB

    db = SessionDB()
    session = db.get_session(args.session_id)
    if not session:
        print(f"会话 {args.session_id} 不存在")
        return 1

    ok = db.delete_session(args.session_id)
    if ok:
        print(f"已删除会话 {args.session_id}")
    else:
        print(f"删除会话 {args.session_id} 失败")
        return 1

    return 0
