# Dismissible 左滑删除的「乐观出树」定式（可复用）

| 项 | 值 |
|---|---|
| 标签 | `mobile` `Flutter` `交互` `列表` `widget 测试` `软删除` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness（`lib/features/todo/presentation/widgets/todo_list_view.dart`、`lib/features/journal/presentation/note_tab.dart`、常驻用例 `test/t3_delete_reversible_test.dart`；交互整改批六 T3） |
| 参考实现 | `_TodoListViewState._gone` / `shown` 过滤 / `_TodoTile.onDismissed`（`onGone`+`unawaited(softRemove)`+`invalidate`+下一帧 `AppToast.showUndoWith`）/ `note_tab` 的同式先例 |
| 适配成本 | 低（纯模式；集合与两行回调照搬，DAO 方法名换成自己的；无撤销条的项目可先用普通 SnackBar，同样要推下一帧） |

> 解决一类**只在 debug/测试里炸、release 真机看不出来**的框架断言：
> 滑掉一行之后，「这行从树上消失」这件事是由 provider/async 重查决定的，而不是本帧决定的，
> 于是 Dismissible 的契约（回调触发后**立刻**出树）被违背。
> 定式＝**清单宿主持一张页面生命期的 `_gone` 集合做乐观出树，写库与重查照常异步，撤销提示条推到下一帧**。
>
> 不适用：① 纯内存列表（本可以直接同步 `removeAt`，不需要这层）；② 用 `confirmDismiss` 做异步二次确认的场景
> （那条路径是"确认返回 true 才开始收缩"，时序不同，别照搬）；③ 需要跨页共享"已滑掉"状态的场景——
> 这张集合是页面级的，离开页面即失效，靠重查自洽。

---

## 0. 一句话定义

给「滑掉即删」的清单加一层**渲染期黑名单**（`Set<int> _gone`），让被滑掉的那行在回调的同一帧就消失，
异步写库与 `invalidate` 只负责让数据最终一致，撤销条延后一帧再挂。

## 1. 术语

| 术语 | 说明 |
|---|---|
| 本帧出树 | `onDismissed` 返回前，该行对应的 widget 已经不在下一次 build 的子树里 |
| `all` / `shown` | 数据源给的全量行 / 真正渲染的行（`all` 过滤掉 `_gone`） |
| 乐观出树 | 不等落库结果，先按"用户已经看到的效果"改渲染 |
| 排他撤销窗 | 同一时刻只允许一条带撤销的提示条在台上，新的顶掉旧的并接管关窗时机 |

## 2. 需求规则（编号即验收项）

- **R1 回调返回后的下一次 build，这一行必须已经不在树上**——这是框架契约（官方注释：
  "its `onDismissed` callback must remove the item from the list"），不是风格问题。
  *为什么*：`flutter/lib/src/widgets/dismissible.dart` 在 `build()` 里有一段 `assert`：
  收缩动画（`resizeDuration`，默认 300ms）**跑完之后如果这个 widget 还被重建一次**，直接抛
  `A dismissed Dismissible widget is still part of the tree.`。而"等 provider 重查落定"的那次重建必然晚于它。
- **R2 出树靠页面级 `_gone` 集合，绝不靠"先 await 写库"**。
  *为什么*：`await` 把出树推到了回调之后的重建，而那时收缩动画早已 completed，正中 R1 的雷；探针实测这样必炸（见 §10）。
- **R3 黑名单只影响渲染，不影响数据**：`shown = all.where((e) => !_gone.contains(e.id))`。
  *为什么*：重查回来后 `all` 里自然没有这行了，集合残留无害；把它当数据层用会造出"本地删了、库里还在"的真 bug。
- **R4 回调进入即把 `container` / `messenger` 取好，回调体内不再碰 `ref` / `context`**。
  *为什么*：这枚 tile 马上要随 Dismissible 出树，State 被 dispose 之后再 `ref.read` 就是
  ref-after-disposed；跨弹层同理（`ScaffoldMessenger.of` 要在弹层 pop 前取）。
