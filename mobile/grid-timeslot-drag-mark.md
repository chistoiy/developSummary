# 时间网格「滑选多格 → 标记区间」（可复用）

| 项 | 值 |
|---|---|
| 标签 | `mobile` `Flutter` `交互` `时间轴` `手势` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness（`lib/features/calendar/presentation/widgets/grid_day_view.dart`、`block_mark_sheet.dart`、`time_block.dart`） |
| 参考实现 | `GridDayView`（Listener 滑选+固定面积网格）、`showBlockMarkSheet`、`TimeBlockDao.removeOverlapping` |
| 适配成本 | 低（网格与时间语义解耦，换 domain 即可） |

> 把一天铺成 N 格（06:00-24:00 ÷ 粒度），用户**按住横向拖动连续选中多格**，松手弹出标记面板写入区间。
> 与相册滑动多选（本库 `flutter-gallery-drag-selection.md`）同属"连续滑选"交互族，
> 差异在：**面积固定**——任意粒度全天恒一屏，格子尺寸随粒度自动伸缩而非数量增长。
> 不适用：需要精确到分钟的手动输入场景（提供表单入口兜底）。

---

## 0. 一句话定义

固定画布的时间网格：`cols = max(6, round(sqrt(n·0.82)))`、总高 `min(52vh, 470)`，Listener 三事件（down/move/up）做**连续区间选择**，松手落标记面板，区间与已有块做覆盖式合并。

## 1. 术语

| 术语 | 说明 |
|---|---|
| 粒度 gran | 1 格代表的分钟数（1/5/10/15/30/60） |
| 区间 [startMin,endMin) | 一天内分钟数，左闭右开 |
| 覆盖标记 | 新区间删除与之重叠的旧手动标记后写入 |
| 回填块 | 其他流程（如计时结束）自动生成的块，`source` 区分，不参与覆盖删除 |

## 2. 需求规则（编号即验收项）

- **R1 总面积固定**：网格像素尺寸与粒度无关（`n=(1440-360)/gran` 格塞进同一画布），保证"任意粒度全天一屏概览"。*为什么：滚动会破坏时间空间感，粒度切换应像变焦而不是拉长。*
- **R2 列数自适应**：`cols=max(6, round(sqrt(n*0.82)))`（15min→8 列、60min→6 列观感稳定），`cellAspectRatio=cellW/cellH` 由 LayoutBuilder 实算。
- **R3 连续滑选**：`Listener`（非 GestureDetector）收 down/move/up；down 定锚格、move 只更新终点格、up 打开面板。**move 中不弹面板、不做 IO**。
- **R4 选中反馈**：选中格描边 + 底部实时提示「已选 HH:MM - HH:MM · N 格 = M 分钟」。
- **R5 单格命中已有块 → 面板出现"取消标记/覆盖"分支**；多格区间一律新标记（覆盖语义）。
- **R6 覆盖式写入**：`removeOverlapping(day,s,e)` 只删 `source='mark'` 的同区间旧块，**永不删回填块**（`source='event'`），再 insert。
- **R7 动画按需**：脉冲等循环动画仅在对应数据存在时 `repeat()`，无数据 `stop()+value=1`。*为什么：永动动画让 widget 测试 pumpAndSettle 超时、白耗电。*
- **R8 时间存储**：`day`（当日 00:00 epoch ms）+ `start_min/end_min`（0-1440），跨天区间在上层拆分。*为什么：按天分片查询简单，日界线规则（自然日 00:00）不侵入网格层。*

## 3. 状态机 / 数据模型

```
idle →(down i)→ selecting{a=i,b=i} →(move j≠b)→ selecting{a,b=j} →(up)→ sheet(a,b) →(确认)→ idle + 写库
                                                        →(取消)→ idle
TimeBlock{ id, day, startMin, endMin, category, note, source: mark|event, eventId? }
```
不变式：`startMin < endMin ≤ 1440`；同 (day,source=mark) 区间两两不重叠（R6 保证）。

## 4. 关键算法或流程

