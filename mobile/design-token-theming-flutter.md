# CSS 设计令牌驱动的 Flutter 多主题换肤引擎（可复用）

| 项 | 值 |
|---|---|
| 标签 | `mobile` `Flutter` `主题` `设计令牌` `热换肤` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness（`lib/core/theme/`、`assets/themes/*.json`、`prototype/themes/`） |
| 参考实现 | `ThemeTokens`（解析器）、`ThemeRepository.loadAll()`（装载）、`AppTheme.build()`（装配）、`AppTokens extends ThemeExtension`（注入） |
| 适配成本 | 低（令牌 schema 若已有 Web 原型可直接共用同一批 json） |

> 用与 Web 原型**同构的 CSS 变量 json** 作为唯一主题源，运行时解析成 Flutter `ThemeData`，
> 实现"7 套风格 × 深色模式正交、实时切换、缺键回落、坏文件不崩"。
> 不适用：主题需要运行时从远程拉取并签名校验的场景（本方案只覆盖内置 assets，扩展点见 E1）。

---

## 0. 一句话定义

设计令牌 json（`--accent`/`--page`/`--card-r`/`--card-sh`…）→ 启动期一次性解析为内存注册表 → `ThemeExtension` 注入 widget 树 → 切主题只是换一个 Map 查找 + `setState`。

## 1. 术语

| 术语 | 说明 |
|---|---|
| 令牌 token | 一组 CSS 变量键值，如 `"--accent": "#6366f1"` |
| theme.json | 一个主题的配置文件：`{id, meta, forceDark?, tokens:{亮态}, dark:{深态覆盖}}` |
| 回落 fallback | 缺失的键取默认主题（简约）的同名值，允许"只改强调色"的轻量主题 |
| forceDark | 该主题无视用户浅色设置强制深色（如赛博朋克） |

## 2. 需求规则（编号即验收项）

- **R1 单一事实源**：主题值只允许存在于 json，Dart 代码里不写死任何主题色（默认值除外）。*为什么：写死后新主题必须改代码，热加载失去意义。*
- **R2 与深色模式正交**：`tokens`=亮态、`dark`=深态覆盖，深色开关与主题选择互不枚举。*为什么：否则 N 套主题 ×2 深色 = 2N 组合爆炸。*
- **R3 缺键回落**：任何键缺失时回落默认主题同名键，再回落内置常量；加载永不抛异常。*为什么：主题市场允许只写 3 个键的微型主题；坏文件不能白屏。*
- **R4 启动期同步切换**：所有主题在 `main()` 前一次性 `rootBundle.loadString` 解析入注册表，运行期切换为纯同步查表。*为什么：避免切主题时异步加载闪烁/竞态。*
- **R5 页面取色只经扩展**：业务侧统一 `context.tokens.card` / `context.cardDecoration`，禁止 `Theme.of(context).colorScheme.xxx` 猜主题色。*为什么：Material colorScheme 是派生物，自定义组件需要原始令牌。*

## 3. 状态机 / 数据模型

```
ThemeTokens（解析结果，不可变）
  id, meta, forceDark
  accent, accentLight, g1/g2/g3(渐变三停点), page, card, cardBorder
  cardRadius(double), cardShadow(List<BoxShadow>), navBg, navBorder, appFont?
ThemeBundle = { id, meta, forceDark, light: ThemeTokens, dark: ThemeTokens }
ThemeRegistry = Map<String, ThemeBundle> + defaultId
AppTokens extends ThemeExtension<AppTokens> { tokens }   // 注入 ThemeData.extensions
```

不变式：`registry.of(任意id)` 永远返回可用 bundle（找不到→默认）。

## 4. 关键算法或流程

**CSS 值解析器**（`ThemeTokens.parseColor/parseLength/parseBoxShadow`）：

1. 颜色：`#rgb` / `#rrggbb` / `#rrggbbaa`（**注意 CSS 是 RGBA 序，Flutter Color 是 ARGB，8 位时须 `((aa<<24)|rrggbb)` 重排**）/ `rgb()` / `rgba()`（alpha 支持 `.85` 前导点小数）/ `transparent`。
2. 圆角：取串首数字（`"20px"`→20）。
3. 阴影：先**按括号深度切顶层逗号**（`rgba(15,23,42,.05)` 内部逗号不是分隔符！），每段再"先摘颜色 token、剩余数字按 offsetX offsetY blur [spread] 归位"。
4. 深态合并：`merged = {...默认主题亮态, ...本主题tokens}` 得 light；`{...默认亮态, ...本主题dark}` 得 dark（R3）。

