# 云盘直连：坚果云 WebDAV 与缤纷云 S3 的接入规则（可复用）

| 项 | 值 |
|---|---|
| 标签 | `protocol` `flutter/dart` `WebDAV` `S3` `坚果云` `缤纷云` `限流` |
| 成熟度 | ✅ 已落地并验证（StoreRider 1.2.0 双协议；坚果云真机对接，缤纷云实测跑通 5 类请求） |
| 来源项目 | StoreRider / webDevRider —— `lib/services/`（协议层与差异引擎） |
| 参考实现 | `remote_store.dart`（抽象）、`s3_store.dart`、`s3_sigv4.dart`、`webdav_transfer.dart`、`quota_service.dart`、`diff_service.dart`、`http_gate.dart` |
| 适配成本 | 中 —— 协议层两份实现 + 一个接口要重写；差异引擎、编排、UI、限流闸门与协议无关，可整块搬 |

> 解决的是「手机端直连云盘做目录增量同步」这一类需求：**两家服务商给的协议根本不是一回事**
> （一家只给 WebDAV，一家只给 S3 且明确没有 WebDAV），既要能接，又不能让协议分支渗进业务代码。
> 也适用于任何「一个 App 对接多个异构远端存储」的场景（对象存储 / NAS / 网盘）。
>
> **不适用**：只做单文件上传下载（直接用服务商 SDK 更省事）；需要在线预览、分享链接、CDN 直链
> （那是另一套预签名与防盗链模型，本文只在扩展点里标了入口）；需要「删除也同步过去」的镜像语义
> （必须先解决「云端多出来的文件是别人传的还是有历史」的判定，见 R13 与已知坑最后一条）。

---

## 0. 一句话定义

用一层 `RemoteStore`（list / put / get / ensureDirectory / verify / quota）把 WebDAV 与 S3 两套
协议挡在业务之外，再用**唯一请求出口**统一做「按服务节流 + 429/503 退避」，
使差异引擎与 UI 只认两份 `FileEntry` 列表，不认协议。

## 1. 术语

| 术语 | 说明 |
|---|---|
| WebDAV 根 | 服务给的 DAV 入口，坚果云固定 `https://dav.jianguoyun.com/dav/`，同步文件夹就在它下面一层 |
| 应用密码 | 坚果云第三方专用口令，**不是登录密码**；账户 = 注册邮箱 + 这把应用密码，走 `Basic` |
| endpoint | S3 接入地址（缤纷云 `https://s3.bitiful.net`），含 scheme |
| virtual-hosted | 桶寻址形态 `https://{bucket}.{endpoint}/{key}`，缤纷云官方 demo 明确 `forcePathStyle: false` |
| ListObjectsV2 | S3 列举接口，`prefix` + `continuation-token` 翻页，无 Delimiter 即递归整棵树 |
| SigV4 | S3 请求签名：两个 HMAC-SHA256 + SHA256，日期与 region 参与签名 |
| 强 / 弱 ETag | 弱 ETag 带 `W/` 前缀；分片上传的对象 ETag 形如 `md5-7`，**不是内容 MD5** |
| 应用层配额 | RFC4331 的 `quota-available-bytes` / `quota-used-bytes`，标准 PROPFIND 属性 |
| 频控 | 服务端对「短时间高频请求」的限制，两家都有，形态不同：坚果云按账号、缤纷云按请求计费 |
| 清单缓存 | 同一目录 60s 内复用已拉取的远端文件列表，避免重复列举 |

## 2. 需求规则（编号即验收项）

### 协议与选型

- **R1 先探测协议，再谈接入。** 判断依据不是"看起来像对象存储"，而是官方文档全文检索
  `webdav|PROPFIND|MKCOL` 是否零命中 + 对 WebDAV 根做 `PROPFIND Depth:0` 看返回。
  缤纷云两条都是否：它只有 S3 / 预签名 / CDN 直链 + 一个只回用量日志的运维 API。
  **为什么**：接一家「其实不支持该协议」的服务商，会在联调中期变成整套协议栈的返工。
