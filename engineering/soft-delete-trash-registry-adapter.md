# 软删回收站的登记式聚合接口：新增一类内容只登记一条就接通（可复用）

| 项 | 值 |
|---|---|
| 标签 | `engineering` `flutter` `sqflite` `软删除` `回收站` `注册表/适配器` |
| 成熟度 | ✅ 已落地并验证（七类在册·真机三拍闭环·变异九刀自证） |
| 来源项目 | LifeHarness（Flutter + sqflite，Android 优先）·`lib/shared/trash/` |
| 参考实现 | `lib/shared/trash/trash_specs.dart`（`TrashKindSpec` / `trashKindSpecs` / `trashSpecOf` / `trashPurgeExpired`）、`trash_repository.dart`、`trash_providers.dart`、`trash_models.dart`；守护 `test/trash_guard_test.dart`；用例 `test/item_delete_test.dart` |
| 适配成本 | 中——模式本身可直接抄；主要工作量在**给每类补齐软删列与五件套出口**，以及把"摘掉 switch 后丢掉的编译期穷尽性"用机器守护补回来（§5/§9） |

> 一句话：把「删除但保留记录」的 N 个模块收敛成**一个聚合面 + 一张登记表**——每类内容登记六条线（读源 / 恢复 / 彻底删除 / 刷新归组 / 到期清理 / 写信号），
> 回收站屏、计数角标、入口文案、到期清理全部按表驱动，新增一类只写一条登记。
> **不适用**：只有一两类软删内容的小项目（直接 switch 更清楚，注册表的间接层是负债）；需要跨进程/多端一致回收站的场景（本篇只管单机库内一致性）。
>
> **与既有篇的分工**：`mobile/soft-delete-trash-source-guard.md` 管**单表内**的软删语义与谓词守护（迁移只加列、在册出口唯一、恢复即同一行、到期清理落日志、守护三段、文案同源＝该篇 S1~S8，本篇不重复）；本篇管**跨模块聚合面**——N 类内容共用一屏回收站时，怎么让类别知识只出现在一张登记表里、以及摘掉 `switch` 之后如何用机器守护补回丢失的编译期穷尽性。两篇配套使用。

---

## 0. 一句话定义

回收站 = 「按类别登记的读/写适配器表」+「一个遍历该表的编排层」；类别知识只出现在登记项里，编排层不许认识任何具体类别。

## 1. 术语

| 术语 | 说明 |
|---|---|
| 软删 / 进回收站 | 只给行打 `deleted_at`（epoch 毫秒），记录原地保留，可恢复 |
| 在册 | `deleted_at IS NULL` 的集合——所有列表、统计、每日成本共用的口径 |
| 垃圾箱行 | `deleted_at IS NOT NULL` 的行，回收站的取数对象 |
| 彻底删除（purge） | 真 `DELETE`，可能连带清子记录（按类定义） |
| 到期清理 | 冷启动按「保留期档位」把超期垃圾箱行 purge 掉 |
| 孤行 | 父实体已不在架（被彻底删除或仍在垃圾箱）的子记录，**不可单独恢复** |
| 登记项 / spec | 某一类内容在注册表里的那一条（六条线） |
| 写信号 / signals | 该类源模块每次写库后自增的 provider，回收站监听它自我失效 |
| 刷新归组 / refreshGroup | 多个类别共用同一个刷新出口时填同一键（如阅读域的书/划线/书单共用一个 tick） |

## 2. 需求规则（编号即验收项）

