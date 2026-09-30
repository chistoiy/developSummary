# 应用内长图分享通道：RepaintBoundary → PNG → 真实路径回执（可复用）

| 项 | 值 |
|---|---|
| 标签 | `mobile` `Flutter` `分享` `图片导出` `widget 测试` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness（`lib/shared/widgets/share_card.dart`，V1.57 批A；六站点接线：物品统计/物品决算/倒数日/目标复盘/记账年度/习惯分享） |
| 参考实现 | `captureShareCard()`、`ShareCardModel/Tile/Row/Section`、`ShareCardView`、`showShareCardSheet()`、`sanitizeShareName()`、`copyText()` |
| 适配成本 | 低（模型字段自定，通道逻辑照搬） |

> 解决的是**"分享/保存图片"这件事在各页各写一遍、且大多写成假的**问题：
> 每页一个 `toast('已保存到相册（模拟）')`，文件名不净化、出图宽度不对、弹层里的反馈看不见。
> 本篇给一条公共通道：一处渲染、一处落盘、一处回执，外加一套能真的测到"图确实出来了"的用例写法。
>
> 不适用：要接系统分享面板（`share_plus`/`Activity` 那套）、或要写进系统相册（需媒体权限/Photo Editor 通道）的场景
> ——本篇刻意不申请任何权限，只落应用自己的文档目录。

---

## 0. 一句话定义

把要分享的卡片包进 `RepaintBoundary`，用**目标宽度反算 `pixelRatio`** 出定宽 PNG，
写进应用文档目录 `share/<净化名>.png`，提示条回显**真实路径**；同一份 model 并列提供"复制文字版"。

## 1. 术语

| 术语 | 说明 |
|---|---|
| 边界 (boundary) | `GlobalKey` 绑定的 `RepaintBoundary`，栅格化取它的 layer |
| paintBounds | 该 RenderObject 的绘制尺寸（逻辑像素） |
| pixelRatio | 逻辑像素→物理像素倍率；本篇按 `目标宽 / paintBounds.width` 反算 |
| 设计宽 | 卡片按固定逻辑宽（本项目 640）1:1 布局，预览靠横向滚动而非缩放 |
| 文字版 | 同一 model 生成的纯文本（`〔段名〕…`），供复制 |

## 2. 需求规则（编号即验收项）

- **C1 出图宽度反算，不写死 3**：`pixelRatio = 目标宽(1080) / boundary.paintBounds.width`。
  *为什么*：写死倍率下不同屏宽/字号出图宽度就不等于 1080，别人拿去发朋友圈就糊或超宽。
- **C2 边界链上禁止任何祖先变换**：`FittedBox`/`Transform`/`ScaleTransition`/`FractionallySizedBox` 都不行。
  *为什么*：栅格化按变换后的尺寸算——本项目实测被 `FittedBox` 缩放后出图 **1364 宽**，不是 1080。
  卡片按设计宽 1:1 布局，预览层用横向 `SingleChildScrollView` 承载。
- **C3 文件名净化**：剥 `\/:*?"<>|` 并 trim（顺带压掉首尾空白与点）。
  *为什么*：标题来自用户输入，不净化会路径穿越 / Windows 非法名 / 写出零字节怪文件。
- **C4 回执只说真话**：成功＝`已保存：<绝对路径>`；失败＝写日志 + `分享长图生成失败 · 详见日志`。
  **禁止**"已保存到相册"这类没做过的事；禁止"（模拟）"字样。
- **C5 文字版与图片同源**：一份 model 两条出口（复制文字 / 存图），别各维护一套数据。
- **C6 弹层里的反馈**：模态弹层内挂页面级 SnackBar **看不见**。要么先 `pop` 再挂 messenger，
  要么在弹层内做状态行。*为什么*：模态路由压在 ScaffoldMessenger 之上。
- **C7 隐私开关同口径传递**：金额/敏感字段有"隐藏"开关的，出图必须沿用当前开关状态
  （本项目传 `amtText(hide:)`），否则分享卡把用户刚隐藏的数据又露出去。
