# S2b 实施细节 — 路由翻转（per-session 文件 + 惰性迁移 + 旧库只读）— 2026-10-27

> 前置：S2a 已完成（`be26ae15`：`_get_db()` = 全局入口、`_session_db(sid)` = 会话入口，
> 当前同指旧库）。本文件定 S2b 的实现细节、迁移协议、以及三个必须先解决的决策。

---

## 1. 现状事实（只读实测，2026-10-27）

| 项 | 值 |
|---|---|
| 旧库 | `<workspace>/.quantnodes_strategy_research_session.db`，5.4MB + 4.3MB WAL |
| `user_version` | **6**（既有迁移链，S2b 必须接在它之后） |
| 会话数 | 83 |
| 行数 | messages 190 / message_parts 299 / attempts 100 / **event_log 6036** / snapshots 1 / blob_refs 54 |
| **孤立数据** | **0**（messages/attempts/event_log 全部有对应 sessions 行） |
| 单会话最大 | 4 消息 / 2 attempts / **2407 事件**（其余多在 200 以下） |
| FTS 消费者 | **仅 `POST /api/chat/sessions/search`**；前端**未接线**（`webui/frontend/src` 无调用） |
| sessions 表 | 83 行，WebUI 会话列表/鉴权/用户枚举的唯一来源（跨会话实体） |

**关键判断**：数据量小（合计 6600 行）、零孤立、单会话有界 → **惰性迁移是安全且快的**
（最坏单会话 2407 行，毫秒级）。这消除了 S2b 最大的性能顾虑。

## 2. 目标布局

```
<workspace>/data/
├── platform.sqlite                  # 全局：sessions（实体登记）+ schema_migrations
│                                   #   + migrated_sessions（台账）+ search_index（见 §4）
└── sessions/<session_id>.sqlite     # 每会话：messages/message_parts/attempts/
                                    #   event_log/snapshots/blob_refs + messages_fts + 触发器
旧 .session.db                       # 只读源：迁移数据来源，此后不再写
```

**归属依据**（P-2 §10.5）：sessions 跨会话 → 全局；其余带 session_id → per-session。

## 3. 路由组件（新文件 `core/storage/session_files.py`）

```python
class SessionFileRouter:
    def __init__(self, workspace: Path, *, legacy_path: Path | None = None)
    def global_conn(self) -> sqlite3.Connection        # platform.sqlite（含 sessions 镜像）
    def session_conn(self, session_id: str) -> sqlite3.Connection
    def ensure_migrated(self, session_id: str) -> None # 懒迁移入口
    def is_migrated(self, session_id: str) -> bool
```

- **线程本地连接缓存**（沿用 `_get_db()` 现有机制，键 = 文件绝对路径）；
- **DDL 复用**：会话文件用 `web_session_db` 现有 `_ensure_schema`（含 FTS 五件套 +
  3 触发器，**顺序敏感块原样调用**，不重写）；
- **全局库**：只建 `sessions` + `schema_migrations` + `migrated_sessions` + `search_index`。

## 4. 决策一：FTS 跨会话搜索 —— **定案：遍历会话文件（B）**

> 完整调研见 `cross-session-search-research-20261027.md`（六方案对比 + 业界调研）。

**结论**：`search_sessions` 改为 fan-out 遍历 per-session 文件，各自 FTS 查询后合并。
不建索引、不做同步。

| 为什么不是别的 | 理由 |
|---|---|
| ~~A 全局 search_index~~ | 需要「写消息 → 同步索引」的跨库同步；实测 SQLite **禁止触发器跨库写**（`qualified table names are not allowed ... within triggers`），只能应用层同步；且 S3 写入归一后同步点要挪 |
| ~~C 删端点~~ | 功能回退、破契约 |
| ~~D 放弃分库~~ | 架构倒退（违背 P-2 自包含定案） |
| ~~E hash 引用索引~~ | 当前消息内容仅 28KB，hash 间接层不值得 |
| ~~F emit 单点索引~~ | 方向对（与 S3 同源），但 `EventStore.emit` 是最热路径，必须异步化才能加索引写 → 复杂度回升；列为**升级目标** |

**为什么 B 可行（实测支撑）**：
- 当前 83 会话 / 190 消息 → 全量扫描几十毫秒；
- 三条约束把线性退化变成有界延迟：
  1. 会话按 `updated_at` 倒序，凑够 `limit` 即停；
  2. 单文件查询加 `LIMIT`；
  3. 最多扫 200 个会话（`SR_SEARCH_MAX_SESSIONS`），响应带 `scanned_sessions` / `truncated`。

**业界对照**：SQLite 官方推荐「每租户一文件」并明确跨文件搜索要靠应用层 fan-out；
Claude Code（每会话一 .jsonl）**不做跨会话全文搜索**；OpenCode / LangGraph 保持单库。
→ 我们的 B 已是业界最完整的做法。

**升级触发器**（任一命中则切 F：emit 单点索引 + 异步队列）：
实测 P95 > 500ms / 会话数 > 500 / 出现「搜全部历史」的真实需求。

### 4.1 B 的实现要点