- 坐标→格号：`i = floor(dy/(cellH+gap))*cols + floor(dx/(cellW+gap))`，越界返回 -1 忽略。
- 分钟↔格号：`minute(i)=360+i*gran`；区间分钟：`[minute(a), minute(b)+gran)`。
- 小时标：`minute%60==0 && cols<=12` 才画，防小粒度文字堆叠。

## 5. 与数据层的边界规则

- **D1** 网格组件不直连 DB：数据经 provider（`dayBlocksProvider.family(day)`），写库在面板/dao。
- **D2** 其他模块向网格写数据只走表约定（如日程结束回填 `source='event'` 块），不 import 网格内部。

## 6. 性能规则

- **P1** 单天 ≤1440 格（1min 粒度），GridView.builder 直出即可；勿上 CustomPainter 除非 >5k 节点。
- **P2** 粒度切换只重算布局常量，选中态清空（`selA=selB=null`）。

## 7. 扩展点

- **E1** 分类字典可配置（设置页自定义名/色）：`BlockCat` 换成 DB 表驱动。
- **E2** 区间长按拖动改边界（resize handle）。
- **E3** 周/月密度视图复用同一 cell 布局数学。

## 8. 实现骨架

```dart
// 布局
final n = (1440-360)~/gran;
final cols = max(6, sqrt(n*0.82).round());
final height = min(screenH*0.52, 470.0);
final cellH = (height - gap*(rows-1))/rows;

// 手势（Listener 不参与竞技场，raw 指针直达）
Listener(
  onPointerDown: (e)=> { a=b = idxAt(e.localPosition) },
  onPointerMove: (e)=> { if((b=idxAt(e.localPosition))==old) return; setState },
  onPointerUp:   (e)=> openMarkSheet(min(a,b), max(a,b)+gran),
  child: GridView.builder(...NeverScrollableScrollPhysics),
)

// 覆盖写入
removeOverlapping(day, s, e, where source='mark'); insert(newBlock);
```

## 9. 验收用例清单

- [ ] 粒度 1↔60 切换：网格总面积不变，格数与列数联动，提示行数字正确。
- [ ] 按住横拖跨 ≥2 格松手 → 面板出现，区间=首格起点~末格终点。
- [ ] 单格点在已有块上 → 面板显示该块信息 + 取消标记入口。
- [ ] 新区间覆盖旧 mark 块；`source=event` 回填块不被覆盖删除。
- [ ] 无进行中事项时脉冲动画不运行（测试 pumpAndSettle 不超时）。
- [ ] 拖选中途拖出网格边界不崩（idxAt 返回 -1 忽略）。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| widget 测试 pumpAndSettle 10 分钟超时 | 格子上的脉冲 AnimationController `repeat()` 无条件启动——永动动画永不收敛（R7） |
| adb 真机滑选怎么都不触发 | 设备物理分辨率与截图显示尺寸不同坐标系（720×1280 屏截图被显示为 1600 高），且页面滚动状态改变命中位置；真机手势调试先 screenshot 校准，交互链路正确性交给 widget 测试（startGesture/moveBy/up 可精确复现） |
| 选中两格却只写一格 | `dragFrom` 单帧完成 move，终点格一步跳变——面板区间用 `min/max(_selA,_selB)` 实时字段而非 build 闭包旧值（stale closure） |
| GridView 与月历 GridView 共存时测试 finder 歧义 | `find.byType(GridView)` 命中多个；用 `.last` 或独立挂载组件做手势测试 |
| 60 分钟粒度下小时标挤爆 | 未按 `cols<=12` 条件隐藏（R2 的显示密度阈值） |

## 11. 复用清单

1. 抄 `GridDayView`（布局数学+Listener 三事件）与 `block_mark_sheet`（面板+覆盖写入）。
2. 表结构照 `time_blocks`（day + start_min/end_min + source）。
3. 分类枚举换成你的领域；回填 source 语义保留。
4. 过 §9 清单，重点盯 R7（永动动画）与测试坐标坑。
