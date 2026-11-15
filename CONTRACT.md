# 湖表在线备份审查工作区

本仓库是有限分区湖表的维护基线。只用 Python 标准库和 SQLite，所有对象位于本地目录；一个目录库、一个写入者、一个回收者和一个备份任务。没有远程存储、增量备份或任意进程崩溃恢复要求。

## 业务接口和材料

`Lake(root)` 创建或打开目录。目录含 catalog.db 和 data/。samples.json 是冻结批次：initial 创建 north(n1=10,n2=0)、south(s1=20)，change 将 north 完整替换为 n3=30，return 将 north 恢复为初始载荷。批次是分区名到 [字符串ID,整数值] 列表的映射，空列表删除分区；分区名为合法 Python 标识符，整数不含布尔值。行按列表写入，重复行保留。

`Lake.commit(replacements)` 原子发布完整新版本并返回从1递增的版本号，未提及分区沿用；文件按载荷摘要复用，不因再次引用而换名。`Lake.version()` 返回当前版本。`Lake.read(version=None)` 返回按 [分区,ID,值] 排序的完整行列表，默认读当前版本。版本目录保留，历史读只在所需文件尚未回收时保证可用；未知版本抛 ValueError，缺文件抛 FileNotFoundError。

`reclaim.plan(lake)` 返回候选文件名列表；`reclaim.sweep(lake,candidates)` 执行删除并返回实际删除文件名；`collect(lake)` 连续执行两者。候选产生后允许提交与备份交错。对象不可在接口外篡改。

`backup.backup(lake,destination,hook=None,fail_at=None)` 选择调用期间的当前版本，返回 {version,files}。destination 必须尚不存在；files 是 {object,digest} 列表。对象复制后逐一核验摘要，完成时写 complete.json，执行事件写 events.json。`fail_at` 为从0开始的复制序号，在对应文件复制前抛 OSError；这是受控复制失败，不是进程崩溃。失败不应出现可接受的完成标记。`backup.restore(source,destination)` 仅接受有完成标记的备份，返回恢复后的 Lake。

成功备份必须恢复出声明版本的完整行集合，恢复目录的当前版本也须等于声明版本。文件摘要正确不能替代这个承诺。备份选定可读版本后，正常提交和回收不得令该次备份失败或串版；不能以暂停提交或回收规避，源表继续前进不应改变已完成副本。备份的数据复制不得跨载荷 I/O 持有目录写事务；前台 read 的现有长事务不能直接用作备份的保护方案。复制失败应拒绝恢复不完整目录，并明确释放保护与清理残留的责任。只审查正常完成、复制失败和可控交错；不要求实现修复或证明任意崩溃恢复。

## 重放与交付

`python3 checks.py verify` 保留正常提交、历史读、静止备份与复制失败断言。`python3 checks.py repro` 通过原入口稳定展示 BACKUP_RESTORE_MISMATCH 并退出1；这不是应修好的测试。`python3 checks.py demo` 展示正常版本、文件事件和恢复行。

lab.exercise(spec) 创建独立临时仓库，先提交 initial，并按 spec 重放；返回选择/声明/目录版本、独立期望行、实际恢复行、完成标记、异常类型和事件顺序。期望行直接由输入批次覆盖语义计算，不调用 Lake.read。spec 为对象，允许 before 动作列表、at 检查点到动作列表的映射和 fail_at。动作仅为 {"commit":"initial|change|return"}（取其中一个批次名）、{"gc":"plan"}、{"gc":"sweep"}、{"gc":"collect"}；plan 暂存候选，sweep 使用这批候选。未列字段省略。每个检查点仅触发一次。备份事件依次为 backup.selected、backup.catalog、各文件的 backup.before_file:N 与 backup.after_file:N、backup.complete；文件按文件名排序，N从0计数。检查点回调在对应源码操作之后同步运行，不另建线程；这足以固定本题允许的交错。失败事件可见于 backup_events。

审查须分别改变提交时点、回收时点及复制失败位置，并以必要组合对照区分单因、掩盖与合法交错；包含候选回收与载荷再次引用的边界。结论应基于原入口的真实恢复内容，不能只看摘要或文件存在，也不能只复述公开 repro。不要扩大到一般并发测试平台。

交付 REVIEW.md、修订的 docs/backup.md、review_cases.json 和由实际重放生成的 review.json。REVIEW.md 记录源码行号、触发顺序、独立期望与实际、发布结论及最小兼容修复建议；区分现状与建议，说明版本选择、所需文件保护的取得/释放及候选回收的裁决顺序、失败责任。不实现生产修复。保留 lake.py、backup.py、reclaim.py、lab.py、checks.py、samples.json 及原断言。

review_cases.json 为非空数组，每项含唯一字符串 id、spec、locations；locations 为 {path,line,text} 列表，path 取三个生产源码文件之一，line 从1计数，text 为该行去首尾空白后的原文。报告用 case:ID 引用每项。evidence_gate.py record 调用原 lab 生成 review.json；verify 重跑原回归、核对源码定位、报告引用与已保存实际结果；demo 展示真实证据。证据中必须包含原失败，报告不能把建议方案的预测当作当前输出。

.pointwise-runtime.json 是公开运行清单：version 为1，kind 为 cli，setup 为 argv 数组列表，verify/demo 为 argv 数组。当前清单演示基线；交付时将 verify、demo 接到 evidence_gate.py 的对应入口，保留正常回归与原失败证据。运行入口用于湖表审查，不是通用文件管理产品。