- **R1 单一聚合面**：回收站的读与写只有 `TrashRepository` / `TrashService` 两个出口；屏层、入口角标、记录管理行、模块内抽屉全部经它取数，禁止模块自己拼回收站。
- **R2 类别知识只出现在登记项**：编排层（repository / providers）里**不许再出现** `switch (kind)` / `case TrashKind.x`。这条是机器闸（§5 D4），不是口头约定。
- **R3 每类六条线齐备**：`rows`（读垃圾箱行→统一视图模型）、`restore`（回原模块原位）、`purge`（真删，返回连带清除条数）、`refresh`（写库后把该类所在模块的读数刷回原位）、`purgeExpired`（到期清理）、`signals`（写信号列表）。缺一条即视为该类未接入。
- **R4 恢复＝回原位，不新建**：恢复只清 `deleted_at`，**状态、计费位、外键归属一律不动**；同一条记录恢复前后是同一个主键。
- **R5 任意状态可删**：删除入口不许按业务状态自我设限（"已归档/已报废就不给删"是常见反例）；状态差异只体现在**后果读数**里。
- **R6 父实体软删要按"会不会藏掉子记录"判定**：若子记录的在册口径继承父行的 `deleted_at`，软删父行会把整棵子树从所有在册视图里抹掉——这类实体要么改成"只删自己"，要么连带口径写进需求并给足提示。
- **R7 孤行只提示不代做**：父行没了的子行，在回收站里置灰＋说明原因＋给下一步（"先恢复书或整体删除"），**禁止**给"恢复"按钮，也禁止编一个假父名。
- **R8 连带口径要在删除前就说清**：确认弹层与回收站行上的"连带清 X 条 / Y 次 / 关联流水保留"必须与真正执行时同源一次取数，两处数字不许各算一遍。
- **R9 到期清理按登记顺序遍历**：父实体类必须排在其子行类**之前**（先清书行，再清划线/书单），否则本轮刚被置孤的行会被当作过期行清掉；顺序写进登记表，消费侧不许 `reversed` / `sort`。
- **R10 写库后必须让回收站重取**：靠 `signals` 监听自我失效，而不是在每个删除入口手工埋一次刷新（十处入口＝十处漏点）。
- **R11 文案里的类别数是算出来的**：屏上"七类删除内容"这类字样必须由 `TrashKind.values.length` 或登记表长度生成，加一类时不许出现"六类"残留（守护扫旧字样）。
- **R12 视图模型对空值兜底要用人话**：空标题→"未命名笔记/（图片书摘）"；取不到父名→直说"所属书已彻底删除"。宁可少显示，不编数据。

## 3. 状态机 / 数据模型

一行内容的生命周期（单表内）：

```
在册 (deleted_at IS NULL)
  ── 软删 ──▶ 垃圾箱 (deleted_at = now)
  ◀─ 恢复 ──    │
                ├── 用户彻底删除 ──▶ 物理 DELETE（+ 连带子记录）
                └── 冷启动到期清理 ──▶ 物理 DELETE（超保留期档位）
```

统一视图模型（跨类别）关键字段：`kind` / `rawId`（**类型按类不同**：int 或 String，恢复与 purge 处按类 cast）/ `title` / `deletedAt` / `subs`（行内副信息）/ `orphan` + `orphanHint` / `cascadeNotes`（彻底删除后果）/ `src`（源模型对象，供恢复路径复用）。

不变式：

1. 一条记录在回收站里最多出现一次（`key = '${kind.name}:$rawId'` 唯一）。
2. 排序＝按 `deletedAt` 倒序，`null` 落底（用 `DateTime(1970)` 兜底参与比较，别抛）。
3. 任何在册读数（列表/统计/角标/派生计算）必须带 `deleted_at IS NULL`；**派生面**（如按别的表反查、连续天数、保修提醒）最容易漏。

## 4. 关键算法或流程

- **读源挂子记录**：`rows` 里取该类垃圾箱行时，**与在册列表同一套装配**（把时间轴/用量等子记录一起挂上），否则行上的派生读数只能吃到主表列——真机就出过"成本池显示购入价、把二次付费抹掉"（§10 坑 1）。
- **连带读数一次取**：`trashReadout(id)` 一次返回 `{events, usages, txns, ...}`，确认弹层与回收站行都吃它，避免两处不一致。
- **到期清理**：`purgeExpired(db, keepDays)` 按登记表顺序 `for` 遍历累加行数，返回 `Map<人话类名, int>` 供启动日志逐类留痕；`keepDays<=0`＝档位"不清理"，直接返回空。
- **恢复/彻底删除分派**：`trashSpecOf(kind)` 查表；查不到抛 `StateError`（**不许静默跳过**——静默＝"删了还在"或"恢复出幽灵"）。
- **刷新归组**：`_refreshFor(kinds)` 先自增回收站自己的 tick，再按 `refreshGroup` 去重刷模块读数（同组只刷一遍），最后按类 invalidate。
- **常量与前置假设**：保留期档位＝`30/7/90/不清理`（默认 30），回收站与"到期清理"共用同一档位；`deleted_at` 存 epoch 毫秒；剩余天数按**自然日**算（`DateTime(y,m,d)` 相减），不按 24h。

