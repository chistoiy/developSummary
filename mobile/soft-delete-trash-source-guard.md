# 同表混放软删（回收站）+ 源码级谓词守护（可复用）

| 项 | 值 |
|---|---|
| 标签 | `mobile` `关系库` `软删除` `守护测试` `Flutter` `sqflite` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness（`lib/features/todo/`、`lib/core/database/schema.dart`、`test/todo_trash_guard_test.dart`，V1.57 批E） |
| 参考实现 | `TodoDao.softRemove/trashed/restore/purge/purgeExpired`、`Todo.deletedAt`、`UserSettings.trashKeepTxt`、`main()` 冷启动清理块 |
| 适配成本 | 低（纯模式；列名与方法名照搬即可，守护测试改表名与白名单） |

> 解决的是**一类会被静默吞掉的 bug**：表里加了 `deleted_at` 之后，只要有**任何一处**在册查询没带谓词，
> 用户就会看到"删掉的东西还在计数 / 还在提醒 / 还出现在统计里"。这类问题运行时几乎测不全，
> 所以本篇的重点不在软删本身，而在**用一条扫源码的守护测试把它钉成编译前的红灯**。
>
> 不适用：单表只读、或软删行永远不进任何聚合/列表的场景；也不适用于需要跨设备合并回收站状态的多人协作库
> （那需要墓碑记录 + 冲突合并策略，是另一套方案）。

---

## 0. 一句话定义

在业务表上放一列 `deleted_at INTEGER`（NULL=在册）当回收站，把"在册读取"收敛到唯一 DAO 出口，
并新增一条**不看运行数据、只看源码文本**的守护测试：谁绕过 DAO 裸点表名而没带谓词，测试直接红。

## 1. 术语

| 术语 | 说明 |
|---|---|
| 在册 | `deleted_at IS NULL`，正常业务可见的行 |
| 垃圾箱 / 回收站 | `deleted_at IS NOT NULL` 的行，可恢复 |
| 软删 | 只写 `deleted_at`，不动其它列、不删行 |
| 彻底删除 | 真 `DELETE`，不可恢复，与软删是**两个入口** |
| 保留期 | 天数档位（30/7/90/不清理），到期由冷启动清理任务真删 |
| 谓词守护 | 扫源码找裸表名，白名单外必须出现谓词文本的测试 |

## 2. 需求规则（编号即验收项）

- **S1 迁移只加列、绝不动数据**：`ALTER TABLE t ADD COLUMN deleted_at INTEGER`。
  *为什么*：默认值写成 `0`/`NOT NULL` 会让全部旧行被当成"已删除"或"未删除"的假状态；NULL 才是"从未被删"的唯一无歧义表达。
- **S2 在册读取只有一个出口**：`list()`、`count*()`、聚合 SQL 全在 DAO 内带 `deleted_at IS NULL`；
  业务层/domain 层禁止裸 SQL 点名表。*为什么*：漏一处就是幽灵数据，而人肉审查询计必漏。
- **S3 恢复＝同一行**：`restore` 只把 `deleted_at` 置回 NULL，绝不新建行。
  *为什么*：新建会丢历史 id，所有外键/关联/排序位/埋点全部断裂。
- **S4 恢复要顺带恢复副作用**：提醒排程、角标计数这类"删除时撤销过"的东西，在 restore 里成对重排。
  *为什么*：只恢复数据行、不恢复提醒＝"恢复后不再提醒"的新 bug。
- **S5 硬删通道独立且入口分两级**：行内钮做软删；彻底删除必须走二次确认弹层并**逐条列出被删项**。
  *为什么*：不可逆操作与可逆操作共用一个入口，误删率极高。
- **S6 到期清理放在冷启动**：`purgeExpired(keepDays)`，`keepDays<=0` 直接返回 0（"不清理"档）；
  结果落**一条 key 级日志**（删除 N 条 · 保留期）。*为什么*：清理是幂等旁路，放启动最安全；无日志则事后无法证明它跑过。
- **S7 期限文案单一来源**：做一个 getter（如 `trashKeepTxt`）把档位翻成人话，屏头、入口行、删除提示三处同源读它。
  *为什么*：写死"30 天"会出现"用户选了 7 天、提示条还说 30 天"的自相矛盾。
- **S8 守护测试必须三段**：① 白名单外裸表名必须含谓词；② **白名单失效自检**（登记的文件不再点表名就报红）；
  ③ DAO 内部自检（在册方法体内确有谓词、软删能力齐备）。*为什么*：只有①的话，白名单会变成永久免罪牌，守护悄悄空转。

