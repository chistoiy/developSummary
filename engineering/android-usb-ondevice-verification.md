# Flutter 真机 USB 联调取证与协作模式

| 项 | 值 |
|---|---|
| 标签 | `engineering` `Flutter` `Android` `adb` `真机联调` |
| 成熟度 | ✅ 已落地并验证（多轮真机反馈批实战；2026-10-05 V1.61 批4 补 R7~R13 坐标时效/朝向/工装自证三条线） |
| 来源项目 | LifeHarness（`d:\dev_workplace\flutter_te\lifeHarness`）V1.41 R2~R6 真机联调 ＋ V1.60~V1.61 回收站/物品删除真机自证（复测包 6060~6071） |
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
- **R7 坐标只在"同一次 dump 的同一屏"内有效**。每步 tap 前重新 dump、tap 后必须再 dump 校验落点，
  **绝不按上一步坐标盲连**。最常见的破坏者是"应用被系统回收后重启"（HyperOS 内存吃紧时几分钟就会发生，
  看 `pidof <pkg>` 变号即可确认）——进程换了，上一屏的坐标会落到完全不同的页面，表现像"点了没反应/跑到首页"。
- **R8 走查前把屏幕朝向钉死**。语义树 bounds 与 `screencap` 都用**当前朝向**的像素系：设备被物理转动成横屏后
  整棵树的 x 会大到 2670（截图变 2670×1200），布局与用例假设全部错位。需要时
  `settings put system accelerometer_rotation 0` ＋ `user_rotation 0` 锁竖屏走完，**收尾必须还原成 1** 并在报告里写明动过设备设置。
- **R9 双证取证**：`uiautomator dump` 给可点节点的 text/bounds，`screencap` 给观感；两者不一致时**以 dump 为准**。
  渲染缩略图判读会看错数字与状态（本项目实测两次误判），要量就画网格叠加层或读 bounds，别靠肉眼。
- **R10 先分清"工装红"与"产品红"**：`uiautomator dump` 并发时会崩在
  `IllegalStateException: UiAutomationService ... already registered!`——那是**工装进程**的 FATAL，会进 crash 缓冲，
  极易被记成"应用崩了"。判据＝看栈顶包名（`com.android.commands.uiautomator`）与 pid 是否等于被测应用 pid。
  另：dump 偶发 `could not get idle state` ⇒ 重试循环；每次 dump 前**删掉远端与本地旧 xml**，否则失败时会 pull 到旧快照、把上一屏当当前屏读。
- **R11 文本注入按 ROM 能力降级**：小米系中文 IME 会把 ASCII 打乱（`t6070item` 实测变 `6070199体199`），
  `input text` 只用数字/纯 ASCII 短串；需要中文样本时改用应用内既有数据或请用户手输。
  Flutter 语义框 bounds 常比可视输入框大，落点要瞄 `EditableText` 实际位置（本项目价格框实测只占左半）。
- **R12「点了没反应」按四因顺序排查**：① 注入权限（R3）② 坐标失效（R7/R8）③ 目标不是可点节点
  （父容器吞点击、热区在别处、卡片整体不可点只有子元素可点）④ 才是产品缺陷。跳过 ①②③ 会白白改产品代码。
- **R13 破坏性操作的确认协议要在一次命令里连打**：需要"二次点击确认"（2~3 秒窗口）的删除，
  两次 `input tap` 之间插一次 dump 就会让窗口过期，表现为"确认了但没删"。

## 3. 状态机 / 数据模型

一次"点按—取证"循环的有效域：

```
dump(屏A) → 坐标表(仅对屏A + 当前朝向 + 当前进程 有效)
   └─ tap → 期望屏B ─┬─ dump 校验落点＝屏B → 继续
                     └─ 落点≠屏B → 作废坐标表，回到 dump(当前屏) 重来（R7）
破坏有效域的三个事件：应用被回收重启（pid 变）／屏幕朝向变／弹层或键盘改变了布局
```

## 4. 关键算法或流程

