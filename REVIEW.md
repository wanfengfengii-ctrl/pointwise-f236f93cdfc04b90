# 湖表在线备份恢复承诺审查报告

审查对象：`lake.py`、`backup.py`、`reclaim.py`、`lab.py`、`checks.py`、`samples.json`（冻结基线，未修改）。
审查范围：正常完成、受控复制失败、提交/回收与复制交错；不涉及任意进程崩溃恢复、增量备份或远程存储。
证据入口：`python3 checks.py verify`（原回归）、`python3 checks.py repro`（原失败，保留）、`python3 evidence_gate.py record|verify|demo`（`review_cases.json` → `review.json` 的真实重放）。

## 1. 发布意见

**结论：不通过，拒绝按当前实现发布“成功备份可完整恢复声明版本”的承诺。**

理由（均为实际重放结果，不是对建议方案的预测）：

1. 成功备份的完成标记可以与声明版本不一致：目录克隆晚于版本选择，中间的正常提交会把“更新的”目录带进备份。恢复目录的当前版本可能等于 2 或 3，而声明版本是 1；其 manifest 指向副本中没有的载荷，当前版本读取直接 `FileNotFoundError`。见 case:late-catalog-commit。
2. 同一缺陷会被载荷复用掩盖：`return` 批次让新版本的 north 重新引用初始对象，摘要与行集合都正确，只有“恢复目录当前版本 == 声明版本”这一条被破坏。仅比较行或摘要的验收会误判为通过。见 case:return-payload-masks-catalog-skew、case:double-commit-masks-version-skew。
3. 备份在“选择版本 → 复制载荷”的整个窗口内没有取得任何文件保护；回收按规划时点的当前版本计算候选，`sweep` 只判断文件是否存在，不重验活跃性，也不咨询备份。正常提交 + 正常回收即可让备份失败（case:reclaim-during-copy-no-protection），或让旧候选在再次被引用后被删除（case:stale-candidate-revived-before-copy、case:stale-candidate-revived-after-copy、case:stale-candidate-swept-before-backup）。
4. 受控复制失败确实不产生完成标记、`restore` 也确实拒绝（`FileNotFoundError`），但目标目录会留下 `catalog.db`、已复制的部分载荷和 `events.json`，且该目标路径已被占用（`exist_ok=False`）；当前代码没有任何残留清理或释放动作，责任未定义。见 case:failure-at-first-copy、case:failure-at-second-copy。
5. `restore` 只校验回执列出的文件摘要，不校验“回执版本 == 恢复目录 head”，也不校验回执文件集合等于该版本 manifest；因此第 1 条的错版目录会被当作正常恢复结果返回（`Lake` 对象构造成功），问题推迟到使用方读取当前版本时才暴露。

原失败未被掩饰：`python3 checks.py repro` 仍然输出 `BACKUP_RESTORE_MISMATCH` 并退出 1；`review.json` 中同时保留成功但不一致的证据（case:late-catalog-commit、case:return-payload-masks-catalog-skew 等）和失败证据。

## 2. 基线与可观察事实

冻结批次（`samples.json`）：

| 批次 | 版本 | 行 | 对象（文件名 = sha256） |
| --- | --- | --- | --- |
| initial | 1 | `[north,n1,10] [north,n2,0] [south,s1,20]` | `2c32600e…(north)`、`2b0e713c…(south)` |
| change | 2 | `[north,n3,30] [south,s1,20]` | `6731eb0f…(north)`、`2b0e713c…(south)` |
| return | 3 | 同 initial | north 重新引用 `2c32600e…`（`lake.py:77-81` 按摘要命名并复用） |

下文用 `N0`=2c32600e…（north 初始/回归载荷），`N1`=6731eb0f…（north 变更载荷），`S`=2b0e713c…（south，三版本共用）。`review.json` 中记录完整文件名的真实摘要。

