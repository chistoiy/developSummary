# 应用内日志与错误可反馈设计（可复用）

| 项 | 值 |
|---|---|
| 标签 | `engineering` `Flutter` `可观测性` `崩溃` `用户反馈` |
| 成熟度 | ✅ 已落地（真机上靠它拿到过多次现场证据） |
| 来源项目 | NikonSync（`lib/engine/app_log.dart` + `lib/main.dart` 全局错误捕获 + 设置页复制入口） |
| 参考实现 | `AppLog`（环形缓冲 + 节流 + 防递归）、三处 `FlutterError` 捕获 |
| 适配成本 | 低：去掉日志来源（原生事件转发）即可用于任何 Flutter 项目 |

> 一句话：让"用户说 App 崩了"变成"用户把完整证据发给你"——
> 日志从启动就开始记、错误自动进日志、UI 只渲染窗口但复制是全量。

**适用场景**：任何交付给他人使用的 App（尤其是**外部拿不到日志**的场景：
真机、客户环境、无 ADB 的机器）。
**不适用场景**：纯内部工具、已有完备远程日志/APM 且能定位到用户的产品
（那时仍可保留，但重点是远程上报）。

---

## 1. 术语

| 术语 | 说明 |
|---|---|
| **环形缓冲** | 固定条数的内存日志，超出丢弃最旧的 |
| **节流通知** | 高频日志合并成一次 UI 通知，避免重建风暴 |
| **显示窗口** | UI 只渲染最近 N 条（渲染成本高），但复制时用全量 |
| **防递归** | 记错误本身又触发错误时的熔断 |
| **关键路径日志** | 同时写进系统日志（logcat）的少量日志 |

## 2. 规则（编号即验收项）

### 记录

- **R1** 日志从 **App 启动就记录**，不要求用户先打开某个页面（否则现场早就过了）。
- **R2** 用**固定条数的环形缓冲**（来源项目 300 条），超出丢最旧。
  不要无上限增长——长会话会 OOM。
- **R3** 每条带**毫秒级时间戳**（`HH:mm:ss.SSS`）。跨层问题（协议/UI）只有靠时间对齐才能看。
- **R4** 日志来源要包含**原生侧**（原生事件转发到 Dart 日志），
  否则"为什么界面没刷新"这类问题在 release 包里完全没有证据。

### 通知与渲染

- **R5** 高频日志（突发、重试、批量事件）必须**节流通知**（来源项目 300ms），
  否则每来一条就 `notifyListeners()` → UI 重建风暴。
- **R6** UI 只渲染**显示窗口**（来源项目 120 条），但**复制出来是全量**。
  理由：`SelectionArea`/长列表渲染成本高，而排查恰恰需要全量。
- **R7** 用独立的 `ValueNotifier` 作为版本信号，让只有日志面板订阅它，
  不要让它触发整页重建。

### 错误

- **R8** 全局错误捕获要覆盖**三处**（缺一处就有漏网的崩溃）：

  ```dart
  FlutterError.onError              // Flutter 框架内的构建/布局错误
  PlatformDispatcher.instance.onError // 异步未捕获异常
  ErrorWidget.builder               // 出错 widget 的占位（顺便记录）
  ```

- **R9** 错误上报要**防递归 + 限频**（来源项目：防递归标志 + 500ms 内合并）。
  否则会陷入"错误 → 记日志 → 通知 → 重建 → 再错误"的死循环。
- **R10** 堆栈要**截断**（来源项目取前 14 行）并压成单行，避免一条日志几十 KB。

### 给用户

- **R11** 提供**一键复制全部日志**（且复制的是全量，不是屏幕上的 120 条）。
- **R12** 错误发生时给用户一句**可行动的提示**（"详情见日志，设置页可复制"），
  而不是只弹一个红色界面。
- **R13** 关键路径日志同时进**系统日志**（logcat），但**不要全量镜像**——
  全量会把 logcat 缓冲冲掉，反而丢了真正需要的协议日志。

## 3. 实现骨架