```
连上设备
 ├─ dumpsys 查已装 versionName/Code/ABI ── 选对应 ABI 新包（-25 预防）
 ├─ install -r（覆盖，数据保留）
 ├─ input tap 探测注入权限
 │    ├─ OK → 自动走查（约定操作权 + 落盘取证）
 │    └─ SecurityException → 请开「USB 调试(安全设置)」→ 仍不行 → 协作模式
 ├─ 锁朝向（必要时，R8；收尾还原）
 └─ 每步：dump → tap → 再 dump 校验落点（R7）→ screencap 双证（R9）→ App 内日志页导出核对
     失败先按 R12 四因排查，别直接改产品代码
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

# R7/R9 单步循环（写成脚本文件，别用内联 shell 拼；中文读数落文件再读，GBK 控制台会吞）
adb shell rm -f /sdcard/u.xml && rm -f local/u.xml                      # R10 防 pull 到旧快照
for i in 1 2 3 4 5; do adb shell uiautomator dump /sdcard/u.xml 2>&1 | grep -q "dumped to" && break; sleep 1; done
adb pull /sdcard/u.xml local/u.xml                                      # stdout+stderr 都要收，成功行在 stderr
adb shell input tap "$X" "$Y" && sleep 3                                # 二次确认要连打：同一条命令里两次 tap
# 然后重新 dump，断言目标节点文本/bounds 出现＝落点对；否则本轮读数作废（R7）
adb shell pidof <pkg>                                                   # 变号＝应用重启过，坐标表全部作废
```

```bash
# R8 锁竖屏与还原（收尾必做，并在报告里写明动过设备设置）
adb shell settings put system accelerometer_rotation 0 && adb shell settings put system user_rotation 0
adb shell settings put system accelerometer_rotation 1
```

## 9. 验收用例清单

- [ ] 升级安装后 versionName/versionCode 与构建产物一致（dumpsys 复验，勿信文件名）。
- [ ] 注入被拦时 5 分钟内切换到协作模式（不空转重试）。
- [ ] 走查结论均有落盘证据（日志行/DB 转储），无「看截图应该没问题」。
- [ ] 每一步都用"tap 后重新 dump 校验落点"闭环，没有沿用上一屏坐标（R7）。
- [ ] 设备朝向在走查期间稳定，且临时锁过的旋转设置已还原（R8）。
- [ ] crash 缓冲里的 FATAL 都判过归属（工装 vs 被测应用），没把工装崩记进产品结论（R10）。
- [ ] 「点了没反应」按 R12 四因排查过，排查记录在结论里。

## 10. 已知坑

- HyperOS 换包（覆盖安装）可能清无障碍服务绑定，且 adb 救不回——依赖无障碍的自动化需重绑。
- MSYS/Git-Bash 会改写 adb 参数里的路径（`/data/...` 变盘符），带设备路径用 `//` 前缀或转 PowerShell。
- 「安装来源：浏览器」等系统提示与 -25 无关，别被带偏去查签名。
- `input tap` 全无反应但 `input keyevent` 有效 ⇒ ROM 的「USB 调试（安全设置）」未开或**换包后被清**（R3）。
- 点 A 却跳到 B 页／回到首页 ⇒ 应用被系统回收后重启，沿用了上一屏坐标（R7）；`pidof` 变号即证据。
- 语义树 x 坐标出现 2670、截图变 2670×1200 ⇒ 设备被物理转成横屏，坐标系整体换轴（R8）。
- crash 缓冲有 FATAL 但应用还在跑 ⇒ 是 `uiautomator dump` 自己的进程撞了 `UiAutomationService already registered`（R10）。
- dump 报成功却读到上一屏内容 ⇒ 远端/本地旧 xml 没删，pull 到旧快照（R10）。
- `uiautomator dump` 的成功行在 **stderr**，只看 stdout 会把"其实 dump 成了"判成失败（R10）。
- 输入框里变出一串乱数字 ⇒ 中文 IME 吞掉了 ASCII（R11）；改用数字名或让用户手输。
- 二次确认"点了没生效" ⇒ 两次点击之间插了 dump/截图，计时窗口过期（R13）。
- `adb install -r -G ""` 这类附带参数会打出半截 Java 栈并吞掉结果行 ⇒ 用干净的 `adb install -r <apk>` 重跑，认 `Success` 行。

## 11. 复用清单

抄：§4 流程与 §8 命令组；R1~R6 直接进任何 Flutter Android 项目的联调 SOP；
R7~R13 是"长时间走查"（几十步、跨十几分钟）才会撞到的三条线——**坐标时效**、**朝向锁定**、**工装自证**，
任何用 adb 做半自动验收的项目都会用到。
配套阅读：`engineering/flutter-android-release.md`（分 ABI 打包与产物核对）、
`engineering/stale-red-triage-and-dead-guard.md`（同一套"先怀疑工装再怀疑产品"的判定思路用在测试红灯上）、
`engineering/mutation-harness-self-verification.md`（工装自证）。