- **C8 不申请权限、不建相册目录**：只写应用文档目录，删除与清理归应用自身。

## 3. 状态机 / 数据模型

```
[卡片 model] --build--> ShareCardView(RepaintBoundary)
      │                          │
      └─ copyText() ─ 复制文字版   └─ captureShareCard() ─┬─ 成功 → 文件路径 → 提示条回显
                                                         └─ 失败 → 日志 + 失败提示条（返回 null）
```

model 最小字段集（够渲染六类卡）：
`file / eyebrow / title / line / big / unit / tiles[] / sections[{head, rows[], bars{}, barLabels{}}] / textVersion?`

## 4. 关键算法或流程

- 栅格化：`boundary.toImage(pixelRatio: 1080 / ro.paintBounds.width)` → `toByteData(png)` →
  `writeAsBytes`（父目录先 `create(recursive: true)`）。
- 段名与文案在 model 里就是最终显示文本，`copyText()` 只做结构化拼装（`〔段名〕`+行），不重新格式化数字。
- 前置假设：**卡片设计宽固定**、**渲染时无祖先变换**、**目标宽度是常量**。三选一变动时同步改 C1 的除数。
- 改 UI 时哪里要同步：卡片新增一段 → 同一处要改 `ShareCardSection` 渲染 + `copyText()` 分支 + 该站点 model 构造，
  漏 `copyText()` 会出现"图上有、文字版没有"。

## 5. 与数据层的边界规则

- **D1** 通道只接 model，不查库：站点自己取数（含 `await`），再 `showShareCardSheet(context, model)`。
- **D2** 落盘位置固定在 `<应用文档目录>/share/`，命名 `<净化标题>.png`；同名覆盖（不做时间戳后缀，便于用户找）。
- **D3** 不进业务备份清单，也不进云同步差异集（是衍生产物，可再生）。

## 6. 性能规则

- **P1** 单次出图 ≈ 一张 1080 宽 PNG（本项目实测 100~500KB）；只在用户点"保存图片"时渲染，预览用普通 widget 树。
- **P2** 长图（多段+图表）别用 `RepaintBoundary` 预栅格化常驻，按需一次即可。

## 7. 扩展点

- **E1** 换目标宽度（如 1242/750）只改一个常量。
- **E2** 接系统分享面板：`captureShareCard` 已返回文件路径，外面套 `share_plus` 即可，通道本身不用改。
- **E3** 多尺寸输出（朋友圈 1:1 + 长图）：同一 boundary 调两次 `toImage`，pixelRatio 各自反算。

## 8. 实现骨架

```dart
String sanitizeShareName(String raw) =>
    raw.replaceAll(RegExp(r'[\\/:*?"<>|]'), '').trim();

class ShareCardSection { final String head; final List<(String, String)> rows;
  final Map<String, double> bars; final Map<String, String> barLabels; }

class ShareCardModel { final String? file; final String eyebrow, title, line;
  final String big; final String unit; final List<ShareCardTile> tiles;
  final List<ShareCardSection> sections; final String? textVersion; }

Future<String?> captureShareCard(GlobalKey boundary,
    {required String name, String? src}) async {
  final ro = boundary.currentContext?.findRenderObject() as RenderRepaintBoundary?;
  if (ro == null) return null;                       // 忘记包边界：留痕后返回，UI 走失败文案
  try {
    final img = await ro.toImage(pixelRatio: 1080 / ro.paintBounds.width);
    final data = await img.toByteData(format: ImageByteFormat.png);
    if (data == null) return null;
    final f = File('${docsDir}/share/${sanitizeShareName(name)}.png');
    await f.parent.create(recursive: true);
    await f.writeAsBytes(data.buffer.asUint8List());
    return f.path;
  } catch (e) {
    AppLog.warn('分享长图生成失败：$e');               // catch 必带交代
    return null;
  }
}

// 弹层：预览（横向滚动·不缩放）+ 复制文字版 + 保存图片
Widget ShareCardView({required GlobalKey boundary}) => RepaintBoundary(
      key: boundary,
      child: SizedBox(width: kCardDesignWidth, child: /* 卡片树 */),
    );
```