```dart
class AppLog {
  AppLog._();
  static final List<({String time, String text})> lines = [];
  static final ValueNotifier<int> version = ValueNotifier(0);
  static const int _max = 300;            // R2 环形缓冲
  static const int displayWindow = 120;   // R6 只渲染窗口

  static Timer? _flushTimer;
  static DateTime? _lastErrorLog;
  static bool _reporting = false;

  static void add(String text) => _push(text);

  /// 关键路径：同时写 logcat（R13，只给关键路径用）
  static void addKey(String text) {
    _push(text);
    unawaited(NativeLog.i(text).catchError((_) {}));
  }

  /// 错误上报：防递归 + 500ms 限频（R9）
  static void reportError(String text) {
    if (_reporting) return;
    final now = DateTime.now();
    if (_lastErrorLog != null &&
        now.difference(_lastErrorLog!) < const Duration(milliseconds: 500)) return;
    _lastErrorLog = now;
    _reporting = true;
    try { _push(text); } finally { _reporting = false; }
  }

  static void _push(String text) {
    lines.add((time: _ts(DateTime.now()), text: text));
    if (lines.length > _max) lines.removeRange(0, lines.length - _max);
    // R5 节流：300ms 内的突发合并成一次通知
    _flushTimer ??= Timer(const Duration(milliseconds: 300), () {
      _flushTimer = null;
      version.value++;
    });
  }
}

// main.dart：三处全局捕获（R8）
FlutterError.onError = (d) {
  AppLog.reportError('‼️ FlutterError: ${d.exception}');
  AppLog.add(d.stack.toString().split('\n').take(14).join(' | ')); // R10 截断
  FlutterError.presentError(d);
};
PlatformDispatcher.instance.onError = (e, st) {
  AppLog.reportError('‼️ 未捕获异常: $e');
  AppLog.add(st.toString().split('\n').take(14).join(' | '));
  return true;
};
ErrorWidget.builder = (d) {
  AppLog.reportError('‼️ WidgetError: ${d.exception}');
  return Center(child: Text('页面局部错误（详情见日志）：${d.exception}'));
};
```

## 4. 验收用例清单

- [ ] App 一启动就开始记录（不依赖用户打开某个页面）
- [ ] 日志条数到上限后自动丢最旧，长时间运行内存不涨
- [ ] 每条都有毫秒时间戳
- [ ] 原生侧日志也进同一个缓冲（跨层问题能对齐时间）
- [ ] 制造一次日志突发（连续写 100 条）→ UI 只重建一次左右，不卡
- [ ] UI 只显示 120 条，但复制出来是 300 条
- [ ] 触发一个 Flutter 构建错误 → 日志里有 `FlutterError` 和堆栈
- [ ] 触发一个异步未捕获异常 → 日志里有 `未捕获异常` 和堆栈
- [ ] 连续触发 10 次错误 → 不会死循环，且日志没有被重复刷屏
- [ ] 设置页能一键复制全部日志，粘贴出来时间戳完整、不含 ANSI/控制字符
- [ ] 关键路径日志能在 logcat 里看到，且没有把 logcat 冲爆
- [ ] 真机（无 ADB）场景下，用户能独立完成"复制 → 发给你"

## 5. 已知坑

| 现象 | 根因 |
|---|---|
| 用户说崩了，但日志里什么都没有 | 日志要用户先打开某页才开始记（R1） |
| 记日志本身导致 UI 卡死 | 没有节流，每条都通知（R5） |
| 记错误时死循环/刷屏 | 没有防递归与限频（R9） |
| 日志面板翻到几百条就卡 | 长列表 + `SelectionArea` 全量渲染（R6） |
| 一条日志几十 KB，复制出来没法看 | 堆栈没截断（R10） |
| 复制出来只有屏幕上看得见的那点 | 复制用了显示窗口而不是全量（R6/R11） |
| 长会话后内存涨 | 缓冲无上限（R2） |
| 真机上"界面为什么没刷新"查不到 | 原生侧日志没进同一个缓冲（R4） |
| logcat 里协议日志被冲掉 | 全量日志镜像进系统日志（R13） |

## 6. 复用清单

1. 复制第 3 节的 `AppLog`，按你的场景调 `_max` / `displayWindow` / 节流时长。
2. 在 `main()` 里接上三处全局捕获。
3. 把原生侧日志转发进来（Android 用 EventChannel，iOS 同理）。
4. 在设置页放一个"复制全部日志"的按钮——**这是整个设计的落点**。
