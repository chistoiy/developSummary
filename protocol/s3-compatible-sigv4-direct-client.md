# S3 兼容网盘直连（手写 SigV4）与「脚本 200 / App 4xx」二分排障法

| 项 | 值 |
|---|---|
| 标签 | `protocol` `S3` `WebDAV` `SigV4` `排查` |
| 成熟度 | ✅ 已落地并验证（真云双协议全链绿） |
| 来源项目 | LifeHarness（`d:\dev_workplace\flutter_te\lifeHarness`）`lib/core/sync/` V1.41 批3~批4 + R2~R6 真机反馈五轮 |
| 参考实现 | `s3_sigv4.dart`（签名器）· `s3_store.dart`（对象操作）· `webdav_store.dart`（坚果云）· `tools/cloud_verify.js`（零密钥真云验证脚本） |
| 适配成本 | 低（签名器/脚本可直接抄；协议层类需换成自己的配置模型） |

> 解决：App 直连 S3 兼容私有云（如缤纷云 bitiful）与 WebDAV（坚果云）时，
> 拿到 400/403/409 这类「服务端一句话、不告诉你哪错了」的回包如何定位。
> 不适用：接 AWS 官方 SDK 的场景（SDK 已封装本文档全部规则）。

---

## 0. 一句话定义

手写 SigV4 客户端的三条铁律（canonical=wire 一致、签名头集完整、scope 段序规范）
+ 一套「零密钥真云验证脚本 → 同代码探针复现 → 固定时间戳签名逐字节比对」的二分排障流水线。

## 1. 术语

| 术语 | 说明 |
|---|---|
| canonical request | SigV4 规范请求串（method/URI/query/headers/signedHeaders/payloadHash） |
| credentialScope | `date/region/service/aws4_request` 四段，进 stringToSign 也进 Authorization 头 |
| wire URI | 实际发出去的 URL，必须与 canonical 逐字节一致 |
| CopyObject | S3 服务端复制（`PUT` + `x-amz-copy-source` 头），用于「归档移动」 |

## 2. 需求规则（编号即验收项）

- **R1** canonical URI/query 与实际请求 URL 逐字节一致：用「逻辑组件 → toWireUri 唯一入口」构造，
  签名前校验 `url.path==canonicalUri && url.query==canonicalQuery`，不一致本地拒发（宁可抛错也不让服务端回误导性 403）。
- **R2** 签名头集必须覆盖所有 aws 头且按字典序：`host;x-amz-content-sha256;x-amz-copy-source;x-amz-date`
  ——CopyObject 的 `x-amz-copy-source` **漏签必 403 SignatureDoesNotMatch**（实测）。
- **R3** credentialScope 段序 = `date/region/service/aws4_request`（AWS 规范）。
  写反成 `date/service/region` 时：部分兼容云宽松放行、严格节点对 ListObjectsV2 回
  **400 InvalidRequest**（无细节）、ListAllMyBuckets 回 **403 AccessDenied**——同一 App 时好时坏即此症状。
- **R4** payloadHash 三态：GET/DELETE=SHA256("")；流式大文件 PUT=`UNSIGNED-PAYLOAD`（缤纷云接受）；
  CopyObject PUT=SHA256("")（空体）。
- **R5** 凭据验证脚本零密钥入库：密钥只从环境变量 / gitignored env 文件读，**用完即删**；
  脚本输出「探针编号 + HTTP 状态 + 回包前 200 字节」，供 App 报 4xx 时对照。
- **R6** 云端清单列表缓存（60s TTL 类）必须在 put/delete 成功后**立即作废本服务缓存**，
  否则「同步成功后差异检测仍说云端没有」假差异（实测踩坑）。
- **R7** 坚果云 WebDAV：put 前无条件预建整条父链（根级文件也要先 MKCOL 根集合，否则 409
  AncestorsNotFound）；MOVE 目标父目录不存在回 **404**（不是 409），409/404 都要「补建父目录→重试一次」。
- **R8** 退避重发必须重建请求+重签（日期进签名；流式体只读一遍）。

## 3. 状态机 / 数据模型

无独立状态机。配置模型：`ServiceConfig{provider, apiUrl, account, region, bucket, cloudDirName}` +
凭据独立走系统保险箱（键 `sync_secret_{id}`），配置与密钥永不混存、不入备份包。

## 4. 关键算法或流程

**二分排障流水线（R6 实战，30 分钟锁定 scope 段序写反）**：

