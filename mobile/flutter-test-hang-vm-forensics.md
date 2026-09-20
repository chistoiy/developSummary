# Flutter 测试/运行挂死的 VM Service 现场取证法（可复用）

| 项 | 值 |
|---|---|
| 标签 | `mobile` `Flutter` `Dart` `调试` `VM Service` `内存炸弹` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness（`test/calendar_test.dart` test7 弹层挂死事故，2026-09-20） |
| 参考实现 | `flutter test --start-paused` + Node `--experimental-websocket` JSON-RPC 取证脚本（vmhist/vmwho/vmalloc 三件套） |
| 适配成本 | 低（任何 Dart/Flutter 项目通用；只需 PATH 上有 Node ≥ 22） |

> 一句话：当 Flutter widget 测试（或真机 isolate）**挂死不动、CPU 满核、内存无界增长、`--timeout` 也杀不掉**时，用 `--start-paused` 暴露 VM Service，以 JSON-RPC `pause → getStack → getAllocationProfile` 在"案发现场"直接采样调用栈与堆画像，几分钟内锁定分配源头——而不是靠删代码二分猜。
> 不适用：进程外问题（Gradle/adb/网络挂起）、纯逻辑死锁（CPU 0%、无分配）——后者 `getStack` 依然有用，但堆画像无信号。

---

## 0. 一句话定义

把 VM Service 当成"活体解剖台"：挂起的 isolate 不会说谎，栈顶循环出现的帧 + 堆里膨胀的类型直方图 = 根因坐标。

## 1. 术语

| 术语 | 说明 |
|---|---|
| 内存炸弹 | 同帧同步无限循环持续分配（如无界 `list.add`），单 isolate 吃满内存/CPU，永不 yield 事件循环，故 `--timeout` 无效 |
| `--start-paused` | `flutter test` 启动时挂住并打印 VM Service ws URL，等待 JSON-RPC 接管 |
| 栈帧直方图 | 多次 pause 采样调用栈，按"函数名+行号"计数；死循环的帧会以压倒性频次出现 |
| interrupt 现场 | `pause` 抓到的栈是信号打断点，**局部变量值可能是帧入口快照而非当前值**——但帧本身的位置是真实的 |

## 2. 需求规则（编号即验收项）

- **R1** 任何测试/运行"卡住超过预期 3 倍时长"，先采集**进程级证据**（CPU%、WorkingSet 增速）判定是"炸弹"还是"死锁"，再选手段。
- **R2** 炸弹类（CPU 满 + 内存秒级增长）一律走 VM Service 取证，**第一手栈证据优先于一切假设推演**。
- **R3** 栈里出现 `_GrowableList._grow` / 分配密集帧 / 同一业务帧高频重复 = 实锤该帧在循环分配，直接读那一行源码；**禁止**用"快照伪影"解释掉它（见坑表）。
- **R4** 定位后必须把根因转成**守护测试**（静态扫描规则）固化，防同型 bug 复发。

## 3. 状态机 / 数据模型

取证会话状态：`test --start-paused` → ws 连接 → `getVM`（取 isolate id）→ `resume`（让程序跑到炸弹中段）→ 定时 `pause` → `getStack(asyncFrames:true)` → 聚合直方图 → `getObject(帧 vars)` / `getAllocationProfile` 看具体集合长度 → 结论。

## 4. 关键算法或流程

1. 后台启动：`flutter test <file> --concurrency=1 --start-paused --reporter expanded`，从日志抓 `ws://127.0.0.1:<port>/...`。
2. Node 裸 WebSocket（零依赖，Node ≥ 22 `--experimental-websocket`）发 JSON-RPC：
   - `getVM` → `isolates[0].id`
   - `resume` 后 sleep 10~20s → `pause` → `getStack`
   - 对每帧 `getObject` 展开 `vars`，重点看局部 List 的 `length` 字段（炸弹现场会是六位数起步）。
3. 多轮 pause/getStack 采样（≥5 次），按 `function.name + location.line` 计数排序，Top-1 即循环体。
4. `getAllocationProfile` 看堆类型直方图交叉验证（`String`/`_GrowableList` 占比异常）。

## 5. 与数据层的边界规则

