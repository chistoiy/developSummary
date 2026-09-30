# Flutter Android 包体归因与压缩判定（可复用）

| 项 | 值 |
|---|---|
| 标签 | `mobile` `Flutter` `Android` `包体` `构建` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness 0.2.3+4035（arm64 分 ABI 包）实测 · `docs/APK包体压缩可行性-20260930.md` |
| 参考实现 | 第 4 节归因脚本；第 8 节 R8 试开配置与放行规则 |
| 适配成本 | 低（一条 `unzip -l` 就能起步） |

> 解决什么问题：被问「安装包能不能再小一点」，直觉是开 R8 / 上资源混淆，
> 花两天做完发现只动 1%，还引入一堆 keep 规则维护成本。
> 这篇给出**先归因、再决定动不动手**的判定流程，以及哪些手段在 Flutter 上注定无效。
> 不适用：iOS `.ipa`；也**不适用**指望 AAB / Play 动态分发——自分发 APK 的项目用不了那套机制。

---

## 0. 一句话定义

把 APK 拆成「Dart 产物 · 引擎 · 三方原生库 · dex · 资源与模型」五类并算占比，
据此判断每个压缩手段**作用在哪一条上、天花板是多少**，天花板低的直接否掉。

## 1. 术语

| 术语 | 说明 |
|---|---|
| `libapp.so` | 你的 Dart 代码 + 三方 Dart 包的 AOT 机器码。**构建配置动不了它**（除删代码/减依赖） |
| `libflutter.so` | Flutter 引擎本体，随 SDK 版本定死，动它需要自编译裁剪版引擎 |
| dex | Java/Kotlin 侧字节码，**R8/ProGuard 唯一能作用的地方** |
| 按功能定价 | 把某个三方 SDK 带来的体积（原生库 + 模型资产）换算成「这个功能值多少 MB」，交给产品决策 |

## 2. 需求规则（编号即验收项）

- **R1 先归因再决策。** 产出「分类占比表」之前，任何压缩方案都不许开工。
  理由：Flutter Android 包里 **60~80% 是 `libapp.so` + `libflutter.so`**，
  这两类构建配置碰不到；不知道这点就去开 R8，属于对 10% 的部分做优化。
- **R2 明确标出「动不了」的大头**，并写进文档。它同时是「天花板」：
  所有打包技巧的合计收益 ≤ 剩余那部分。
- **R3 每个手段必须对齐它作用的条目**：
  R8/ProGuard → 只管 `classes*.dex`；资源缩减 → 只管 `res/`；
  图标字体 tree-shaking → 只管字体；`--split-per-abi` → 消除多 ABI 冗余。
  写不出「它减哪一条」的手段，就是无效手段。
- **R4 开 R8 前先算上限**：`dex 占比 × 可减比例`。经验值：Flutter 项目 dex 常在 8~12%，
  而其中大半是引擎 embedding 与三方 SDK 的胶水（大量 keep 规则），实际可减通常 **< 3%**。
  上限低于 5% 就别为它承担 keep 规则的长期维护成本。
- **R5 三方 SDK 按功能定价**：把「原生 so + 模型资产 + 资源」合并成一个数，标上它服务的功能。
  决策权交给产品，工程师不要在暗地里砍功能或暗地里留着。
- **R6 结论必须有同提交前后对比数**（同一台机、同一 ABI、只改一个开关），
  负收益就明确写「不采纳 + 为什么」，否则半年后又有人试一遍。
- **R7 已经吃掉的默认收益不要再当方案提**：`--split-per-abi`、图标 tree-shaking
  （构建日志里会直接报「−98%」）、release 模式本身。提之前先查构建日志有没有报过。
- **R8 产物字节可复现**：同一提交本地重跑的包与已发布包字节数一致。
  所以核对「线上这枚是不是这个提交出的」用**字节数 + `aapt2 dump badging`**，不要用 mtime 猜
  （打包/发版流程见 `engineering/flutter-android-release.md`）。

## 3. 分类账本（示例：35.4 MB · arm64 分 ABI 包）

| 类别 | 条目 | 占比 | 可否动 |
|---|---|---|---|
| Dart AOT | `lib/arm64-v8a/libapp.so` 16.9 MB | 42.6% | ✗ 只能靠删代码/减依赖 |
| 引擎 | `lib/arm64-v8a/libflutter.so` 11.1 MB | 28.0% | ✗ 除非自编译引擎 |
| 三方原生库 | ML Kit 扫码内核 4.9 MB | 12.5% | △ 按功能定价（R5） |
| Java 侧 | `classes.dex` 3.8 MB | 9.5% | △ R8 只管这块（R4） |
| 模型资产 | 3 个 `.tflite` 0.88 MB | 2.2% | △ 随扫码来 |
| 胶水/清单/资源 | `libdartjni.so`、`NOTICES.Z`、`resources.arsc`、字体、其余数百条 | 5.2% | ✗ 许可清单不能摘 |

**这份账本直接给出结论**：可谈的只有那 14.7%（三方 SDK + dex），
而其中「真正值得拿出来讨论的」是扫码那一套 ≈ **15%**，属功能取舍，不属打包技巧。