## 5. 与数据层的边界规则

- **D1 前提**：单表软删的迁移/在册出口/恢复语义/到期清理/守护三段，照 `mobile/soft-delete-trash-source-guard.md` 的 S1~S8 做，本篇不重复。
- **D2 表名匹配要看得见 SQL 串内裸表名**（该篇守护的扩展）：只匹配 Dart 常量与带引号表名，会让 `FROM todos` 这种串内裸表名**静默漏检**——本项目实测漏出两处真缺陷（保修派生把已删条目算进提醒、连续天数把删掉的待办算成活动）。扩成 `\b(?:FROM|UPDATE|INTO|JOIN)\s+表名\b` 后立刻现形。
- **D3 摘掉 `switch` 就要补回穷尽性**（本篇核心）：三条守护——① 每个枚举值在登记表里**登记一次且仅一次**（漏册/重复都红）；② 编排文件里不得再出现 `switch (` 或 `case TrashKind.`（**只断代码行**，注释不算证据）；③ 每个登记项必须有非空 `signals`，且同域多类必须共享 `refreshGroup`。
- **D4 整包备份含垃圾箱行**：备份/导出侧按"存储口径"取全表（软删行也占空间），别套在册谓词；这条要写进守护白名单并带理由，否则被 D2 打红（白名单失效自检照该篇 S8② 做，防改名后守护空转）。

## 6. 性能规则

- **P1 一次读装配，别按行 N+1**：`rows` 里对每行再查子记录（`for` 里 `await`）在千级垃圾箱行时会卡屏；改成一次取全量子记录后按 `item_id` 分组。
- **P2 计数与列表同源**：分段 chips 的计数从已取的条目集合算（`byKind`），不要每段再发一次 `COUNT(*)`。
- **P3 到期清理放冷启动且逐表打点**：单事务内先取 id 列表再删子表，日志一行一类，便于真机取证。

## 7. 扩展点

- **E1 新增一类**＝登记一条 spec（六条线）＋该模块 DAO 补五件套（softDelete / trashed / restore / purge / purgeExpired）＋迁移加列。屏层、入口、计数、到期清理、文案计数**零改动**——这是本模式的核心收益，也是验收点（§9 用例 6）。
- **E2 类别差异化 UI**：只通过视图模型字段（`subs` / `amberSub` / `orphan` / `cascadeNotes`）表达，屏层不许按 `kind` 分支。
- **E3 换存储**：`rows`/`purge` 都是函数注入点，可整体换成 Drift/Realm/服务端接口，编排层不动。
- **E4 多端同步**：若要跨设备，把 `deleted_at` 与恢复动作做成可同步事件（墓碑记录），D2 审计口径不变。

## 8. 实现骨架