- **R2 一个接口两个实现，业务层零协议分支。** `RemoteStore` 六个方法见 §8；
  差异引擎、同步编排、通知栏、页面全部只消费 `FileEntry` 列表。
  任何 `if (isS3)` 出现在协议层之外都算破口（只有 `ServiceConfig.isS3` 决定表单字段与 `RemoteStore.forConfig` 的分发）。
- **R3 凭据形态按协议分开表述。** WebDAV = 账户 + 应用密码；S3 = 子账户 AK/SK + region + bucket。
  同一组字段名复用（`account` / `authCode`），但界面上的标签随协议切换（「账户/应用密码」↔「Access Key/Secret Key」），
  连接验证的提示文案也要跟着换——否则用户以为填错了。
- **R4 加 provider 字段必须让旧数据原样可读。** 反序列化时缺 `provider`/`region`/`bucket`/节奏字段一律落回
  WebDAV 默认值，并留一条单测固定「1.1.x 的配置文件升级后照常工作」。

### 协议差异要点

- **R5 寻址与路径。** S3 用 virtual-hosted；但 `ListAllMyBuckets`（唯一能拿缤纷云用量的口）**只能打在根 endpoint**，
  带桶前缀的 host 拿不到。key 的分段编码要和签名侧完全一致（见 R6）。
- **R6 列举策略按协议定，别照抄。** WebDAV 只能 `PROPFIND Depth:1` 逐层递归 = `1+D` 次请求（D 为目录层数，实现里限深 10 层）；
  S3 必须用**一次递归 ListObjectsV2 + prefix**，每 1000 个对象 1 次请求 = `⌈N/1000⌉`。
  **为什么**：S3 侧按请求次数计费且有 List QPS 上限，逐层展开等于把账单和 429 一起点上。
- **R7 目录语义。** S3 是扁平键空间：`ensureDirectory` 空实现，key 里带 `/` 自然成目录；
  列举响应里 `Key` 以 `/` 结尾的目录占位对象**不计入文件**。
  WebDAV 传输前必须逐级 `MKCOL`，`201/204/405` 都算成功（405 = 已存在），且**同一次运行内记住已建过的目录**，
  别为同一个目录重复发请求。
- **R8 一致性判定分三档，默认最保守那档。** ① 只比字节数（默认）；② 两端都有强 ETag 时比 ETag；
  ③ 仅当「服务端 ETag 就是内容 MD5」且用户显式开启严格校验时，才逐文件算本地 MD5 比对。
  开启 ③ 的前置探测：远端清单里存在 32 位十六进制的 ETag，否则算了也白算（分片对象的 `xxx-7` 不匹配）。
  **为什么**：PUT 之后云端记录的是写入时间而不是源文件 mtime，用 mtime 兜底会把每个刚同步过的文件判成「已修改」。
- **R9 容量是三态，不是一态。** ① 有总配额（坚果云 `storage_quota`）；② 只有已用量（按量付费的对象存储，桶不设上限）；
  ③ 服务端什么都不给。UI 必须能只渲染「已用 22.6 KB」而没有进度条。
  数据模型上 `quotaBytes`/`usedBytes` 都允许 null，缺哪块显示哪块。
- **R10 所有云端请求只能经唯一出口发出。** 出口做两件事：按**所属服务**的最小间隔排队（默认 300ms，档位 150/300/800），
  以及撞上 `429/503` 时退避重发（优先听 `Retry-After`，支持秒数与 HTTP-date 两种形式；没有就 `2s→4s→8s`；
  上限截断 30s，避免任务看起来卡死；最多 3 次）。
  **重发必须重建请求对象**——流式请求体只能读一遍，而 SigV4 把日期算进签名，退避几秒后复用旧请求必然签名过期。
- **R11 凭据单独进加密存储，明文配置与导出文件里不留。** Android 上落到 Keystore 支撑的
  `flutter_secure_storage`；**只有写进保险箱成功才清掉明文**，保险箱异常时保留原值并记日志，
  绝不能把服务变成「没有凭据」。导出的备份 JSON 不含凭据（备份落在公共目录），导入时提示重填并在界面上标「待填凭据」。