## 4. 关键流程 / 脚本

```bash
APK=build/app/outputs/flutter-apk/app-arm64-v8a-release.apk
ls -l "$APK"                                  # 总量（对照发布说明里的数字）
unzip -l "$APK" | sort -rn | head -20         # 前 20 大条目，一眼看出类别
# 归类口径：
#   lib/*/libapp.so       → Dart AOT          （动不了）
#   lib/*/libflutter.so   → 引擎              （动不了）
#   lib/*/<其它>.so       → 三方原生库        （按功能定价）
#   classes*.dex          → Java/Kotlin       （R8 的地盘）
#   assets/**             → 模型与资源        （看是谁带进来的）
# 想知道某个 .so / 模型是谁带进来的：在依赖锁文件里搜 SDK，或临时摘掉它重跑归因表
```

## 7. 扩展点

- **E1** 把归因表做成发版流程的固定产物（每次发版顺手生成，跨版本能看体积趋势）。
- **E2** 引入新三方 SDK 前先记录它带来的 so/模型体积——**引入容易摘除难**，事前一行数字最便宜。
- **E3** 若真要摘 `libflutter.so`，方向是自编译裁剪版引擎（关掉不用的 embedder 特性），成本高，需单独立项。

## 8. 实现骨架（R8 试开·用于证伪）

```kotlin
// android/app/build.gradle.kts
android {
  buildTypes {
    release {
      isMinifyEnabled = true
      isShrinkResources = true
      proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
    }
  }
}
```

```proguard
# android/app/proguard-rules.pro —— ML Kit 依赖 Play Core，不放行会编译期报 12 条 Missing class
-dontwarn com.google.android.play.core.**
```

```bash
# 前后对比（同一提交·同一 ABI·只改这一个开关）
unzip -l app-arm64-v8a-release.apk | grep -E "classes.*dex|libapp|libflutter"
```

本项目实测：**dex 由 3.59 MB 涨到约 4.20 MB，包体反增**（同提交对照，实验工作树事后已回收，
结论方向可按上面步骤一键复现）。根因：dex 里大半是 Flutter embedding 与 ML Kit 的反射入口，
keep 规则一加，能删的没多少，反而多出 mapping 与桩开销，而占包体七成的 `.so` 它完全碰不到。
**处置：release 不开 R8。**

## 9. 验收用例清单

- [ ] 有分类占比表，且明确标出「构建配置动不了」的类别（R1/R2）
- [ ] 每个候选压缩手段标注了它作用的具体条目（R3）
- [ ] 开 R8 前算过 dex 上限，写清是否值得（R4）
- [ ] 三方 SDK 体积按功能定价，并交给产品一句话决策（R5）
- [ ] 采纳/不采纳都有同提交前后对比数字（R6）
- [ ] 方案清单里没有重复提「默认已生效」的收益（R7）
- [ ] 发布说明写明推荐安装哪一枚 ABI，且产物可用字节数/`aapt2` 核对（R8）

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| 开了 R8 包反而更大 | Flutter embedding + ML Kit 大量 keep 规则；能删的少，mapping/桩开销反而多（见第 8 节实测） |
| R8 编译期 12 条 `Missing class com.google.android.play.core.*` | ML Kit 依赖 Play Core，需 `-dontwarn`（R8 试开的必经一步，别当成"配置坏了"） |
| 顺手开 full mode 后运行时崩 | R8 full mode 语义更严，反射/序列化入口需要更多 keep；收益不抵风险 |
| 以为 `shrinkResources` 能缩原生库 | 它只处理 `res/`，`lib/**.so` 完全不动（违反 R3） |
| 「只发一枚包所以更小」发成了通用包 | 不带 `--split-per-abi` 的包内含全部 ABI，反而更大（R7 + `engineering/flutter-android-release.md` R2） |
| 拿 AAB 的 Play 动态分发当解 | 自分发 APK 用不了 Feature Delivery，那 15% 的模型还是在你包里 |
| 把 `NOTICES.Z` 当噪音删 | 开源许可清单不能摘 |
| 比体积时拿不同 ABI/不同 versionCode 的包互比 | 没固定变量（R6）；正确做法：同一提交、同一 ABI、只改一个开关 |
| 「线上这枚包是不是这个提交出的」靠 mtime 猜 | 同提交重跑字节可复现，比字节数即可（R8） |

## 11. 复用清单

1. 把第 4 节脚本贴进发版流程，每次出包顺手产一张归因表（成本一分钟，能挡掉所有"凭直觉压缩"）。
2. 判断「还能不能压」时，先说"动不了的那类占多少"，再谈手段——这句话能省掉几天无效工作。
3. 三方 SDK 的体积账（so + 模型 + 资源）单独列一行，交给产品看，不要混在"技术债"里。
4. 配套阅读：`engineering/flutter-android-release.md`（打包与产物核对）、
   `engineering/performance-ceiling-first.md`（先定上限再优化的通用方法学，本篇是它在包体上的实例）。