独立期望的来源：`lab.py:14-31` 只用输入批次做覆盖语义计算（空列表删除分区、未提及分区沿用），写入 `expected_versions`，**不调用 `Lake.read`**。因此下文的“独立期望”与实现读取路径独立。

备份事件顺序（`backup.py:25-43`，`lab.py:59-64` 在事件后同步触发检查点）：
`backup.selected(v)` → `backup.catalog` → 每个去重文件名排序后的 `backup.before_file:N` / `backup.after_file:N` → `backup.complete(v)`。
`backup.selected` 发生在目录克隆之前，这正是可被检查点插入提交的位置。

## 3. 缺陷清单（现状）

### F1 版本选择与目录克隆是两个独立读取（单因，最高优先级）

- `backup.py:20-24`：在 `BEGIN` 中读取 `head` 版本与该版本 manifest，然后立刻 `c.commit()`。
- `backup.py:25`：`note("backup.selected")` —— 检查点在此同步执行，此时保护已结束。
- `backup.py:26`：`clone_catalog(...)` 重新打开 `catalog.db` 复制；`lake.py:105-112`（`src.backup(dst)`，`lake.py:109`）只保证**复制那一刻**目录内部一致，不保证等于第 22 行选中的版本。
- 结果：回执 `complete.json` 写的是 `version`（`backup.py:41-42`），目录副本却可能是更晚的版本；`restore`（`backup.py:52-63`）不做版本一致性检查，直接 `return Lake(destination)`（`backup.py:63`）。

触发时序（case:late-catalog-commit）：
`commit(initial)=v1` → `backup.selected(v1)` → `commit(change)=v2` → `backup.catalog` → 复制 `S`、`N0` → `backup.complete(v1)`。
独立期望（v1）：`[[north,n1,10],[north,n2,0],[south,s1,20]]`。
实际：`complete=true`，`declared_version=1`，`catalog_version=2`，`actual_rows=null`，`restore_error=FileNotFoundError`。
说明：v1 的两个对象确实被完整复制且摘要正确，但恢复目录当前版本为 2，manifest 指向未复制的 `N1`，当前版本不可读。**摘要正确不能替代恢复承诺。**

### F2 备份没有取得/移交/释放任何文件保护（单因）

- 选择窗口只在目录库内成立（`backup.py:20-24`），复制阶段（`backup.py:31-40`，`read_bytes` 在 `backup.py:35`）不持有目录写事务——这一点符合契约“复制不得跨载荷 I/O 持目录写事务”，但同时也没有任何等价保护。
- `reclaim.plan` 只按当前 `head` 计算活跃集（`reclaim.py:4-6`），候选产生后允许提交与备份交错（契约第 11 行）。
- 因此“正常提交 + 正常 `collect`”就能删掉备份已选中版本的载荷。case:reclaim-during-copy-no-protection：
  `backup.selected(v1)` → `commit(change)=v2` → `backup.catalog` → 复制 `S` → `gc.collect` 删除 `N0`（在 v2 下确实已无引用，回收行为本身符合 `reclaim.py:6` 的定义）→ 读 `N0` 时 `FileNotFoundError`。
  实际：`complete=false`，`backup_error=FileNotFoundError`，`restore_error=FileNotFoundError`，`actual_rows=null`。
  这是 F2 单因：回收没有违约，是备份没有保护。

### F3 旧候选不复核，`sweep` 会删除已被再次引用的活跃对象（单因）

- `reclaim.py:11-21`：`sweep` 对规划时得到的名单逐个 `BEGIN IMMEDIATE`（`reclaim.py:15`），只做 `path.exists()`（`reclaim.py:17`）再 `unlink()`（`reclaim.py:18`）；从不重新读取 `head`/manifest，也不咨询任何保护。
- `lake.py:77-81`：对象按载荷摘要命名，`if not path.exists()` 复用；同一对象可跨版本、跨分区再次被引用。因此“规划时死、执行时活”是常规场景，不是异常。
- 契约要求“旧候选再次被引用也须正确裁决”。当前实现没有裁决，直接删除。