1. 从全局库取用户会话（`updated_at DESC`、`archived = 0`）；
2. 逐个 `_session_db(sid)` 查 `messages_fts MATCH`（带 LIMIT）；
3. 合并按 rank 排序、截断 limit；
4. 单会话文件损坏/缺失 → 跳过并计数（不整体失败）；
5. 返回既有字段 + `scanned_sessions` / `truncated`。

## 5. 决策二：sessions 表的归属与双写

S2b 后 `sessions` 在 `platform.sqlite`，但**旧表里也有它**（83 行，含 message_count /
starred / tags / archived）。

- **一次性搬迁**：S2b 首次启动时把旧 `sessions` 全量复制到 `platform.sqlite`（83 行，
  微秒级），记 `schema_migrations('sessions', 1)`；
- **此后唯一写入点是全局库**，旧库的 sessions 表不再读；
- **message_count 漂移**：S3（消息写入归一）之后由 projector 统一维护；S2b 阶段
  仍在 `persist_message` 里更新全局库的 sessions 计数（S2a 已拆成两次 commit，天然支持）。

## 6. 决策三：迁移协议（幂等 + 可校验 + 可回退）

```
ensure_migrated(sid):
  if is_migrated(sid): return                     # 台账命中，零成本
  if not legacy.exists(): mark_migrated(sid, "empty"); return
  src = legacy(ro)
  if not session_exists_in_legacy(sid): mark_migrated(sid, "absent"); return
  dst = open_session_file(sid)                    # 建 DDL + FTS
  BEGIN IMMEDIATE
    for table in (messages, message_parts, attempts, event_log, snapshots, blob_refs):
        INSERT INTO dst.<table> SELECT * FROM src.<table> WHERE <sid 列> = ?
    verify: 逐表 COUNT 相等（不等 → ROLLBACK + raise）
    record in platform.migrated_sessions(sid, row_counts_json, migrated_at)
  COMMIT
```

- **幂等**：台账是唯一判据；迁移事务内校验失败即回滚，台账不写，下次重试；
- **可回退**：删 `data/` 目录 + 台账，重启后从旧库重新迁移（旧库始终只读、内容不变）；
- **旧库只读**：`legacy_conn()` 用 `file:...?mode=ro` 打开，物理上防误写。

## 7. 接线改动（在 S2a 的两入口内翻转）

| 文件 | 改动 |
|---|---|
| `web_session_db._get_db()` | → `router.global_conn()` |
| `web_session_db._session_db(sid)` | → `router.session_conn(sid)`（内部触发 `ensure_migrated`） |
| `web_session_db._get_db_path()` | → 保留，语义降级为「全局库路径」（测试大量 patch 它） |
| `EventStore`（S1 门面） | PlatformStore 从 `db_path=legacy` 切 `data_dir=data/`；`_db_path` 用于 messages 的部分不动（EventStore 只碰 event_log） |
| `projection_store` / `Projector` | `db_path` 改传会话文件路径（S2a 已让 projector 零改动，只换构造参数） |
| `search_sessions` | 改查 `platform.search_index`（方案 A） |

## 8. 验收

1. **迁移正确性**：83 会话逐会话行数对账（messages/parts/attempts/event_log/snapshots/blob_refs 六表 COUNT 全等）；
2. **幂等**：重复 `ensure_migrated` 不重复插入、不报错；
3. **隔离性**：两个会话各写消息互不可见（不同文件）；
4. **旧库只读**：迁移后旧库文件 mtime 不变（可用 stat 断言）；
5. **搜索**：`/search` 在 index 缺失/存在两态下返回正确（方案 A 落地后）；
6. **回归**：webui / sse / message_parts / event_log_schema / projector 全家绿；
7. **回退演练**：删 `data/` → 重启 → 数据从旧库恢复。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 迁移事务与正在运行的 WebUI 写入竞争 | 迁移在 `BEGIN IMMEDIATE` + 台账双保险下进行；旧库只读，写入目标只有新文件 |
| 迁移后 `_get_db()` 语义变化影响 29 个调用点 | S2a 已分类；S2b 只换两个函数内部实现，调用点零改动 |
| FTS 触发器重建顺序 | 复用 `web_session_db._ensure_schema` 原样调用（不重写 DDL） |
| 测试大量 patch `_get_db_path` | 保留该函数，语义改为「全局库路径」；涉及会话文件的测试改用 `_session_db` |
| 旧库遗留 `-wal`/`-shm` 未 checkpoint | 迁移前对旧库执行 `PRAGMA wal_checkpoint(TRUNCATE)`（只读连接下改为一过性读写） |

## 10. 决策状态（2026-10-27 已定案）

| # | 决策 | 状态 |
|---|---|---|
| 1 | **FTS = 遍历会话文件（B）+ 三条约束** | ✅ 定案（调研见 `cross-session-search-research-20261027.md`） |
| 2 | **sessions + blob_refs 一次性搬全局库，全局唯一写入** | ✅ 定案（`blob_refs` 无 session 列，属跨会话共享资源，与 sessions 同归全局） |
| 3 | **旧库只读保留、永不删除**（回退依赖） | ✅ 定案 |
| 4 | 迁移协议：台账 + 事务内逐表 COUNT 校验 | ✅ 定案 |