## 3. 状态机 / 数据模型

```
在册(deleted_at=NULL) --softRemove--> 垃圾箱(deleted_at=now)
垃圾箱 --restore--> 在册（同一行，其余列不变）
垃圾箱 --purge（用户确认）--> ∅（真 DELETE）
垃圾箱 --purgeExpired（冷启动·超过保留期）--> ∅（真 DELETE）
```

不变式：
- 任意在册查询结果集中不含 `deleted_at IS NOT NULL` 的行；
- `restore` 前后行 id 相同；
- `purge` / `purgeExpired` 之后该行在任何查询中都不存在；
- `keepDays=0` 时任何垃圾箱行都不会被自动删。

## 4. 关键算法或流程

- **剩余天数**：`left = keepDays - 已删天数`（按自然日差，不按毫秒差）；`keepDays<=0 → null`（文案"不清理"）；
  `left<=0 → "待清理"`；`left<=7 → 标红`。UI 只消费这个纯函数，别在页面里各算各的。
- **到期界**：`cutoff = now - keepDays*86400s`，条件 `deleted_at < cutoff`（严格早于）。
  ⚠️ 边界用例**不要卡整日数**：清理函数内部会再取一次 `now`，几毫秒推进就让等号边界翻面（见坑表）。
- **排序**：垃圾箱按 `deleted_at DESC, id DESC`（删得晚的在前，符合"刚删的想找回"）。

## 5. 与数据层的边界规则

- **D1** 建表 DDL、迁移脚本、纯 `insert` 这三类文件进守护测试白名单（它们不读在册集）。
- **D2** 全表聚合函数（`MAX(sort_order)`、导出、统计）同样要过谓词审计——它们常被当成"不算查询"漏掉。
- **D3** 备份/导出走"仅在册"还是"含垃圾箱"必须显式选择并写进文档；默认建议仅在册，回收站不进备份。
- **D4** 同步协议里软删行不进差异集（否则对端会把恢复前的行当真删）。

## 6. 性能规则

- **P1** 在册查询是绝对主路径，谓词写成 `deleted_at IS NULL` 并视数据量补部分索引：
  `CREATE INDEX i ON t(...) WHERE deleted_at IS NULL`（不支持处改用 `deleted_at` 前导的复合索引）。
- **P2** `purgeExpired` 每次启动一条 `DELETE`，不要逐行 SELECT+DELETE；返回受影响行数写日志即可。

## 7. 扩展点

- **E1** 多模块共用回收站：期限档位做成全局设置项（本项目 `lh.trash_keep_days`），各模块文案同源读它。
- **E2** 回收站分页/搜索：`trashed()` 保持谓词与排序不变，加分页参数即可。
- **E3** "批量恢复回原分组"：restore 时保留原分组/排序位列（本项目原样保留 `sort_order`），别重置。

## 8. 实现骨架

```dart
// ---- DAO：六个方法就是全部软删面 ----
Future<List<T>> list() => db.query(table,
    where: 'deleted_at IS NULL', orderBy: 'done ASC, due_at ASC, updated_at DESC, id DESC');

Future<List<T>> trashed() => db.query(table,
    where: 'deleted_at IS NOT NULL', orderBy: 'deleted_at DESC, id DESC');

Future<void> softRemove(int id) => db.update(table,
    {'deleted_at': nowMs, 'updated_at': nowMs}, where: 'id = ?', whereArgs: [id]);

Future<void> restore(int id) => db.update(table,
    {'deleted_at': null, 'updated_at': nowMs}, where: 'id = ?', whereArgs: [id]);

Future<int> purge(List<int> ids) => ids.isEmpty ? Future.value(0)
    : db.delete(table, where: 'id IN (${List.filled(ids.length, '?').join(',')})', whereArgs: ids);

Future<int> purgeExpired(int keepDays) => keepDays <= 0 ? Future.value(0)
    : db.delete(table, where: 'deleted_at IS NOT NULL AND deleted_at < ?',
        whereArgs: [nowMs - keepDays * 86400000]);
```