三个触发位置（同一根因）：

| 用例 | 触发顺序 | 独立期望 | 实际 |
| --- | --- | --- | --- |
| case:stale-candidate-revived-before-copy | `plan` 在 v2 得到 `{N0}` → `commit(return)=v3` 重新引用 `N0` → `backup.selected(v3)` → 复制 `S` → `before_file:1` 时 `sweep(N0)` | v3 的完整行 = initial 行 | `complete=false`，`backup_error=FileNotFoundError`，`restore_error=FileNotFoundError`，`actual_rows=null` |
| case:stale-candidate-revived-after-copy | 同上，但 `sweep` 放在 `after_file:1`（`N0` 已复制完） | initial 行 | 备份成功、行正确、`catalog_version=3=declared`；但 `gc.sweep removed={N0}`，而 `backup.selected objects` 含 `N0`，即**活跃对象被删**，源表当前版本随后不可读（补充直测见 §6） |
| case:stale-candidate-swept-before-backup | `commit(change)=v2` → `plan={N0}` → `commit(return)=v3` → `sweep` → 才开始备份 | initial 行 | `complete=false`，`backup_error=FileNotFoundError`，源表在备份前已损坏 |

### F4 完成标记与恢复缺少版本/集合一致性校验（单因，放大 F1）

- `backup.py:41-43`：`complete.json` 只写 `{version, files}`，不含目录应处的 `head` 快照。
- `backup.py:52-63`：`restore` 只检查 `complete.json` 存在、逐个回执文件摘要匹配；`clone_catalog`（`backup.py:57`）把目录原样带入，`return Lake(destination)`（`backup.py:63`）返回一个 head 可能不等于 `receipt["version"]` 的湖。
- 因此 case:late-catalog-commit 的恢复在 API 层“成功返回”，错误被推迟到 `Lake.read()`；case:return-payload-masks-catalog-skew 更隐蔽：目录是 v2、回执是 v1，而 v2 的 manifest 恰好只引用 `N0`/`S`，两文件都在回执里，`read()` 返回的行与 v1 完全相同——**行集合正确也不是恢复承诺的充分条件**，当前版本必须等于声明版本。

### F5 受控失败残留与重试路径（现状，责任未定义）

- `backup.py:8-10`：`destination.mkdir(..., exist_ok=False)`，失败后同名路径不能复用，重试必须先改目的地。
- `fail_at` 在复制前抛 `OSError`（`backup.py:33-34`）；`finally` 仍写 `events.json`（`backup.py:48-49`），失败事件进 `backup_events`（`backup.py:45-46`）。
- 实测残留（case:failure-at-first-copy）：`catalog.db`、空 `data/`、`events.json`；case:failure-at-second-copy 另有一个已复制载荷 `S`。两者都无 `complete.json`，`restore` 在读取完成标记时拒绝（`backup.py:54`，`FileNotFoundError`），不会创建目标目录。
- 当前没有删除残留的代码；残留目录甚至能被 `Lake(...)` 当作正常仓库打开并声称版本 1（补充直测），但读取失败。清理责任必须在文档中落到人：备份任务负责释放保护并留下可判定的失败现场，维护负责人负责删除失败目的地；`restore` 负责拒绝，不负责清场。

### F6 `fail_at` 的边界语义（契约边界，非缺陷）

`fail_at` 是去重排序后复制序号，`backup.py:29-34`：序号等于文件数时不触发任何失败。case:fail-index-past-end（`fail_at=2`，两文件）实际为完整成功、行正确。文档必须写明该边界，避免值班把它当作“注入未生效”的故障。

### 已排除的修复路径（契约明确禁止，不作为建议）