- **D1** 杀 flutter_tester 进程后，Windows 下必须清 `build/native_assets`，否则下次运行 sqlite3.dll 复制报 errno 183。
- **D2** heap 已达 GB 级时 `getObject` 可能超时，把 RPC 超时调到 3 分钟或先 `getStack`（栈小、快）。

## 6. 性能规则

- **P1** 采样循环本身别太密（5~20s 一次 pause），pause 期间 isolate 冻结会拖慢现场。
- **P2** 全量测试收口时并行跑一个内存监视器（轮询 `flutter_tester` WorkingSet，超阈值如 3GB 直接杀），把"等超时"变成"秒级熔断"。

## 7. 扩展点

- **E1** 同法适用于 `flutter run --start-paused` 的真机/模拟器 debug 进程。
- **E2** 直方图脚本可升级为火焰图（连续采样聚合）。

## 8. 实现骨架

```js
// tools/vmhist.mjs — 栈帧直方图（Node ≥22，零依赖）
const ws = new WebSocket(process.argv[2]);       // ws://... 来自 --start-paused 日志
const rpc = (m, p = {}) => { /* id 自增, pending Map, onmessage 派发 */ };
await open(ws);
const iso = (await rpc('getVM')).isolates[0].id;
await rpc('resume', { isolateId: iso });
const hist = {};
for (let i = 0; i < 6; i++) {
  await sleep(3000);
  await rpc('pause', { isolateId: iso });
  const st = await rpc('getStack', { isolateId: iso, asyncFrames: true });
  for (const f of st.frames ?? []) {
    const k = `${f.function?.name}:${f.location?.line ?? '?'}`;
    hist[k] = (hist[k] ?? 0) + 1;
  }
  await rpc('resume', { isolateId: iso });
}
console.table(Object.entries(hist).sort((a, b) => b[1] - a[1]).slice(0, 10));
```

根因修复的配套守护（R4）——扫描全部 `for(init;cond;inc)`，inc 为裸标识符/字面量即失败：

```dart
// test/architecture_test.dart (R4 核心判定)
final parts = splitThreeSections(forHeader);     // 括号按深度切 3 段
if (parts.length != 3) continue;                 // for-in / while 风格跳过
final inc = parts[2].trim();
if (RegExp(r'^[A-Za-z_$][\w$]*$').hasMatch(inc) || RegExp(r'^[0-9]+$').hasMatch(inc))
  violations.add('$rel:$line  `for (...; ...; $inc)`');
```

## 9. 验收用例清单

- [ ] 对一个已知死循环用例（如 `for (var h = 0; h < 24; h)` 漏 `++` 的集合推导）跑取证，Top-1 帧命中该行。
- [ ] 直方图 6 轮采样内出结论（< 2 分钟），快于任何二分。
- [ ] 守护测试对"inc 有副作用"的正常循环零误报、对裸标识符必报。
- [ ] 杀 tester 后 `build/native_assets` 清理路径写入项目脚本/记忆。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| `--timeout` 到点不杀 | 主 isolate 同步死循环永不 yield，测试框架的定时器根本不触发 |
| 栈里局部变量看着"不可能"（如 `d=1` 却在上限 31 的循环里） | pause 是 interrupt，vars 可能是**帧入口快照**；但高频帧位置本身是真信号——**别用它反推否定栈** |
| 二分删代码定位 3 小时未果 | 无证据驱动；每次"占位也炸"其实是因为替换文本里仍保留着炸弹表达式（items 计算式本身） |
| Dart 集合推导 `for (var h = 0; h < 24; h)` 不报编译错 | `h` 单作表达式是合法 no-op，空增量子句**静默**死循环 → 构建列表一律 `List.generate`（结构上不可表示该 bug） |
| Windows 重跑测试 errno 183 | 杀 flutter_tester 后 `build/native_assets/sqlite3.dll` 残留锁定 |
| `flutter test --plain-name` 传 `-N` 报未知参数 | 该版 test runner 无 `-N` 缩写 |

## 11. 复用清单

1. Flutter/Dart widget 测试挂死、CI 无日志黑盒。
2. 真机某页面"打开即卡死/OOM"（`flutter run --start-paused` 同法）。
3. 任何 isolate 内 CPU 100% + 内存线性增长问题的根因定位。
4. 搭 `architecture_test` R4 型守护，把事故固化成 CI 规则（配合 `engineering/module-decoupling-architecture-guard.md`）。