1. `node tools/cloud_verify.js`（App 同款探针口径）→ 若 200 而 App 4xx：差异在 App 请求，继续；
   若脚本也 4xx：云端权限/配置问题，到此为止。
2. 用**被测 App 的同一份签名代码**写一次性 dart 探针直连 → 复现 400 ⇒ 锁定在签名/请求构造。
3. 加/减 Dart 特有头部（UA/accept-encoding/content-length:0）二分 ⇒ 全 200 ⇒ 排除传输层。
4. **固定同一时间戳**：node 签一次、dart 签一次，逐字节比对 Authorization 与 canonical ⇒
   当场看出 scope 段序差异（`20260925/s3/cn-east-1` vs `20260925/cn-east-1/s3`）。
5. 修复后用 App 签名器再跑三探针（verify / list 带 prefix / 列桶）全 200 收工。

**证据样例**（缤纷云 s3.bitiful.net · cn-east-1 · virtual-hosted）：

```
GET https://<bucket>.s3.bitiful.net/?list-type=2&max-keys=1
scope 写反 → 400 <?xml..><Error><Code>InvalidRequest</Code><Message>Invalid Request</Message>..
scope 规范 → 200 <ListBucketResult ...>
GET https://s3.bitiful.net/  (ListAllMyBuckets)
scope 写反 → 403 AccessDenied.   scope 规范 → 200
```

## 5. 与数据层的边界规则

签名器纯函数无 IO；网络一律经「唯一请求出口」（节流+退避），守护测试禁止其他文件直连 http 客户端。

## 6. 性能规则

列举递归分页（continuation-token）+ 每服务清单缓存（TTL 60s，写操作即作废）；附件按内容哈希命名做
文件级增量（同内容全局去重、历史文件永不重传）。

## 7. 扩展点

- 换任意 S3 兼容端点：只改 apiUrl/region/bucket；path-style 时 bucket 置空即回落裸 host。
- 「归档不删除」类云端清理：CopyObject→`media_archive/` 前缀 + 源键 DELETE（两断，失败可重放）。

## 8. 实现骨架

```dart
// 签名（三钉：R1 校验、R2 头集、R3 段序）
final scope = '$dateStamp/$region/$service/aws4_request';
final canonicalHeaders = 'host:$host\n'
    'x-amz-content-sha256:$payloadHash\n'
    '${copySource != null ? 'x-amz-copy-source:$copySource\n' : ''}'
    'x-amz-date:$amzDate\n';
final signedHeaders = copySource == null
    ? 'host;x-amz-content-sha256;x-amz-date'
    : 'host;x-amz-content-sha256;x-amz-copy-source;x-amz-date';
```

```js
// 零密钥验证脚本骨架（env 读取 → 探针数组 → 打印状态+回包摘要 → env 用完即删）
const AK = process.env.LH_S3_AK || readGitignoredEnv('LH_S3_AK');
```

## 9. 验收用例清单

- [ ] canonical≠wire 时本地抛错拒发（不发出畸形签名请求）。
- [ ] CopyObject 签名头集含 `x-amz-copy-source` 且字典序位置正确（钉死 SignedHeaders 字符串）。
- [ ] credentialScope 期望值由**独立实现**（node crypto / AWS 官方向量）复算，禁止被测代码自产期望值。
- [ ] put/delete 后清单缓存作废（模拟：写成功→立即 list 应重新发请求）。
- [ ] 坚果云：根级 put 先建根集合；MOVE 409 与 404 均触发补建重试。
- [ ] 真云直连脚本在 CI 外手动跑通：verify/list/put/copy/delete/MOVE 全 2xx。

## 10. 已知坑

- 缤纷云子账户默认无 ListAllMyBuckets 全局权限：403 应**优雅降级**（容量显示未知 + 进程级一次 warn），
  不得让同步链报错——但若你修完 scope 后仍 403，才是真权限问题（两者症状相同，先查 scope）。
- 「同步成功但验证 400」不是矛盾：不同前端节点宽松度不同，宽松节点放行畸形签名=潜伏隐患，
  换网络/换时间随机复发。
- Windows 下 dart 与 flutter test 并发跑会互踩原生库文件锁，串行执行。

## 11. 复用清单

抄：`s3_sigv4.dart`（签名器，含 canonical 构造纯函数与一致性校验）、`tools/cloud_verify.js`
（零密钥双云验证）、本文 R1~R8 规则与 §9 验收清单。改：配置模型、保险箱键名、缓存 TTL。