- **R12 响应一律 `utf8.decode(bodyBytes)`。** S3/坚果云的 XML 恒定 UTF-8，用 `response.body` 会按响应头猜编码，
  中文文件名变乱码后才想起来查就晚了。

### 使用侧（面向配置的人）

- **R13 坚果云要在网页端先开第三方访问并生成应用密码**：登录网页版 → 账户信息 → 安全选项 →
  「添加应用密码」；App 里填**注册邮箱 + 应用密码**，填登录密码会稳定返回 401/403。
  要同步的文件夹必须在坚果云客户端里开启同步（WebDAV 根下只露出这些文件夹）。
- **R14 缤纷云要走子账户**：控制台建子账户 → 为其创建 Access Key/Secret Key → **手动分配桶权限**。
  没有「应用密码」概念，也没有根密码直连；主账户密钥不该放进 App。
  权限模型只认子账户策略——实测 `GET /?acl` 返回空标签，别指望标准 ACL。

## 3. 状态机 / 数据模型

```dart
enum StoreProvider { webdav, s3 }

class ServiceConfig {          // 一条云盘配置
  StoreProvider provider;      // 决定协议分发与表单字段
  String apiUrl;               // WebDAV 根 / S3 endpoint（含 scheme）
  String account;              // WebDAV 账户邮箱 / S3 Access Key
  String authCode;             // WebDAV 应用密码 / S3 Secret Key —— 只存加密存储
  String region;               // 仅 S3，缤纷云目前只有 cn-east-1
  String bucket;               // 仅 S3，同时是 virtual-hosted 的域名前缀
  int requestIntervalMs;       // 频控口径是「账号 + 服务端」的属性 → 按服务配，不是全局配
  bool verifyContent;          // R8 第 ③ 档开关，默认关
}

class FileEntry { String relativePath; int size; String? etag; DateTime? modified; String? digest; }
class FileDiff  { String relativePath; DiffStatus status; FileEntry? local, remote; }
// DiffStatus: onlyLocal / onlyRemote / modified / inSync（+ 残留两态，见 D2）

class CloudQuota {             // R9 三态靠 null 表达
  int? quotaBytes; int? usedBytes;
  Map<String, int> collectionUsed;   // 坚果云 getUserInfo 才有：按同步文件夹给的用量
}
```

**不变式**

- 一条目录映射的身份 = `服务 id + 本地路径 + 云端目录名`（界面缓存、失败清单、同步台账、后台任务共用这个键）；
  云端目录名只在**同一服务下**要求唯一，跨服务商命名空间互相独立。
- 差异引擎的输入永远是两份已经过忽略规则过滤的 `FileEntry`，输出永远是 `List<FileDiff>`；它拿不到协议对象。

## 4. 关键算法或流程

### 4.1 坚果云（WebDAV 路线）

```
账户        = 注册邮箱；口令 = 应用密码（R13）→ Authorization: Basic base64(email:appPwd)
根地址      = https://dav.jianguoyun.com/dav/          （要同步的文件夹必须已被客户端同步）
列目录      = PROPFIND Depth:1 逐层递归（限深 10），每层 1 次请求，请求体固定四属性：
              resourcetype / getcontentlength / getetag / getlastmodified
建目录      = MKCOL 逐级，201/204/405 视为成功
容量        = GET {scheme}://{host}/nsdav/getUserInfo   ← 自有扩展，不是 RFC4331
              解析 user/storage_quota、user/used_storage、user/account_state、
                   user/collection[]{href, used_storage}
              collection 的 href 归一化（去尾斜杠 + 小写）后才能按目录名命中
为什么不用 RFC4331：实测坚果云对 allprop 只回
  getcontenttype/displayname/owner/resourcetype/getcontentlength/getlastmodified/current-user-privilege-set
  —— 没有 quota-*，PROPFIND 路线拿不到容量。
一致性      = getetag 是内容 MD5 → 严格校验档（R8③）在这家最有价值
频控        = 按账号限制短时间高频请求；数值以官方为准，本项目按 R10 的间隔 + 退避应对，未观测到明确错误码样本
```