- `Lake.read` 在目录写事务内做载荷 I/O（`lake.py:92-101`，`BEGIN IMMEDIATE` 在 `lake.py:92`、`c.commit()` 在 `lake.py:101`）：契约明确“前台 read 的现有长事务不能直接用作备份的保护方案”，且会违反“复制不得跨载荷 I/O 持目录写事务”。
- 暂停提交或回收以规避交错：契约禁止。
- 只比较摘要或文件存在性：被 case:return-payload-masks-catalog-skew 证伪。

## 4. 用例裁决（实际重放，见 review.json）

### 4.1 基线与合法交错（5）

| 用例 | 触发顺序 | 独立期望 | 实际 | 裁决 |
| --- | --- | --- | --- | --- |
| case:baseline-normal | `commit(initial)=v1` → 备份全程无交错 | initial 行 | `complete=true`，`declared=catalog=1`，行相同 | 合法基线 |
| case:commit-at-catalog-note | `backup.selected(v1)` → `backup.catalog` **之后** `commit(change)=v2` | initial 行 | `catalog=1`，行相同 | 合法：目录已克隆，载荷未变 |
| case:commit-after-last-file | 最后一次 `after_file:1` 之后 `commit(change)=v2` | initial 行 | `catalog=1`，行相同 | 合法：复制已完成 |
| case:dead-candidate-sweep-during-copy | `commit(change)=v2` → `plan={N0}`（v2 下确为死对象）→ 复制 `S` 时 `sweep(N0)` | v2 行 | `complete=true`，`catalog=2`，行相同；`removed={N0}` | 合法：候选确实不活跃 |
| case:fresh-collect-after-rereference | `commit(change)=v2` → `commit(return)=v3` → `collect`（重新 plan） | v3 行 | `complete=true`；`removed={N1}`，`N0` 被保留 | 合法：plan 与 sweep 同批、活跃集是当时 head，证明问题在“旧候选不复核”而非“再引用本身” |

### 4.2 单因失败（5）

| 用例 | 触发顺序 | 独立期望 | 实际 |
| --- | --- | --- | --- |
| case:late-catalog-commit | `selected(v1)` → `commit(change)=v2` → `catalog` → 复制 → `complete(v1)` | initial 行，当前版本 1 | `complete=true`，`declared=1`，`catalog=2`，`actual=null`，`restore_error=FileNotFoundError` |
| case:reclaim-during-copy-no-protection | `selected(v1)` → `commit(change)=v2` → `catalog` → 复制 `S` → `collect` 删 `N0` | initial 行 | `complete=false`，`FileNotFoundError`，`actual=null` |
| case:stale-candidate-revived-before-copy | v2 时 `plan={N0}` → `commit(return)=v3` → `selected(v3)` → 复制 `S` → `before_file:1` 时 `sweep(N0)` | initial 行 | `complete=false`，`FileNotFoundError` |
| case:stale-candidate-revived-after-copy | 同上，`sweep` 放到 `after_file:1` | initial 行 | `complete=true`，`catalog=3`，行相同；但删除的是 v3 活跃对象 `N0` |
| case:stale-candidate-swept-before-backup | v2 时 `plan={N0}` → `commit(return)=v3` → `sweep(N0)` → 开始备份 | initial 行 | `complete=false`，`FileNotFoundError`；源表在备份开始前已被破坏 |

### 4.3 组合与掩盖（4）

