# 模块解耦的可执行守护：import 架构测试（可复用）

| 项 | 值 |
|---|---|
| 标签 | `engineering` `架构` `守护测试` `Flutter` `任意语言可迁移` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness（`test/architecture_test.dart`、《开发计划书》§2.1） |
| 参考实现 | `collectImports()`（import 扫描）+ R1/R2/R3 三断言 + 白名单 |
| 适配成本 | 低（~100 行，改规则常量即可） |

> 把"模块不许互相 import"从口头约定变成**测试**：每次 `flutter test`/CI 扫描全部源码 import，
> 违规即红。专治"代码量上来之后改 A 崩 B、微调引发跨模块回归、测试漏测"。
> 不适用：需要分析类型级依赖（用了哪些类而非哪些文件）的场景——那要自定义 lint 或 dependency_validator 级别工具。

---

## 0. 一句话定义

一个纯读文件、解析 import 语句、按目录规则断言依赖方向的单元测试；规则、白名单、违例信息全部写死在测试里，评审时看 diff 即看架构决策。

## 1. 术语

| 术语 | 说明 |
|---|---|
| 组合根 | 允许"装配一切"的入口文件（main/app/router/DI 装配处），依赖汇聚的合法终点 |
| 聚合层 | 唯一允许 import 各 feature 的公共目录（如 `shared/linkage/`），承载跨模块查询与全局 UI |
| 守护测试 | 断言"代码结构"而非"代码行为"的测试 |

## 2. 需求规则（编号即验收项）

- **R1 feature 间零直连**：`features/<A>/**` 不得 import `features/<B>/**`。跨模块取数→聚合层 provider；跨模块通知→事件总线。*为什么：直连让"微调 A 弄坏 B"成为常态，且 B 的测试覆盖不到 A 的变更。*
- **R2 core 不依赖 feature**：core 是业务无关底座；反向依赖使它不可复用、不可独立测试。
- **R3 feature 的 domain/data 不得 import presentation**：domain/data 是对外稳定契约（也是未来插件化/后移的切割线），必须脱离 UI 可测。
- **R4 白名单显式化**：组合根文件逐个列名，新增必须改测试（=强制过一次架构评审）。
- **R5 守护自身要防"假绿"**：附一条"确实扫到了 N 个文件"的自校验断言。*为什么：路径写错会让规则静默空转，全绿但什么都没守护。*

## 3. 状态机 / 数据模型

```
imports: Map<lib相对路径, List<lib相对路径>>   // 一次收集，多规则复用
规则 = (源路径谓词) × (禁止的目标前缀) × (白名单集)
```

## 4. 关键算法或流程

1. 递归 `lib/**/*.dart`；正则取 `import '<uri>'`。
2. 三类 uri 归一为 lib 相对路径：`package:<本包>/x` → `x`；相对路径 → `normalize(dirname+uri)`（越出 lib 的丢弃）；`dart:`/外部包 → 忽略。
3. 逐规则收集违例字符串，`expect(violations, isEmpty, reason: 人话解释+该走什么替代方案)`。*reason 是给人看的，写清"怎么改对"。*

## 5. 与数据层的边界规则

- **D1** 违例处理只有两种：改代码走聚合层/事件，或（评审通过后）登记白名单——**永不放宽规则本身来凑绿**。
- **D2** 全局 UI（悬浮条、角标）归聚合层文件，由组合根挂载；不许 feature 直接把 widget 塞进 shell。

## 6. 性能规则

- **P1** 扫描在测试期完成（百级文件毫秒量），不进运行时。

## 7. 扩展点

- **E1** 加规则：循环依赖检测、domain 禁 import Flutter（`package:flutter/(material|widgets|cupertino)`）。
- **E2** 迁移到其他栈：同一算法适用于 TS（import from）、Dart、Go（module path）、Kotlin（package import）。

## 8. 实现骨架

```dart
test('R1 features 不得互相 import', () {
  final violations = <String>[];
  for (final e in imports.entries) {
    final mod = e.key.split('/').first;
    if (!e.key.startsWith('features/')) continue;
    for (final imp in e.value.where((i) => i.startsWith('features/'))) {
      if (imp.split('/').first != mod) violations.add('${e.key} → $imp');
    }
  }
  expect(violations, isEmpty, reason: '跨模块请经 shared/linkage 聚合或事件总线');
});
// R2 core→features 同理，白名单 {main.dart, app.dart, core/providers.dart, core/router/app_router.dart}
// R3 domain/data→presentation 同理；R5 自校验 expect(imports.length, greaterThan(20));
```

## 9. 验收用例清单

- [ ] 当前代码全绿（治理后的基线）。
- [ ] 手动加一条违规 import → 对应测试立刻红，reason 可读。
- [ ] 故意把 lib 路径写错 → R5 自校验断言变红（防假绿）。
- [ ] 白名单文件从规则说明中能找到出处。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| 守护全绿但明显有违规没抓到 | 只处理了 `package:` 形式漏了相对 import（本项目大量用 `../../`）；或扫描根路径写错——R5 自校验兜底 |
| Windows 路径分隔符 `\` 导致前缀匹配失效 | 统一 `p.posix.joinAll(p.split(...))` 归一再比较 |
| export 语句绕过检测 | 初版只扫 import；聚合层用 `export` 转发是**设计内**行为（它本来就允许 import feature），feature 侧仍禁直连，无需扫 export |
| doc 注释里的 `<mod>` 触发 unintended_html lint | 注释尖括号加反引号 |

## 11. 复用清单

1. 抄 `architecture_test.dart`，改三样：目录名（features/shared/core → 你的）、白名单、规则前缀。
2. 在开发规范里写明"违例=测试失败=不许合入"，并把 reason 文案当 code review checklist。
3. 先治理存量违规（改道聚合层），再上守护，否则第一天就红没人愿意维护。