### 4.2 缤纷云（S3 路线）

```
endpoint    = https://s3.bitiful.net（控制台 Bucket 设置页底部，含 scheme）
region      = cn-east-1（目前仅此一个）
寻址        = virtual-hosted：https://{bucket}.s3.bitiful.net/{key}
凭证        = 子账户 AK/SK + 手动分配桶权限（R14）；签名 Signature V4
列举        = GET /?list-type=2&prefix={dir}/&max-keys=1000[&continuation-token=…]
              不带 Delimiter = 一次拿整棵子树；翻页直到 IsTruncated=false（硬上限 200 页）
传输        = PUT/GET 单个 key，流式；Content-Length 必给
建目录      = 不需要（空实现）
容量        = GET https://s3.bitiful.net/  (ListAllMyBuckets)
              响应里除标准 Name/CreationDate 外，附带缤纷云私有字段：
                ImageBytes/TmpBytes/VideoBytes/AudioBytes/OtherBytes + *Files + Folders
              usedBytes = Σ(*Bytes)；**没有任何配额字段** → quotaBytes 恒为 null（R9 第 ② 态）
连接验证    = GET /?list-type=2&max-keys=1（200 即凭证与桶权限都通）
```

实测证据（2026-09-22，子账户 AK/SK + 手写 SigV4）：`HEAD /`、`GET /?location`、`GET /?list-type=2…`、
`GET /?acl`、`GET /?versioning` 全部 200；`?acl`/`?versioning` 内容为空标签。

**明确别用的接口形态**

| 想用 | 实测结果 | 结论 |
|---|---|---|
| `GET /{bucket}/?stat` 拿用量 | 返回的是 **ListObjects 结果**，不是 GetBucketStat | 该查询串未实现，别用；用量走 ListAllMyBuckets 私有字段 |
| `x-bitiful-limit-rate=1024` 限速 | 设 1024 实测约 6464 B/s | 数值不可信，限速只能在客户端做 |
| 官方 AWS Android SDK | 其 `Bucket` 模型只有 `name/owner/creationDate`（已核对源码） | 反序列化会丢掉私有 `*Bytes` → 接了 SDK 反而拿不到容量；且该线自 2026-08-01 停止支持 |
| 预签名 URL 做主链路 | 官方代签服务无 ListObjects，且要求自架常驻服务器 | 只适合将来的分享/CDN 场景 |

静态文档值（未向服务端验证，只能当预算写死）：每桶 List QPS 200、GET 5000、PUT 1000；
S4 请求 0–10 万次/月免费、超出 0.02 元/万次、每天免费上限 1 万次、最小计费单位千次；每账号 10 桶。

### 4.3 SigV4 要点（手写，两个 HMAC + SHA256 即够）

```
credentialScope = {yyyyMMdd}/s3/{region}/aws4_request     // 缤纷云的 service 段就是 s3
canonicalRequest = METHOD\nCanonicalURI\nCanonicalQueryString\nCanonicalHeaders\nSignedHeaders\n{PayloadHash}
PayloadHash 三态：
  空体 GET  → SHA256("")
  流式 PUT  → UNSIGNED-PAYLOAD（流式上传时算不出实体摘要，且缤纷云接受）
  其他      → 实体 SHA256
CanonicalQueryString 必须按 key 排序、键值各自 percent-encode；
path 的分段编码要与实际请求 URL 一致（每段 uriEncode(encodeSlash:false) 再拼 '/'）——
  两处编码不一致是 403 SignatureDoesNotMatch 的头号成因。
时间戳用 UTC；设备时钟漂超过 15 分钟会被判过期（Signature 日期参与计算）。
```

## 5. 与数据层的边界规则

- **D1 配置与凭据分家。** 明文 prefs 存 `ServiceConfig`（`authCode` 字段写空串占位），
  真实凭据按 `service_secret_{id}` 之类的键进加密存储；删服务时同步清理孤儿键。