```dart
/// 一类内容在回收站里的全部知识，只写在登记项里。
class TrashKindSpec {
  TrashKindSpec({
    required this.kind,
    required this.rows,          // 读垃圾箱行 → 统一视图模型（含子记录装配）
    required this.restore,       // 回原模块原位（清 deleted_at，不动状态）
    required this.purge,         // 真删，返回连带清除条数（屏层据此出回执）
    required this.refresh,       // 写库后把该类所在模块读数刷回原位
    required this.purgeExpired,  // 到期清理（受全局保留期档位控制）
    required this.signals,       // 该类源模块的写信号
    required this.refreshGroup,  // 同组只刷一遍（同域多类共用一个 tick）
    this.restoreToastOf,         // 类别专属恢复回执
  });
  final TrashKind kind;
  final Future<List<TrashItem>> Function(Database db) rows;
  final Future<void> Function(Ref ref, TrashItem it) restore;
  final Future<int> Function(Ref ref, TrashItem it) purge;
  final void Function(Ref ref) refresh;
  final Future<int> Function(Database db, int keepDays) purgeExpired;
  final List<ProviderListenable<Object?>> signals;
  final Object refreshGroup;
  final String Function(TrashItem it)? restoreToastOf;
}

final List<TrashKindSpec> trashKindSpecs = [ /* 待办 笔记 习惯 书籍 划线 书单 物品 … */ ];

TrashKindSpec trashSpecOf(TrashKind kind) => trashKindSpecs.firstWhere(
      (s) => s.kind == kind,
      orElse: () => throw StateError('回收站类别未登记：${kind.name}'), // 不静默
    );

/// 编排层：认识表，不认识类别。
class TrashRepository {
  Future<List<TrashItem>> items() async {
    final out = <TrashItem>[];
    for (final s in trashKindSpecs) out.addAll(await s.rows(db));
    final epoch = DateTime(1970);
    return out..sort((a, b) => (b.deletedAt ?? epoch).compareTo(a.deletedAt ?? epoch));
  }
  Future<Map<String, int>> purgeExpiredByTable(int keepDays) =>
      trashPurgeExpired(db, keepDays);   // 按登记顺序遍历，键＝人话类名
}

/// 写侧：分派＝查表；刷新＝按 refreshGroup 去重。
class TrashService {
  Future<void> _restoreOne(TrashItem it) => trashSpecOf(it.kind).restore(ref, it);
  Future<int> _purgeOne(TrashItem it) => trashSpecOf(it.kind).purge(ref, it);
  void _refreshFor(Set<TrashKind> kinds) {
    ref.read(trashTickProvider.notifier).state++;
    final groups = <Object>{};
    for (final k in kinds) {
      final s = trashSpecOf(k);
      if (groups.add(s.refreshGroup)) s.refresh(ref);
    }
  }
}

/// 读侧：监听所有类的写信号自我失效（不在删除入口手工刷新）。
final trashItemsProvider = FutureProvider<List<TrashItem>>((ref) {
  for (final l in trashSourceSignals) ref.listen(l, (_, _) => ref.invalidateSelf());
  return ref.watch(trashRepositoryProvider).items();
});
```

守护（D2/D3/D4 的最小形状）：

```dart
const tables = {'tTodos': 'todos', /* … */};
RegExp ref(String c, String raw) => RegExp(
    '''(?:$c|['"]$raw['"]|\\b(?:FROM|UPDATE|INTO|JOIN)\\s+$raw\\b)'''); // 看得见串内裸表名
// 命中处向前找最近语句起点、向后收语句体 → 读语句必须含 deleted_at，否则红灯；白名单条目失效即红。
// 每类登记一次且仅一次；编排文件代码行里禁 switch/case TrashKind.；每类 signals 非空。
```

## 9. 验收用例清单