- **R5 写库 `unawaited` + 紧跟 `invalidate`**（两条：主列表与派生计数各一条）。
  *为什么*：顺序反了（先 await 再 invalidate）= R2 的雷；只 invalidate 不写库 = 刷新后又回来，用户觉得"删不掉"。
  *前置假设*：同一 DAO/同一 sqflite 连接的操作按队列串行，所以重查读到的是写完之后的行。
  换库、换 isolate、或读写走两个连接时，这条假设要自己补（要么先 await 但按 R2 的黑名单出树，要么在重查里合并）。
- **R6 撤销提示条建议 `addPostFrameCallback` 延后一帧——但这条是保守写法，不是硬约束（实测记录见 §10）**。
  *为什么*：同帧里既改渲染黑名单、又让 `ScaffoldMessenger` 走一遍 `hideCurrentSnackBar`+`showSnackBar`，
  等于把两件事挤进同一次重建；框架对"已滑掉的那行还能不能被重建"的检查时机一旦收紧，这里就是第一个踩的。
  延后一帧零成本。**别把这条写成守护用例**：它红不出来，写死了就变成不可复核的教条（本篇就是踩过一次）。
- **R7 撤销动作三件套，缺一不可**：`_gone.remove(id)`（本帧放回）＋ 写库 `restore` ＋ `invalidate`。
  *为什么*：少第一条，点了撤销要等一次异步重查才看得见（用户判定"没反应"）；少第二条＝假撤销，
  翻页或重进又没了；少第三条，角标/统计还按删掉的算。
- **R8 交付点要有源码级守护**：常驻用例断言清单文件里 `contains('softRemove')` 且
  `isNot(contains('.remove('))`，并断 `contains('showUndoWith')`（找回入口不许被"顺手优化"掉）。
  *为什么*：这类语义回潮（滑删又变真删）在页面上看不出来，只有读源码能钉住。

## 3. 状态机 / 数据模型

```
在册 ──滑掉(本帧：_gone.add)──> 不渲染 ──异步：DB deleted_at≠NULL──> 回收站
不渲染 ──点撤销(本帧：_gone.remove)──> 回到在册 ──异步：DB deleted_at=NULL──> 在册（同一行）
```

不变式：
- `shown ⊆ all \ gone`（任何时候，gone 里的 id 不渲染）。
- `gone` 是**渲染期**状态，页面销毁即消失；权威状态只在数据库那一列（`deleted_at`）。
- 撤销必须回**同一行**（只清标记），不新建行——新建会断掉外键、排序位与历史关联。

## 4. 关键算法或流程（一帧一帧写清）

框架依据（Flutter 3.38.9，`packages/flutter/lib/src/widgets/dismissible.dart`）：
`resizeDuration` 默认 `Duration(milliseconds: 300)`（L112）；`resizeDuration==null` 时
`onDismissed` 在 `_startResizeAnimation()` 内**同步**调用（L580-583）；
契约那段检查是 `build()` 里的 `assert(() { if (_resizeAnimation!.status != AnimationStatus.forward) throw … }())`
（L621-637，抛点 L626）——**它包在 `assert` 里，release 不执行**，所以真机看不出问题。

```
拖过阈值 → move 动画 completed → _confirmStartResizeAnimation()（有 confirmDismiss 才异步）
        → 收缩动画 300ms → **动画跑完那一刻**框架才调 onDismissed
          （`resizeDuration==null` 时不等收缩，滑动动画结束就调；实测默认配置下
           拖动后 1 帧与 150ms 都还没触发，200+200ms 之后才触发）
本回调内（同步，禁 await）：
   1) container = ProviderScope.containerOf(context, listen:false); messenger = ScaffoldMessenger.of(context)
   2) onGone(id)      → 宿主 setState(_gone.add(id)) → 下一次 build 里没有这一行  ← R1/R2/R3
   3) unawaited(repo.softRemove(x))                                      ← R5
   4) container.invalidate(listProvider); container.invalidate(countProvider)
   5) addPostFrameCallback(() → AppToast.showUndoWith(messenger, '已移入回收站', onUndo: () {
          onBack(id); unawaited(repo.restore(x)); 再次 invalidate 两条 }))  ← R6/R7
回调返回后的任意一次重建：如果这个 key 的子树还在 → 收缩动画状态已是 completed → 抛断言。
"出树"由 2) 的黑名单保证，**不是**由 3) 的落库结果保证——这就是本篇的全部重心。
```