**装配**：`AppTheme.build(tokens, isDark)` → `ColorScheme.fromSeed(seedColor: accent)` + 覆盖 scaffoldBackground/cardTheme(圆角+描边)/appBarTheme，并挂 `extensions:[AppTokens(tokens)]`。

**forceDark**：`appThemeModeProvider` 里 `bundle.forceDark ? ThemeMode.dark : 用户mode`。

## 5. 与数据层的边界规则

- **D1** 当前主题 id、深浅偏好存设置存储（本项目 shared_preferences，正式版进 settings 表随备份走）；主题 json 本身只读。
- **D2** assets 目录须在 pubspec `flutter: assets:` 注册；json 清单文件**不要以 `_` 或 `.` 开头**（`_index.json` 在 flutter test 的 unit_test_assets 快照中不可见，改名 `index.json` 解决）。

## 6. 性能规则

- **P1** 启动期一次解析 N 套（本项目 7 套 <10ms），运行期切换零 IO。
- **P2** 依赖主题的 provider 只 watch `themeId`（`select`），避免任意设置变更全量重建。

## 7. 扩展点

- **E1 远程主题市场**：注册表加 `merge(List<theme.json>)` 入口，拉取→SHA-256/minisign 校验→注入缓存目录，UI 零改动。
- **E2 组件级皮肤**：`AppTokens` 可再挂派生 getter（如 `chipDecoration`），保持 R5 单出口。

## 8. 实现骨架

```dart
// 解析
class ThemeTokens {
  static Color? parseColor(String s) { /* #rgb|#rrggbb|#rrggbbaa→ARGB重排|rgba(...) */ }
  static List<BoxShadow> parseBoxShadow(String css) { /* 括号感知切分→逐段 */ }
  factory ThemeTokens.fromMap(id, raw, {base}) => /* 每键 raw→base→内置常量 三级回落 */;
}
// 装载（main 前）
final registry = await ThemeRepository().loadAll(); // 读 assets/themes/index.json 遍历
// 注入
ThemeData build(ThemeTokens t, {required bool isDark}) => base.copyWith(
  extensions: [AppTokens(tokens: t)], scaffoldBackgroundColor: t.page, ...);
// 使用
extension AppTokensContext on BuildContext {
  ThemeTokens get tokens => Theme.of(this).extension<AppTokens>()?.tokens
      ?? AppTokens.fallback.tokens;   // 兜底：独立 MaterialApp 测试/预览不崩
  BoxDecoration get cardDecoration => /* card+radius+border+shadow 统一卡片 */;
}
```

## 9. 验收用例清单

- [ ] 7 套主题逐一点击切换，AppBar/卡片/底栏/渐变全部随变，无重启。
- [ ] 任一主题 × 深色开/关 = 14 组合截图正确（正交）。
- [ ] 删掉某 json 的一个键 → 回落默认值不崩；删整个文件 → 该主题消失其余正常。
- [ ] forceDark 主题下"跟随系统/浅色"均呈深色。
- [ ] 8 位 hex（`#rrggbbaa`）与 `rgba(...,.85)` 渲染色值与浏览器 CSS 一致。
- [ ] 双阴影（`8px 8px 18px #a,-8px -8px 18px #fff`）解析出 2 个 BoxShadow。
- [ ] 独立 `MaterialApp`（未挂扩展）中使用 `context.tokens` 不抛异常（兜底生效）。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| 半透明色渲染成不透明/色序错 | CSS `#rrggbbaa` 与 Flutter `0xAARRGGBB` 字节序相反，8 位 hex 必须重排 |
| 多阴影只解析出 1 条 | 直接 `split(',')` 把 `rgba(1,2,3,.5)` 内部逗号也切了，须括号深度感知 |
| 主题切换后部分页面不变 | 页面用了 `Theme.of().colorScheme` 派生色或缓存了旧 tokens；统一走 `context.tokens`（R5） |
| flutter test 里 asset 读不到而"静默用默认值" | 纯 `test()` 无 binding，rootBundle 不可用：需 `TestWidgetsFlutterBinding.ensureInitialized()`；改文件名后 `build/unit_test_assets` 快照不刷新，删该目录重建；`_`开头文件名可能不进清单，改无前导下划线 |
| 独立 widget 测试 `extension<AppTokens>()!` 崩 | 测试自建 MaterialApp 无扩展；getter 必须 `?? fallback` 兜底 |

## 11. 复用清单

1. 复制 `theme_tokens.dart / theme_repository.dart / app_theme.dart` 三件 + `assets/themes/` 目录与 `index.json`。
2. pubspec 注册 assets；`main()` 里 loadAll 后 override 进 DI 容器。
3. 按你的组件补 `cardDecoration` 之类的派生 getter。
4. 过一遍 §9 验收清单。