- [ ] 1 软删后：在册列表/统计/派生读数都不含该行，回收站多一行（含兜底标题与副信息）。
- [ ] 2 恢复后：同一主键回到在册，**状态与计费位与删除前一致**（断言不是新建）。
- [ ] 3 任意业务状态（含"已归档/已报废"）都能进回收站。
- [ ] 4 彻底删除：父行清掉、子记录按类定义处理、**关联流水/账目类记录条数不变**（"保留在记账"类承诺要有正面断言）。
- [ ] 5 到期清理：只清超保留期的行；档位＝"不清理"时零删除；清理顺序＝父先子后（用例里造"本轮被置孤的行不许同轮被清"）。
- [ ] 6 **新增一类只登记一条即接通**：屏层/入口/计数/文案零改动，七类分段与"七类"字样自动到位。
- [ ] 7 孤行：置灰＋说明＋无恢复入口；父行恢复后孤行自动可恢复。
- [ ] 8 写信号：在模块内删除后**不重进页面**，回收站计数与入口角标即时变化。
- [ ] 9 派生读数审计守护：在册谓词漏一处即红（含 SQL 串内裸表名形态）；白名单文件不再命中时守护也红（防空转）。
- [ ] 10 变异自证：至少四刀——分派处回潮 `switch`／到期清理改 `.reversed`／摘掉某类 `signals`／登记项撞类（重复+漏册）。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| 回收站行上"成本池/总额"显示的是初始值，把后续追加金额抹掉了 | `rows` 只取主表行，没挂子记录，派生公式吃了主表列。**修法＝与在册列表同一套装配**，并给派生值加一条"含追加项"的用例 |
| 进回收站前删的条目不出现，要重进页面才刷新 | `FutureProvider` 缓存上次结果；手工在删除入口埋刷新会漏。改＝监听各类**写信号**自我失效（R10） |
| 恢复一个习惯后它"回到在册但又在垃圾箱" | `copyWith` 没带 `deletedAt`，任何一次经 copyWith 的写都把软删标记洗掉。定式＝**所有 copyWith 必须显式携带软删列** |
| 软删一本书后，该书全部划线/计时从所有在册视图消失 | 子记录的在册口径继承了父行 `deleted_at`（R6）。要么改父行删除语义，要么把连带范围写进需求并在确认弹层说清 |
| 书单软删时连带删成员行 → 恢复后是空单 | 恢复只能还原父行，成员行已物理消失。改＝软删不连带，成员行随父行的在册谓词自然隐藏 |
| "全查询带谓词"守护一片绿，但真有两处漏检 | 表名匹配只认 Dart 常量与带引号表名，看不见 SQL 串里的 `FROM todos`（D2）。扩正则后立刻抓出两处真缺陷 |
| 摘掉 `switch` 后新增类别忘了接入，运行期静默不显示 | Dart 的编译期穷尽性没了。必须补 D3 三条守护（登记一次且仅一次／编排层禁 switch／signals 非空） |
| 到期清理把"本轮刚被置孤的子行"直接清掉 | 未按父先子后遍历（R9），或消费侧把登记表 `reversed` 了 |
| 加了第七类，屏上仍有"六类删除内容" | 类别数写成字面量（R11）。改算出来＋守护扫旧字样（同族坑＝该篇 S7 的"天数写死"） |
| 彻底删除确认弹层只写"会删除 N 条"，用户以为账也没了 | 缺"保留部分"的显式承诺。口径＝连带清除项与**保留项**都要写（"关联流水保留在记账，不随档案删除"） |
| 回收站排序偶发抖动 | `deletedAt` 可空却参与 `compareTo`。用 epoch 兜底值比较，别 `!` |

## 11. 复用清单

1. 抄 §8 的 `TrashKindSpec` 形状与编排层（`rows/restore/purge/refresh/purgeExpired/signals/refreshGroup` 六条线），任何"删除但保留记录"的产品都能直接用。
2. 抄 §5 D2/D3 两条——**注册表模式必须配机器闸**，否则你只是把编译期检查换成了运行期沉默（D3 是本篇独有的三条）。
3. 抄 R6/R7（父实体软删的藏子记录判定与孤行只提示不代做），这是同类系统里最容易做错的两条语义。
4. 抄 §10 前四条：`copyWith` 洗掉软删标记、Provider 缓存不重取、派生读数吃主表列——都是踩过才写得出的。
5. 配套阅读：**`mobile/soft-delete-trash-source-guard.md`（单表软删语义与谓词守护 S1~S8，先读它）**、`engineering/ratchet-guard-multi-writer-policy.md`（守护红灯语义与白名单失效检查）、`engineering/mutation-harness-self-verification.md`（§9 变异自证落地）、`mobile/flutter-dismissible-optimistic-removal.md`（左滑软删的乐观出树定式）、`engineering/stale-red-triage-and-dead-guard.md`（守护空转与陈旧红灯归位）。