要同步改的地方：**文案**（提示条文字与守护用例的 `contains` 是配对的）、**计数器 provider**
（漏一条 invalidate 就是"删了角标还在"）、**列表 key**（`ValueKey('dismiss-$id')` 是测试与真机走查的锚点，改名要连测试一起改）。

## 5. 与数据层的边界规则

- **D1 删除落库走软删（`deleted_at`），回收站读取谓词由另一套守护保证**：见
  [`mobile/soft-delete-trash-source-guard.md`](soft-delete-trash-source-guard.md)。本篇只管"滑掉之后的渲染与找回入口"。
- **D2 撤销不是"新增一条记录"**：`restore` 只把标记清回 NULL，其余列（象限/截止/归属）一律不动，
  否则"撤销"会变成"重建"，用户看到的顺序和统计都会变。

## 6. 性能规则

- **P1 集合判重 O(1)**，每帧多一次 `where` 过滤，行数量级到几千才需要换成"渲染前 splice"，
  但**不要为了省这点开销去改数据源**（那是 R3 的反面）。
- **P2 一次滑一条**：批量滑掉（多选删除）用同一张 `_gone.addAll(ids)`，不要为每条各起一次 `invalidate`。

## 7. 扩展点

- **E1 撤销条被下一条顶掉**：让"带撤销的条"共享一个排他窗（token + `controller.closed` 跟随框架动画时钟，
  不要另起裸 `Timer`——`FakeAsync` 下裸定时器会炸测试不变量）。
- **E2 找回入口不止一条**：提示条撤销只是"快捷路径"，完整找回必须在回收站页；文案与保留期天数要同源读设置项
  （写死"30 天"会出现用户选了 7 天、提示条还说 30 天的自相矛盾）。
- **E3 想把语义换成真删**：只改回调内那一行（`softRemove` → 真删）＋ 去掉回收站谓词守护；
  定式的其余部分（乐观出树、延后一帧、三件套）与删除是否可逆无关。

## 8. 实现骨架

```dart
/// 清单宿主：除了渲染，还管「乐观出树」这张黑名单。
class _ListViewState extends ConsumerState<ListView> {
  final Set<int> _gone = {};                     // R2/R3

  @override
  Widget build(BuildContext context) {
    final shown = widget.items.where((e) => !_gone.contains(e.id));
    return SlidableList(
      itemBuilder: (_, e) => _Tile(
        key: ValueKey('dismiss-${e.id}'),        // 测试与真机走查锚点
        item: e,
        onGone: (id) => setState(() => _gone.add(id)),
        onBack: (id) => setState(() => _gone.remove(id)),
      ),
    );
  }
}

// tile 内
onDismissed: (_) {
  final container = ProviderScope.containerOf(context, listen: false); // R4
  final messenger = ScaffoldMessenger.of(context);                      // R4
  final repo = container.read(repositoryProvider);
  onGone(item.id!);                                                     // R1/R2：本帧出树
  unawaited(repo.softRemove(item));                                     // R5
  container.invalidate(listProvider);
  container.invalidate(countProvider);
  WidgetsBinding.instance.addPostFrameCallback((_) {                    // R6
    AppToast.showUndoWith(messenger, '已移入回收站', onUndo: () {
      onBack(item.id!);                                                 // R7
      unawaited(repo.restore(item));
      container.invalidate(listProvider);
      container.invalidate(countProvider);
    });
  });
},
```

测试侧骨架（四条都会踩到，逐条写在用例里）：

```dart
await tester.drag(find.byKey(ValueKey('dismiss-$id')), const Offset(-500, 0));
expect(await pollText(tester, '已移入回收站'), isTrue);   // 找回入口必须在
await settleFrames(tester);                               // 进场动画落定，否则钮不可命中
final undo = find.descendant(of: find.byType(SnackBar), matching: find.text('撤销'));
await tester.tap(undo);
for (var i = 0; i < 20; i++) {                            // 小步 pump：大 pump 会跳过 SnackBar 退场
  await tester.pump(const Duration(milliseconds: 50));
  if ((await row(id))['deleted_at'] == null) break;
}
expect(find.text('约体检'), findsOneWidget);              // 本帧就放回（R7）
```

## 9. 验收用例清单

