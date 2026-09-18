# 单通道设备的请求调度（可复用）

| 项 | 值 |
|---|---|
| 标签 | `mobile` `架构` `调度` `Flutter/Dart` `硬件通信` |
| 成熟度 | ✅ 已落地（几千条目的相册在单条命令通道上稳定加载） |
| 来源项目 | NikonSync（`lib/engine/camera_gateway.dart` 的调度核心） |
| 参考实现 | `CameraGateway.schedule()` / `_pump()` / `needsLoad()` / 失败计数 |
| 适配成本 | 低：把"操作闭包"换掉即可；与具体协议无关 |

> 一句话：当**同一时刻只能干一件事**时（相机命令通道、蓝牙、串口、单连接 API），
> 所有请求必须串行化，并加上"优先级 + 同键去重 + 失败止损"三件事，
> 否则会出现请求风暴、相互打断、以及卡死。

**适用场景**：蓝牙 BLE、USB/串口设备、PTP/IP 这类单会话协议、
有并发限制的第三方 API、限流的服务端接口。
**不适用场景**：后端本身支持并发且无状态（那时直接并发即可）。

---

## 1. 术语

| 术语 | 说明 |
|---|---|
| **单通道** | 同一时刻只能处理一个请求的资源 |
| **优先级队列** | 用户当前看得见的请求插队，后台任务让路 |
| **同键去重** | 同一个资源的重复请求只保留一个在飞 |
| **负缓存** | 反复失败的资源记次数，到上限后不再重试 |

## 2. 规则（编号即验收项）

- **R1** 所有对单通道的访问**必须走同一个串行入口**，不能有任何一处绕过它直接发请求。
- **R2** 两级优先级：**用户可见优先**（当前屏幕要显示的缩略图/详情）> **后台任务**（预取、索引、同步）。
  每次取任务时先看高优先队列，空了才取低优先。
- **R3** 串行执行用"取一个 → 跑完 → 再取下一个"，**不要**用 `Future.wait` 并发。
- **R4** 同键去重：同一个资源正在加载时，后续请求**直接复用**同一个 Future（或直接跳过），
  不要重复排队。
- **R5** UI 侧先问"还需不需要加载"（`needsLoad`），再决定要不要排队——
  避免每个单元格每次重建都排一个注定什么都不做的回调。
- **R6** 失败**负缓存**：同一资源连续失败到上限（来源项目 3 次）后不再重试。
  用计数而不是一次性放弃：瞬时失败（设备忙）值得重试，反复失败的才止损。
- **R7** 数据源刷新（重新枚举/重连）后**清空失败计数**，给这些资源一次完整重试机会。
- **R8** 请求在飞的集合（`pending`）必须在完成/失败时**无条件移除**，
  否则该资源会被永久跳过。
- **R9** 调度器不吞异常：失败要通过 `Future.error` 传给调用方，由调用方决定重试或提示。

## 3. 实现骨架

```dart
final Queue<_Job> _hi = Queue();
final Queue<_Job> _lo = Queue();
bool _running = false;
final Set<int> _pendingInfo = {};        // R4 在飞去重
final Map<int, int> _infoFailCnt = {};   // R6 负缓存
static const int _maxRetry = 3;

/// R1 唯一入口
Future<T> schedule<T>(Future<T> Function() op, {bool priority = false}) {
  final c = Completer<T>();
  final job = _Job(() async {
    try { c.complete(await op()); }        // R9 不吞异常
    catch (e, st) { c.completeError(e, st); }
  });
  (priority ? _hi : _lo).addLast(job);     // R2 优先级
  _pump();
  return c.future;
}

/// R3 严格串行
void _pump() {
  if (_running) return;
  final q = _hi.isNotEmpty ? _hi : _lo;
  if (q.isEmpty) return;
  _running = true;
  q.removeFirst().run().whenComplete(() {
    _running = false;
    _pump();                               // 跑完再取下一个
  });
}

/// R5 UI 侧先判断值不值得排队
bool needsLoad(Item f) =>
    !f.loaded && (_infoFailCnt[f.key] ?? 0) < _maxRetry;

bool ensureLoaded(Item f) {
  if (!needsLoad(f)) return false;
  if (!_pendingInfo.add(f.key)) return false;   // R4 已在飞就跳过
  schedule(() async {
    f.apply(await api.fetch(f.key));
  }, priority: true).catchError((_) {}).whenComplete(() {
    _pendingInfo.remove(f.key);                  // R8 无条件释放
    if (!f.loaded) _countFail(f.key);            // R6 计一次失败
  });
  return true;
}
```

## 4. 与 UI 的配合

- **U1** 单元格在 `build` 里调用 `ensureLoaded()`，返回值决定是否显示占位动画。
- **U2** 加载成功的回调要**节流通知** UI（来源项目 150ms 节流），
  否则几千个条目逐个完成 → 每完成一个重建一次整页。
- **U3** 列表刷新后调用 `resetFailures()`（R7），并清理在飞集合里已失效的键。
- **U4** 页面销毁/断开连接时，要能放弃队列里还没跑的任务（避免给已关闭的通道发请求）。
- **U5** 正在跑的任务若要支持取消，**取消请求不能进这个队列**——会排在它后面。
  见 [长任务的取消](long-task-cancellation.md)。

## 5. 验收用例清单

- [ ] 快速滚动列表 → 请求一个一个跑，日志里没有交错/并发
- [ ] 可见区域的条目**优先**于后台预取拿到结果
- [ ] 连续触发同一资源的加载 10 次 → 实际只发了一次请求
- [ ] 故意让某资源一直失败 → 达到上限后不再重试，且不影响其它资源
- [ ] 重新枚举/重连后，之前失败过的资源能够重新加载
- [ ] 请求失败时 UI 能收到错误（不是被静默吞掉）
- [ ] 断开连接后，队列里残留的任务不会打到已关闭的通道上
- [ ] 几千条目的列表滚动时 UI 不卡（配合 U2 的节流）

## 6. 已知坑

| 现象 | 根因 |
|---|---|
| 设备/接口报"忙"、响应错乱 | 有请求绕过了串行入口（R1） |
| 滚动时缩略图半天不出来 | 后台任务（索引/同步）把队列占满了（R2） |
| 单元格每次重建都发请求 → 请求风暴 | 没有在飞去重与 `needsLoad` 预判（R4/R5） |
| 某个条目永远加载不出来 | `pending` 没有在失败分支释放（R8） |
| 反复失败的资源一直重试，拖慢整体 | 没有负缓存（R6） |
| 重连后失败的条目仍然加载不了 | 失败计数没清空（R7） |
| 请求失败但 UI 一直转圈 | 调度器吞掉了异常（R9） |
| 页面关闭后仍收到"连接已关闭"的报错 | 没有在销毁时放弃队列（U4） |

## 7. 复用清单

1. 确认你的资源确实是"一次只能一个"（否则不该串行化）。
2. 定出优先级判据：通常是"当前用户看得到的"优先。
3. 定出去重键（资源 id）与失败上限（3 次是经验起点）。
4. 接上 UI 侧的 `needsLoad` 与节流通知。
5. 别忘了销毁时清空队列——这一步最容易漏。