```dart
// ---- 守护测试：三段式（扫源码，不连库）----
final tableRef = RegExp(r'''tTodos|'todos'|"todos"''');   // 常量名 / $插值 / 裸表名三种写法并集
bool isComment(String l) { final t = l.trim();
  return t.startsWith('//') || t.startsWith('///') || t.startsWith('*'); }

List<String> hits(bool Function(String rel, String line) pred) { /* 递归 lib/**/*.dart，跳注释行 */ }

test('白名单外一律必须带 deleted_at IS NULL', () => expect(
  hits((rel, line) => !whitelist.containsKey(rel) && !line.contains('deleted_at IS NULL')), isEmpty));

test('白名单条目仍然成立（守护不得空转）', () => expect(
  whitelist.keys.where((k) => !stillHitsSource(k)), isEmpty));   // ← 这条最容易省

test('DAO 内部：在册读带谓词 · 软删六件套齐备', () { /* 取方法体子串断言 */ });
```

```dart
// ---- 冷启动清理（放在启动序列里，失败不阻断启动）----
try {
  final n = await TodoDao(db).purgeExpired(settings.trashKeepDays);
  AppLog.addKey('垃圾箱自动清理：删除 $n 条 · 保留期 '
      '${settings.trashKeepDays > 0 ? '${settings.trashKeepDays} 天' : '不清理'}');
} catch (e) {
  AppLog.warn('垃圾箱自动清理失败：$e');   // catch 必带交代
}
```

## 9. 验收用例清单

- [ ] 迁移用例：手工造 v(n-1) 结构 + 若干旧行 → 跑迁移 → 断言行数不变、列值不变、`deleted_at` 恒为 NULL。
- [ ] 联动断言（照抄原型校验脚本那条）：软删后「清单 / 分组视图 / 今日数字 / 逾期计数」四处同时减一，且表里行仍在。
- [ ] 恢复用例：id 不变、原分组/原截止不变、垃圾箱清空、提醒重新排上（S4）。
- [ ] `purgeExpired` 三条：超期删 / 界内不删 / `keepDays=0` 一条不删；边界带 1 分钟余量。
- [ ] 守护测试自证：临时造一个裸查询文件 → 红 → 删除 → 绿；把白名单改成不存在的路径 → 报"白名单失效"。
- [ ] 期限档位：选后即时回显 + **真实落盘** + **冷重载（force-stop 后）读回**三条都在。
- [ ] 彻底删除弹层：标题/玫红行/逐条列出/取消不动数据 四条 UI 断言。
- [ ] 全仓文案扫描：`grep` 无"（模拟）/规划 V1.x"残留（假承诺一律摘除）。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| 守护测试上线即"零命中"通过 | 正则只写了 `DbSchema.tTodos`，源码里实际是 `$tTodos` / 裸 `'todos'`；**上线前先打印命中数确认 >0** |
| 某文件改名后守护悄悄空转 | 只做了"违规检测"没做"白名单失效自检"（S8②） |
| DDL 注释被判成违规 | 扫描没跳 `//` / `///` / `*` 开头行 |
| 边界用例随机红/随机绿 | `purgeExpired` 内部第二次取 `now`，用例卡 `keepDays` 整日数时等号两侧几毫秒就翻面；改成"界内 1 分钟 / 超界 1 分钟" |
| 第二个用例报 `table todos already exists` | 内存库按**路径**在同 isolate 内共享；`setUp` 建的表在 `tearDown` 没 `db.close()` |
| 档位改了、提示条还写旧天数 | 文案在多处写死，没走同源 getter（S7） |
| 恢复后"不再提醒" | restore 只改数据、没重排提醒（S4） |
| 统计/排序被回收站污染 | `MAX(sort_order)`、导出聚合、跨模块计数（本项目是目标 KR 的已完成数）没带谓词（D2） |
| 假绿：设置改了但重启回默认 | `save()` 漏写这个键、或读取处 `try/catch` 把失败吞成默认值；验收必须"选后落盘 + 冷重载读回"两条都测 |

## 11. 复用清单

1. 任何"删除后可恢复 + 保留期可设"的关系库场景：笔记/邮件/工单/账本归档/设备清单。
2. 任何"一条规则必须由全仓所有调用点遵守"的场合，都可以抄这套**源码级守护三段式**
   （违规检测 + 白名单失效自检 + 出口内部自检）——同族可参考
   `engineering/module-decoupling-architecture-guard.md`（import 方向守护）与
   `engineering/app-log-and-error-reporting.md`（catch 必带日志的登记式棘轮）。
3. 迁移 + FFI 测试的建表/升版手法见 `mobile/sqflite-versioned-migration-ffi-testing.md`，本篇是它在"新增软删列"上的一次落地。