- **D2 「云端多出来一个文件」有两种相反含义**：别人新传的（该下载）与本地删过的残留（该删）。
  只看两侧清单无法区分。所以必须有一份「本目录同步成功过哪些相对路径」的台账，
  残留 = 台账有 + 本地无 + 云端仍在；**台账为空的目录不报残留**（第一次见到某目录时云端一切都是未知而非多余）。
  没有台账就别提供镜像删除——换机后会一键删光云端。
- **D3 缓存键必须带服务 id。** 清单缓存、容量缓存、差异缓存、失败清单都用 `{serviceId}|…` 前缀；
  删服务或改映射时按前缀失效，否则换桶后仍读到旧桶的清单。
- **D4 导出/导入是配置层的事，凭据不在导出范围内**（R11）。导入要先校验再落盘：
  坏条目跳过并计数、缺凭据单独提示，并让用户在「覆盖 / 合并」之间选（合并按服务 id 与三元组映射键去重）。
- **D5 忽略规则在比对前把两侧一起剔除。** 只剔本地会让云端的 `.DS_Store` 变成「仅云端」误报。

## 6. 性能规则

- **P1 请求数才是成本。** 差异检测 = 1 次列举 + 1 次本地扫描；一轮同步里同目录会被反复问到时，
  靠 60s 清单缓存吸收；容量 5min 缓存；`MKCOL` 结果进程内记忆。
- **P2 单任务串行。** 同一时刻只允许一个同步任务，多目录排队跑——并发打头阵的是限流而不是加速。
- **P3 摘要按块读。** 严格校验要算全文件 MD5，用 64KB 分块 `RandomAccessFile`，别 `readAsBytes` 整个视频。
- **P4 传输全程流式。** 上传 `StreamedRequest` + `addStream(file.openRead())`，下载 `response.stream.pipe(sink)`；
  喂数据放到独立 Future 里，避免与 `client.send()` 互相等待死锁。
- **P5 超时分档。** 列举/查询 20–30s，传输 10min。用同一个超时会让大文件被误杀。
- **P6 节流间隔按服务配。** 频控是「账号 + 服务端」的属性：坚果云按账号、缤纷云按请求计费，
  所以阈值放服务上（150/300/800ms 三档），不是全局常量。

## 7. 扩展点

- **E1 换一家 S3 源**（阿里云 OSS / 腾讯云 COS / MinIO / Cloudflare R2）：只改 `endpoint`/`region`/`bucket`，
  S3 实现类零改动。个别源要另注意：Cloudflare R2 公开桶可走免签 GET；OSS 的 ETag 不保证是 MD5。
- **E2 新协议**：实现 `RemoteStore` 六方法 + 在 `forConfig` 里加一个分支即可，差异引擎与 UI 不用动。
- **E3 分享 / CDN 直链**：预签名器与 header 签名共用同一套 canonical 构造（约 30 行增量），
  顺带能白捡「限次下载链接」（`x-bitiful-max-requests` 实测精确生效）。
- **E4 账号健康提示**：坚果云 `getUserInfo` 的 `account_state` 字段目前只解析未使用，可用来提示空间满 / 账号异常。
- **E5 镜像删除**：D2 的台账攒够之后再开，直接删还是加 `.deleted` 前缀要一起定。

## 8. 实现骨架