| 用例 | 构造 | 独立期望 | 实际 | 裁决 |
| --- | --- | --- | --- | --- |
| case:return-payload-masks-catalog-skew | F1：`selected(v1)` 后 `commit(return)=v2` | initial 行，当前版本 1 | `complete=true`，`catalog=2`，`actual` 行与期望**相同** | 行/摘要全部正确，仍失败于 `catalog_version != declared_version`；单看行会漏判 |
| case:double-commit-masks-version-skew | F1：`selected(v1)` 后连续 `commit(change)`、`commit(return)` | initial 行，当前版本 1 | `complete=true`，`catalog=3`，行相同 | 版本差被放大到 2，行仍掩盖 |
| case:skew-and-dead-sweep-combined | F1 + 合法 `sweep`：`selected(v2)` 后 `commit(change)=v3`，同时回收真死对象 `N0` | v2 行，当前版本 2 | `complete=true`，`catalog=3`，行相同 | 合法回收不掩盖也不修复 F1；版本偏差仍在 |
| case:skew-and-stale-sweep-combined | F1 + F3：v2 时 `plan={N0}`、`selected(v3)` 后 `commit(change)=v4`，`before_file:1` 时 `sweep(N0)` | v3（initial）行，当前版本 3 | `complete=false`，`backup_error=FileNotFoundError` | 先发生的复制中断使 F1 的错版结果不可观察；组合导致“掩盖”，不能据此认为 F1 已消失 |

### 4.4 受控复制失败与边界（3）

| 用例 | 触发顺序 | 独立期望 | 实际 |
| --- | --- | --- | --- |
| case:failure-at-first-copy | `selected(v1)` → `catalog` → `before_file:0` → 抛 `OSError` | initial 行；无完成标记；拒绝恢复 | `complete=false`，`backup_error=OSError`，`restore_error=FileNotFoundError`；残留 `catalog.db`、空 `data/`、`events.json` |
| case:failure-at-second-copy | 复制 `S` 成功后 `before_file:1` 抛 `OSError` | 同上 | `complete=false`，`OSError`，`FileNotFoundError`；残留 `catalog.db`、`S`、`events.json` |
| case:fail-index-past-end | `fail_at=2`，去重后仅 2 个文件 | initial 行，正常成功 | `complete=true`，行相同，无 `backup.failed` |

失败现场判定：`backup.failed` 事件在 `backup_events` 末尾（`backup.py:45-46`），但 `events.json` 由 `finally` 写出（`backup.py:49`），因此失败目录总带事件日志，且不带 `complete.json`。`restore` 对无标记目录在内层抛 `FileNotFoundError`（`backup.py:54`），且因异常发生在 `destination.mkdir`（`backup.py:55`）之前，不会留下恢复目录。

## 5. 最小兼容修复建议（未实现，未运行；以下为设计预测，不是当前输出）

目标：不改变 `Lake`/`backup`/`reclaim` 的公开签名与既有断言，不引入后台线程、远程存储或增量格式，不在复制阶段持有目录写事务。

1. **保护记录（取得/移交/释放）**：在 `catalog.db` 增加 `pins(owner TEXT PRIMARY KEY, version INTEGER)`（纯新增表，旧代码可忽略）。`backup` 在 `backup.py:20-24` 的同一个短事务里完成“读 head 版本 + 读 manifest + 写入 `(run_id, version)`”，事务提交即“取得”；复制阶段不持事务。写完 `complete.json`（`backup.py:42`）后再用另一个短事务删除该 pin（“释放”）；`finally` 中无条件再次短事务删除（幂等），保证受控失败也释放。`pin` 的“移交”定义为：回执中的 `version` 与 `files` 自此刻起是该备份自身的保护依据，源侧 pin 释放后不再依赖源目录。
2. **版本与目录同源**：在取得 pin 的同一事务里生成“选择结果”（版本、manifest 行、去重文件名、摘要）。目录克隆要么仍用 `clone_catalog` 但随后**校验副本 head 等于选中版本**，要么把回执写进克隆后的目录并在 `restore` 侧强校验。最小改动是 `restore` 增加：`receipt["version"] == 恢复目录 head`，且 `receipt["files"]` 的对象集合 == 该版本 manifest 的对象集合。
3. **回收裁决顺序**（`reclaim.py:1-8`、`11-21`）：`plan` 的活跃集改为“当前 head 的对象 ∪ 所有 `pins.version` 的对象”；`sweep` 在每个 `BEGIN IMMEDIATE` 内、`unlink` 之前重新读取 head 与 pins，若候选已活跃或已受保护则跳过（不删除、不计入 `removed`），再执行删除。这样旧候选每次执行都重新裁决，且不依赖候选名单的新鲜度。
4. **恢复拒绝责任**：`restore` 在 `destination.mkdir` 之前完成全部判定：完成标记存在、回执版本等于目录 head、文件集合等于该版本 manifest、逐文件摘要匹配；任一失败抛错且不创建目标目录。恢复后应能用默认 `read()` 得到声明版本的完整行。
5. **失败残留**：保持 `exist_ok=False` 与“无完成标记即拒绝恢复”的现状；文档明确失败目的地由维护负责人删除，备份只写 `events.json` 作为判定依据，不自动删除现场（避免与证据留存冲突）。
6. **`fail_at` 边界**：保持现有语义（等于文件数即不注入），仅在文档中说明。