```dart
// 用例侧要点：真异步 I/O + 交错 pump + 按字节数轮询（FakeAsync 下栅格回调永不返回）
testWidgets('保存 → 真实出图 1080 宽 + 提示条含路径', (tester) async {
  late File out;
  await tester.runAsync(() async {
    await tester.tap(find.byKey(const ValueKey('sc-save')));
    for (var i = 0; i < 60; i++) {
      await tester.pump(const Duration(milliseconds: 30));
      await Future<void>.delayed(const Duration(milliseconds: 30));
      final f = File(...);
      if (f.existsSync() && f.lengthSync() > 1000) { out = f; break; }
    }
  });
  expect(out.lengthSync(), greaterThan(1000));
  // 读 PNG IHDR 断言宽度 == 1080；再断言提示条文本含真实路径且不含「模拟」
});
```

## 9. 验收用例清单

- [ ] 出图后**读 PNG 头断言宽度 == 目标宽**（不是只断言"文件存在且非空"）。
- [ ] 断言提示条文本含**真实路径**；全仓 `grep` 无"（模拟）/已保存到相册"残留。
- [ ] 忘记包 `RepaintBoundary` 时：返回 null + 有 warn 日志（防"静默失败"）。
- [ ] 文件名净化用例：含 `/:*?"<>|` 的标题不炸、不穿越。
- [ ] 文字版用例：`〔段名〕` 结构与卡片可见文本一致（同源断言）。
- [ ] 站点回归：每个接入点断言"点了确实出图"，并核旧版假回执文案已从源码消失。
- [ ] 隐私开关态下出图：敏感字段仍遮蔽（C7）。
- [ ] 无祖先变换：用例里断言预览层不含 `FittedBox`（或改测宽度）。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| 出图宽度不是 1080（本项目实测 1364） | 边界链上有祖先变换（`FittedBox` 缩放）；栅格化按变换后的物理尺寸算 |
| 用例断言 `existsSync()` 为 false，但手工操作能出图 | 用 FakeAsync 跑 tap 链：引擎栅格回调排在真实事件循环里 → 整个 tap+轮询放 `tester.runAsync`，并 `pump(30ms)` 与 `Future.delayed(30ms)` 交错 |
| `existsSync()` true 但文件 0 字节 / 断图 | `writeAsBytes` 的中间态；轮询条件改成 `lengthSync() > 1000` |
| 提示条断言永不命中 | toast 文本含动态路径，用 `find.text` 精确匹配必然失败 → 用 `find.textContaining` 轮询 |
| mock 了 `flutter/platform` 整通道后测试炸 `FormatException` | Title/SystemSound 等消息也走同一通道，整通道接管会把它们喂给你的 mock → **别 mock 整通道**，改断言 UI 结果 |
| `Uint8List` 未定义 | 删 `flutter/services` import 后需 `import 'dart:typed_data';` |
| 弹层里 toast 看不见 | 模态路由压在页面 SnackBar 之上（C6）：先关弹层再挂 messenger |
| 分享卡把已隐藏金额露出来 | 出图走的是"原始 model"，没沿用页面隐私开关（C7） |
| 中文/带空格标题写出怪文件 | 未做 `sanitizeShareName`（C3） |

## 11. 复用清单

1. 任何 App 内"生成分享图/报表图/凭证图并落盘"的场景（Flutter 直接抄，原生同理：`View.draw → Bitmap → 文件`）。
2. 需要"提示条只说真话"这条行为规范的落地样板：源码级扫描假回执文案 + 用例断言真实路径。
3. widget 测试里做真异步 I/O 的交错 pump 手法，与 `mobile/flutter-test-hang-vm-forensics.md`
   和 `mobile/sqflite-versioned-migration-ffi-testing.md` 的 FFI/异步坑互为补充。
