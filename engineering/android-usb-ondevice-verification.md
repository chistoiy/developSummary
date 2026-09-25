# Flutter 真机 USB 联调取证与协作模式

| 项 | 值 |
|---|---|
| 标签 | `engineering` `Flutter` `Android` `adb` `真机联调` |
| 成熟度 | ✅ 已落地并验证（多轮真机反馈批实战） |
| 来源项目 | LifeHarness（`d:\dev_workplace\flutter_te\lifeHarness`）V1.41 R2~R6 真机联调 |
| 参考实现 | 《交接文档》第 6 节「真机联调固化经验」；`tools/` 下 aapt2 产物核对流程 |
| 适配成本 | 低（纯流程与判据，无代码依赖） |

> 解决：用 adb 在真机上装包、走查、取证时的三类高频陷阱（误装降级包 -25、
> 注入点击被 ROM 拦、判定取证失真）与对应的协作模式切换。
> 不适用：CI 上的模拟器自动化（无 ROM 权限层）。

---

## 0. 一句话定义

真机联调先立三条规矩：**包按 ABI 选对、注入被拦就切手动协作、判定只信落盘证据**。

## 1. 术语

| 术语 | 说明 |
|---|---|
| split-per-abi | 按 ABI 出多个瘦身 APK；versionCode 常带 ABI 偏移（如 +1000/+2000/+4000） |
| 安装 -25 | HyperOS/MIUI 报「无法降级安装」：目标包 versionCode 低于已装包 |
| USB 调试（安全设置） | 小米系 ROM 单独开关，控制 adb 能否模拟点击（普通 USB 调试不含） |

## 2. 需求规则（编号即验收项）

- **R1** 真机安装**先核对已装包 ABI 与 versionCode**（`adb shell dumpsys package <id> | grep version`），
  再选对应 ABI 的新包；arm64 手机装 v7a 偏移包必触发 -25。release 正文固定写「真机请下 arm64-v8a」。
- **R2** -25 时**绝不按系统建议卸载重装**（清用户数据）；改装正确 ABI 包覆盖安装即可。
- **R3** 首次使用设备先探测注入权限：`adb shell input tap` 抛 SecurityException ⇒ 请用户开
  「USB 调试（安全设置）」；开不了/找不到开关 ⇒ **立即切协作模式**：Agent 发逐屏指引，
  用户操作，Agent 拉 `screencap`+App 日志/logcat 核对。禁止反复重试 input tap。
- **R4** 坐标按**截图 PNG 实际像素**换算（`wm size` 与 Read 显示尺寸可能有缩放系数），先取
  PNG 头拿宽高再映射。
- **R5** 自动化走查期间明确操作权：先与用户约定「这段时间别碰手机」，避免人手与注入互踩；
  判定「是否写入成功」用 `run-as`/`exec-out` 转储 DB 等落盘证据，不连拍截图猜。
- **R6** 覆盖安装升级（同签名+更高 versionCode）数据无损，可用于带真实用户数据的复测；
  装完先跑一次数据敏感走查（如启动迁移统计行）再进功能项。

## 3. 状态机 / 数据模型

无。

## 4. 关键算法或流程

```
连上设备
 ├─ dumpsys 查已装 versionName/Code/ABI ── 选对应 ABI 新包（-25 预防）
 ├─ install -r（覆盖，数据保留）
 ├─ input tap 探测注入权限
 │    ├─ OK → 自动走查（约定操作权 + 落盘取证）
 │    └─ SecurityException → 请开「USB 调试(安全设置)」→ 仍不行 → 协作模式
 └─ 每步：screencap(按 PNG 实际像素映射坐标) + App 内日志页导出核对
```

## 5. 与数据层的边界规则

联调不改用户数据路径；涉及 DB 迁移的升级包，先验迁移统计日志再验功能。

## 6. 性能规则

无。

## 7. 扩展点

多设备矩阵：把 R1 的 ABI 探测脚本化，按设备自动挑包安装。

## 8. 实现骨架

```bash
adb shell dumpsys package <pkg> | grep -m2 -E "versionName|versionCode"   # R1
adb install -r app-arm64-v8a-release.apk                                   # R2/R6
adb shell input tap 600 1300                                               # R3 探测
node -e "const b=require('fs').readFileSync('shot.png');console.log(b.readUInt32BE(16),b.readUInt32BE(20))"  # R4 取 PNG 实际尺寸
```

## 9. 验收用例清单

- [ ] 升级安装后 versionName/versionCode 与构建产物一致（dumpsys 复验，勿信文件名）。
- [ ] 注入被拦时 5 分钟内切换到协作模式（不空转重试）。
- [ ] 走查结论均有落盘证据（日志行/DB 转储），无「看截图应该没问题」。

## 10. 已知坑

- HyperOS 换包（覆盖安装）可能清无障碍服务绑定，且 adb 救不回——依赖无障碍的自动化需重绑。
- MSYS/Git-Bash 会改写 adb 参数里的路径（`/data/...` 变盘符），带设备路径用 `//` 前缀或转 PowerShell。
- 「安装来源：浏览器」等系统提示与 -25 无关，别被带偏去查签名。

## 11. 复用清单

抄：§4 流程与 §8 命令组；R1~R6 直接进任何 Flutter Android 项目的联调 SOP。
配套阅读：`engineering/flutter-android-release.md`（分 ABI 打包与产物核对）。