```dart
/// 协议抽象：差异引擎与编排只依赖它。
abstract class RemoteStore {
  static RemoteStore forConfig(ServiceConfig c) => c.isS3 ? S3Store() : WebDavStore();

  Future<CloudListing> list(ServiceConfig c, String cloudDirName, {bool force = false});
  Future<void> put(ServiceConfig c, String localPath, String cloudRelative);
  Future<void> get(ServiceConfig c, String cloudRelative, String localPath);
  Future<void> ensureDirectory(ServiceConfig c, String cloudRelativeDir); // S3: 空实现
  Future<VerifyResult> verify(ServiceConfig c);                            // 1 次极轻请求
  Future<CloudQuota> quota(ServiceConfig c, {bool force = false});         // 三态，null 即未提供
  void close();
}

/// 唯一请求出口（R10）。
class HttpGate {
  static const _throttled = {429, 503};
  static const maxBackoff = Duration(seconds: 30);

  static Future<http.StreamedResponse> send(
    http.Client client, http.BaseRequest Function() build, {
    required ServiceConfig config, required String label, int maxAttempts = 3,
  }) async {
    http.StreamedResponse? res;
    for (var attempt = 0; attempt < maxAttempts; attempt++) {
      await Throttle.wait(config);              // 按 config 的节奏排队，全局串行
      res = await client.send(build());         // 每次重建请求：流式体只读一遍 + 签名含日期
      final wait = waitFor(res!, attempt, DateTime.now());
      if (wait == null) return res;
      log('$label 被限流（${res.statusCode}），等待 ${wait.inSeconds}s 后重发');
      await Future<void>.delayed(wait);
    }
    return res!;                                // 用尽次数：把最后的响应交回调用方
  }

  static Duration? waitFor(http.BaseResponse r, int attempt, DateTime now) {
    if (!_throttled.contains(r.statusCode)) return null;
    final w = retryAfterOf(r, now) ?? Duration(seconds: 2 << attempt);
    return w > maxBackoff ? maxBackoff : w;
  }
}

/// 一致性判定（R8）：两份清单进，差异出，不碰网络。
static bool same(FileEntry l, FileEntry r) {
  if (l.size != r.size) return false;
  final remote = normEtag(r.etag), local = normEtag(l.etag);
  if (l.digest != null && isMd5Hex(remote)) return l.digest == remote.toLowerCase(); // 严格校验档
  if (remote.isNotEmpty && local.isNotEmpty && !remote.startsWith('W/') && !local.startsWith('W/'))
    return remote == local;
  return true;                                  // 只比大小；绝不用 mtime 兜底
}

/// S3 递归列举（R6）：一次前缀 List，翻页到底。
Future<CloudListing> listAll(ServiceConfig c, String dir) async {
  final prefix = '${trim(dir)}/';
  final files = <FileEntry>[]; String? token;
  for (var page = 0; page < 200; page++) {
    final body = await get(c, url(c, query: {
      'list-type': '2', 'prefix': prefix, 'max-keys': '1000',
      if (token != null) 'continuation-token': token,
    }));
    final r = parseListObjects(body, prefix);   // Key 以 '/' 结尾的目录占位对象不计入
    files.addAll(r.files);
    if (!r.truncated || r.nextToken == null) return CloudListing(files);
    token = r.nextToken;
  }
  return CloudListing(files);
}
```

## 9. 验收用例清单