`Lake.read` 的长事务（`lake.py:92-101`）维持原样：它是前台读的既有行为，不能改造成备份保护，也不应让备份在复制期间借用它。

## 6. 支撑性直测（不属于 review.json，用于确认 F3 的后果）

在临时目录中执行 `commit(initial); commit(change); plan; commit(return); sweep(旧候选)` 后：

- `lake.read()` → `FileNotFoundError`（当前版本 v3 的 north 对象已被删除）；
- 随后 `backup.backup(...)` 同样在读取该对象时 `FileNotFoundError`。

这与 case:stale-candidate-revived-after-copy 的事件证据一致：该用例的 `backup.selected` 对象列表含 `N0`，而 `gc.sweep` 的 `removed` 也含 `N0`，证明被删对象在 v3 仍被引用。该直测只说明后果，不替代 `lab` 的独立期望与真实恢复行。

## 7. 责任划分（与修订后的 docs/backup.md 一致）

| 角色 | 责任 |
| --- | --- |
| 备份任务 | 在短事务内取得 pin（版本 + manifest）；复制期间不持目录写事务；完成后写回执并释放 pin；失败时在 `finally` 释放 pin，留下无完成标记的现场与 `events.json`；不得以暂停提交/回收实现一致性 |
| 回收者 | `plan` 的活跃集含 pins；`sweep` 在事务内按“当前 head + pins”重新裁决每个旧候选，跳过已活跃/受保护项；只删除裁决为死且无保护的文件 |
| 恢复方 | 无完成标记、版本不符、集合不符或摘要不符时拒绝恢复；拒绝时不创建目标目录；不得返回 head 与声明版本不同的湖 |
| 维护负责人 | 核准发布前确认 §5 的 1–4 已落地并有重放证据；删除失败备份目的地与失败恢复目录；保管 `events.json` 作为失败判定材料 |

## 8. 复现与证据对应

- 原回归：`python3 checks.py verify` → `{"normal_backup": [...], "copy_failure": {"complete": false, "error": "OSError"}}`（通过，保留原断言）。
- 原失败：`python3 checks.py repro` → `BACKUP_RESTORE_MISMATCH`，退出 1（保留，不修）。
- 用例重放：`python3 evidence_gate.py record` 生成 `review.json`；`python3 evidence_gate.py verify` 重跑原回归、核对 17 个用例的源码定位与报告引用，并确认 `review.json` 与新鲜重放逐项相等；`python3 evidence_gate.py demo` 打印同一份真实重放内容。
- `review.json` 为 `[{id, result}]` 的数组，`result` 含 `selected_version`、`declared_version`、`catalog_version`、`expected_rows`、`actual_rows`、`complete`、`backup_error`、`restore_error`、`events`、`backup_events`；报告中以 `case:` 前缀引用的每个条目均在 `review_cases.json` 中有唯一条目，且 `evidence_gate.py verify` 会逐条核对定位原文与引用存在性。