- [ ] 滑掉：**行还在库里**（`deleted_at` 非空）＋ 在册列表不再出现 ＋ 出现带「撤销」的提示条。
- [ ] 滑掉后**不做任何 await 也能通过**：测试里 `takeException()` 为 `null`（证明没踩 R1）。
- [ ] 点撤销：条目**当场回到清单**（不等重查），库里 `deleted_at` 清回 NULL，象限/截止等字段没被动过。
- [ ] 派生计数（角标/统计）在滑掉与撤销两个方向都跟着变。
- [ ] 连续滑两条：第二条的提示条顶掉第一条后，**剩下的那条仍能撤销**（排他窗行为明确）。
- [ ] 页面重进后列表与数据库一致（黑名单不留"本地没了、库里还有"的尾巴）。
- [ ] 源码守护：清单文件断言含软删入口与撤销条、不含真删调用（R8）。
- [ ] **负向基线**（换项目第一次落地时做一次）：临时写一个用例，在回调里 `await` 落库后再出树，
  确认它抛出 R1 那句断言——证明这个环境确实会炸，定式不是在防一个不存在的问题。
- [ ] 变异自证：把 `softRemove` 改回真删、把撤销文案改掉、把 `restore` 摘掉——三刀各自让对应用例红
  （工装口径见 [`engineering/mutation-harness-self-verification.md`](../engineering/mutation-harness-self-verification.md)）。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| 调试期抛 `A dismissed Dismissible widget is still part of the tree.`（栈到 `dismissible.dart` 的 `build`） | 回调里 `await` 写库 / 只靠异步重查出树，收缩动画完成后又被重建一次（R1/R2）。探针复现：`onDismissed` 里 `await Future.delayed(450ms)` 后每 50ms 重建 → 必抛 |
| **真机 release 包看不出问题** | 那段检查写在 `assert(() { throw … }())` 里，只在 debug 生效；release 不抛，只是这一行停在收缩后的形态（`SizeTransition` 从 1.0 收到 0.0，推论）直到重查把它移走。所以这条必须靠 widget 测试钉，不能靠走查 |
| 点了「撤销」没反应 | ① `SnackBarAction` 在**滑入动画期间不可命中**，要先 `pumpAndSettle`/`settleFrames`；② 撤销回调只写了库、没有 `_gone.remove`（R7），要等下一次重查才看得见 |
| 撤销之后条目"闪回两次"或测试里 `findsNWidgets(2)` | 单次大 `pump(Duration(seconds: 2))` 跳过了 SnackBar 退场定时器；用小步 pump 循环（见 §8 测试骨架） |
| 「同帧挂提示条一定会炸」 | **不成立**，别再这样写注释。变异实验：把 `addPostFrameCallback` 摘掉、回调里同帧挂条，5 条常驻用例全绿；独立探针（乐观出树 + 同帧 `hideCurrentSnackBar`/`showSnackBar`）也没抛断言。真正会炸的只有 R1/R2（出树靠异步）。延后一帧按 R6 的定位走：保守写法，不做守护 |
| 提示条文案改了，守护用例红在别处 | 守护用例断的是文案子串（`contains('已移入回收站')`），改文案要连刀单一起改，否则红因是"字符串对不上"而不是行为变了 |
| 回调里 `ref.read(...)` 报 ref/context 已失效 | tile 随 Dismissible 出树后 State 已 dispose；进入回调先抓 `containerOf` / `ScaffoldMessenger.of`（R4） |
| 用 `confirmDismiss` 做二次确认，滑掉时序全变 | 那条路径是"异步确认返回 true 才开始收缩"，`onDismissed` 触发点更晚；本篇定式按无 `confirmDismiss` 写，混用时按 §「不适用」重新验证 |
| 删掉了但角标/统计没变 | 只 `invalidate` 了主列表，派生 provider 漏一条（R5 是"两条都要发"） |

## 11. 复用清单

1. 宿主 State 加 `Set<int> _gone` + `shown` 过滤 + 往下传 `onGone/onBack`（三处，一次到位）。
2. 回调体照 §8 骨架的 5 步顺序写：取句柄 → 本帧出树 → 异步写库 → 双 invalidate → 下一帧挂撤销条。
3. 列表项 key 用 `ValueKey('dismiss-$id')`，测试与真机走查共用同一锚点。
4. 常驻用例两条：行为用例（滑掉/撤销各一条）＋ 源码守护（R8）。
5. 交付前跑一次变异自证（三刀），确认这些用例真的会红。