- [ ] 协议探测有证据：官方文档全文检索 + 对 WebDAV 根做一次 `PROPFIND Depth:0`，结论写进文档
- [ ] 业务层搜不到协议分支（`if (isS3)` 只允许出现在表单字段与 `forConfig`）
- [ ] 中文与带空格的文件名：列举、上传、下载三处都不乱码（R12 + key 编码）
- [ ] 100 层以内深目录：WebDAV 限深生效、S3 一次递归拿全
- [ ] 大文件（>1GB）上传下载内存平稳（P4 流式，不整读）
- [ ] 制造 `429/503`：日志里能看到「等待 Ns 后重发」，任务不整目录失败；`Retry-After` 两种格式都解析
- [ ] 重发路径被单测覆盖：请求工厂被调用 ≥2 次（签名日期必须重算）
- [ ] 旧版本配置升级后照常连接（R4 的向后兼容单测）
- [ ] 容量三态都有截图/断言：有配额（坚果云）、只有已用（缤纷云）、全 null
- [ ] 改服务端时间 ±20 分钟能复现 `SignatureDoesNotMatch`，说明签名链路真实生效
- [ ] 凭据不在明文配置、不在导出备份里；保险箱写失败时保留原值（不许把服务变成无凭据）
- [ ] 同服务下云端目录名唯一、跨服务允许同名；缓存键带服务 id（D3）
- [ ] 单测不真连公网：签名用固定向量、协议层用注入的假 store

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| 缤纷云填 WebDAV 地址怎么试都不通 | 它不提供 WebDAV（官方文档全文检索零命中），只有 S3 / 预签名 / CDN 直链 |
| `403 SignatureDoesNotMatch` | 规范请求与实际请求不一致，四类成因：path 分段编码不同、query 未按 key 排序、region/service 段写错、设备时钟漂移超 15 分钟（签名日期过期） |
| 退避重发后必失败，首次正常 | 复用了同一个请求对象：流式请求体只能读一遍，且 SigV4 把日期算进签名 → 必须用工厂重建 |
| 刚同步完的文件全被判成「已修改」 | 用 mtime 兜底；PUT 之后云端 `LastModified` 是服务端写入时间，不是源文件时间 |
| 开了严格校验反而误报 | 分片上传对象的 ETag 形如 `md5-7`，不是内容 MD5；只在 32 位十六进制时才比对摘要 |
| 中文文件名列表里变乱码 | 用 `response.body`，它按响应头猜编码；XML 响应恒定 UTF-8 |
| 坚果云 PROPFIND 问不到容量 | allprop 实测不含 `quota-*`；要用自有扩展 `GET /nsdav/getUserInfo` |
| 按目录名取「这个文件夹占用多少」取不到 | `collection[].href` 是 URL 编码带尾斜杠的路径，要先解码 + 去尾斜杠 + 小写归一化 |
| 缤纷云 `?stat` 返回一堆文件列表 | 该查询串未实现，服务端按 ListObjects 回答了；用量只能从 ListAllMyBuckets 的私有 `*Bytes` 求和 |
| 接了官方 AWS Android SDK 拿不到用量 | 其 `Bucket` 模型只有三个字段，反序列化直接丢弃私有字段；该 SDK 线已停止维护 |
| 目录一多就 429 / 请求账单暴涨 | WebDAV 逐层 `Depth:1` = `1+D` 次请求；S3 逐层同理更贵。要靠「一次递归 List」+ 清单缓存 + 串行排队 |
| 全局唯一云端目录名把第二家服务商挡住 | 命名空间本来就是每服务独立的；唯一性判定要按「服务 id + 云端目录名」，编辑既有映射时还要排除自己那条 |
| 坚果云填登录密码 401 | 第三方必须用「应用密码」（网页端 → 账户信息 → 安全选项 → 添加应用密码） |
| 导入备份换机后云端全空 | 备份不含凭据（R11 有意为之），需要在服务配置里重填一次；界面上要用「待填凭据」标出来 |
| 切到后台/另一个 isolate 后凭据读不到、日志静默丢 | 平台通道未注册（`DartPluginRegistrant.ensureInitialized()`）、文件日志未挂目录、忽略规则未加载——后台入口要自己把 `main()` 里那套初始化补齐 |
| widget 测试一调加密存储就超时不返回 | 真实的加密存储没有测试平台通道实现，调用永远等不到回复；测试里注入内存版保险箱 |
| 「本地删了云端也删」差点删光云端 | 没有同步台账时，`onlyRemote` 既可能是别人新传也可能是本地残留；换机/新目录场景下 100% 误判 |

## 11. 复用清单

1. `RemoteStore` 接口签名（§8）—— 业务层与协议层的唯一交界。
2. `HttpGate.send` + 退避判定（§8）—— 任何"按账号频控"的远端都能套，只需换 `config` 里的间隔。
3. 服务商参数速查（§4.1 / §4.2）—— 坚果云：`https://dav.jianguoyun.com/dav/` + 邮箱 + 应用密码 + `/nsdav/getUserInfo`；
   缤纷云：`https://s3.bitiful.net` + `cn-east-1` + virtual-hosted + 子账户 AK/SK + ListAllMyBuckets 取用量。
4. 一致性判定三档规则（R8）与 `same()` 片段。
5. 容量三态模型 `CloudQuota`（null 语义）与坚果云 / 缤纷云两条解析分支。
6. 目录映射三元组键（`服务|本地#云端`）与「同服务唯一」判重规则（§3 不变式）。
7. 验收清单 §9 可直接当代码评审 checklist。
8. 本文所有示例均为占位符，不含任何真实账号、Access Key、Secret Key、应用密码或桶名。
