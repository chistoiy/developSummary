# Flutter Android 发布与分 ABI 打包（可复用）

| 项 | 值 |
|---|---|
| 标签 | `engineering` `Flutter` `Android` `发布` `CI` |
| 成熟度 | ✅ 已落地并多次发布验证 |
| 来源项目 | NikonSync（Flutter + Kotlin 的 Android App）；R11 双端附件通道源自 LifeHarness（v0.1.11~v0.1.19 多次实测验证） |
| 参考实现 | `build_apk.sh` / `build_apk.bat` / `.build_number` / 发布流程 |
| 适配成本 | 低：换 applicationId、包名与签名配置即可 |

> 一句话：把"Flutter Android 从改完代码到 Release 上线"这条链路上**会静默出错的环节**
> （版本号、产物核对、脚本）固化成可照抄的流程。

**适用场景**：任何 Flutter Android 项目要出 release 包 / 上传 GitHub Release / 装机验证。
**不适用场景**：仅打 debug 自测（那时只需 `flutter build apk --debug`）。

---

## 1. 术语

| 术语 | 说明 |
|---|---|
| **基号** | 命令行 `--build-number` 传入的版本号，也是 `pubspec.yaml` 里 `x.y.z+N` 的 N |
| **ABI 偏移** | Flutter 分 ABI 打包时，给每个 ABI 的 versionCode **叠加**一个固定偏移（见 R3） |
| **per-ABI 包** | `--split-per-abi` 产出的三个包：arm32 / arm64 / x86_64 |
| **通用包** | 一个包含所有 ABI 的包（体积最大），`--target-platform` 多值时会打出它 |

## 2. 发布规则（编号即验收项）

### 打包

- **R1** release 分 ABI 包**只能**用 `flutter build apk --release --split-per-abi --build-number <基号>`。
- **R2** 不要用 `--target-platform android-arm,android-x64` 来"凑"分 ABI 包——它打的是**一个通用包**。
- **R3** versionCode 会被 Flutter **按 ABI 叠加偏移**（×1000 量级）：

  | ABI | versionCode |
  |---|---|
  | armeabi-v7a | 基号 + 1000 |
  | arm64-v8a | 基号 + 2000 |
  | x86_64 | 基号 + 4000 |

  例：基号 2040 → 3040 / 4040 / 6040。**发新版时基号必须递增，且确认三个值都高于旧版对应包**，
  否则老机器会被 `INSTALL_FAILED_VERSION_DOWNGRADE` 拒绝。

- **R4** 产物拷贝到 `dist/` 后，**必须逐个核对版本与 ABI**，不能只看文件名：

  ```bash
  aapt2 dump badging dist/xxx.apk | grep -E "^package:|^native-code:"
  # 期望：versionName = 新版本；versionCode = 基号 + 该 ABI 偏移；native-code 唯一
  ```

- **R5** 出包必须在**所有代码改动完成之后**启动（构建中途改 Dart → 产物与源码不一致）。
- **R5b** **构建失败时 `build/app/outputs/flutter-apk/` 里仍然是上一版产物**。
  此时若无条件 `cp`，会把旧包复制成新名字、甚至上传成新版本——而且全程不报错。
  所以拷贝/上传前必须"确认构建成功"，拷贝后必须核对版本：

  ```bash
  # 构建：显式看结果，不要只看运行完没报错
  flutter build apk --release --split-per-abi --build-number <基号> 2>&1 \
    | grep -E "^e: |Built|FAILURE:"      # 有 e:/FAILURE 就是失败，后面的 cp 一律别做
  # 拷贝后核对（把"版本对不对"变成一条命令的判断，而不是靠眼睛）
  V=$(aapt2 dump badging dist/xxx.apk | grep -oE "versionCode='[0-9]+'" | grep -oE "[0-9]+")
  [ "$V" = "<期望的 versionCode>" ] || { echo "❌ 版本不符，拒绝安装/上传"; exit 1; }
  ```

  > 这条被同一项目在两天内踩了两次：一次是把旧版本 per-ABI 包当新版上传，
  > 一次是构建失败后 `cp` 把旧包复制成新名字（并"成功"装到了手机上）。

### 装机

- **R6** 装机必须用 **release** 包，且 `versionCode` 递增，才能覆盖安装并保留应用数据。
- **R7** 不要用 debug 包覆盖 release 包：debug 用 debug keystore，签名不一致会报
  `INSTALL_FAILED_UPDATE_INCOMPATIBLE`；卸载重装则会清空本地数据（下载记录、设置）。

### 上传

- **R8** `dist/` 应在 `.gitignore` 里（二进制不入库）→ **APK 必须作为 Release 附件上传**。
- **R9** 建 Release 用命令行一次到位，避免网页手传漏包：

  ```bash
  gh release create <tag> --title "版本 <tag>" \
     --notes-file <正文.md> --latest --verify-tag \
     dist/xxx-arm64.apk dist/xxx-arm32.apk dist/xxx-x86_64.apk
  gh release list          # 确认已置 Latest
  gh release view <tag> --json assets   # 确认三个包 state=uploaded
  ```

- **R10** 脚本要能一键跑，并自动递增基号（见第 8 节）。
- **R11** 国内镜像平台（Gitee）「镜像双推」= 双端 release + **双端附件**。
  `gitee release create` 只建条目、**不支持附件**，也没有 CLI 上传子命令——
  附件固定用 API `attach_files` 端点逐包补挂（实测 201；**不是** `upload_file`/`attachers`，均 404）：

  ```bash
  TOK=$("…/gitee.exe" auth token | grep -Eo '[A-Za-z0-9_-]{20,}' | head -1)   # token 只进 shell 变量，不落文件
  "…/gitee.exe" api "repos/<owner>/<repo>/releases"        # 查目标 tag 的 numericId（路径不带前导斜杠）
  for abi in arm64-v8a armeabi-v7a x86_64; do
    curl -s -o /dev/null -w "%{http_code}\n" -X POST \
      "https://gitee.com/api/v5/repos/<owner>/<repo>/releases/<numericId>/attach_files?access_token=$TOK" \
      -F name="app-$abi-release.apk" -F "file=@build/app/outputs/flutter-apk/app-$abi-release.apk"
  done                                                        # 期望三行 201
  "…/gitee.exe" api "repos/<owner>/<repo>/releases/<numericId>" | grep -o '"name":"app-[^"]*"'   # 三行齐才算挂上
  ```

  「先建 release 再补附件」是正常顺序；收口前必须核对**两端** assets 非空再汇报。

## 3. 前置校验

- **C1** 单独验一遍 release 的 Kotlin/Java 编译：
  `./gradlew :app:compileReleaseKotlin`。
  **debug 构建成功不代表 release 能编过**——release 走 minify/shrink，配置不同；
  有些错误（如 `companion object` 写在 `object` 里）在 debug 下会被缓存掩盖。
- **C2** 跑 `flutter analyze`（应为 0 issue）与测试。
- **C3** 若依赖镜像仓库，构建命令要带镜像变量
  （来源项目用 `FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn`——
  清华镜像缺 Flutter 引擎的 Maven 构件，会直接构建失败）。

## 4. 状态与版本约定

```
pubspec.yaml : version: 1.0.1+2040      ← 语义版本 + 基号（可读、可追溯）
命令行       : --build-number 2041      ← 装机版本由命令行给，源码版本不被安装问题绑架
.build_number: 2041                     ← 脚本维护的自增文件，随仓库提交（团队共享序列）
```

> 为什么解耦：曾经为了绕 `INSTALL_FAILED_VERSION_DOWNGRADE` 直接改 pubspec 的版本号，
> 导致"应用显示的版本"被安装问题绑架。解耦后 pubspec 只管语义版本。

## 5. 实现骨架（构建脚本）

```bash
#!/usr/bin/env bash
# ./build_apk.sh          编 debug
# ./build_apk.sh release  编 release 分 ABI 包（基号自动递增）
set -e
cd "$(dirname "$0")"
export FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn   # 按你的网络环境改

if [ "${1:-debug}" != "release" ]; then
  flutter build apk --debug
  echo "APK: $(pwd)/build/app/outputs/flutter-apk/app-debug.apk"
  exit 0
fi

BASE=$(cat .build_number 2>/dev/null | tr -dc '0-9')
[ -z "$BASE" ] && BASE=2040
BASE=$((BASE + 1))
echo "$BASE" > .build_number
echo "构建基号 = $BASE"

flutter build apk --release --split-per-abi --build-number "$BASE"

# 产物与核对命令都要打印出来——照着敲才不会漏核对
echo "产物：build/app/outputs/flutter-apk/app-{arm64-v8a,armeabi-v7a,x86_64}-release.apk"
echo "核对：aapt2 dump badging <apk> | grep -E '^package:|^native-code:'"
```

## 6. 验收用例清单

- [ ] `./build_apk.sh release` 能一键跑完，基号自增并写回文件
- [ ] 产出的三个包 `versionName` 都是新版本
- [ ] 三个包的 `versionCode` 分别等于 基号+1000 / +2000 / +4000，且**都高于**上一版对应 ABI
- [ ] 每个包 `native-code` 只有一个 ABI（说明确实是分 ABI，不是通用包）
- [ ] `flutter analyze` 0 issue；`./gradlew :app:compileReleaseKotlin` 通过
- [ ] 装到真机：`dumpsys package <pkg>` 显示的 versionCode 与预期一致
- [ ] 从上一版**覆盖安装**成功，且本地数据（记录/设置）保留
- [ ] Release 页面三个附件都在，`Latest` 标记正确
- [ ] 双端发布（GitHub+Gitee）时两端 release 的 APK 附件都非空（R11，三行 201 + api 复核）
- [ ] 用 debug 包覆盖 release 会明确失败（而不是静默降级）

## 7. 已知坑

| 现象 | 根因 |
|---|---|
| **把旧版本的包当成新版传上去** | 用 `--target-platform` 打通用包后，`build/app/outputs/flutter-apk/` 下**残留上一版的 per-ABI 文件**；照文件名拷贝就拷到了旧包。R4 的逐个核对就是为此 |
| **构建失败却"成功"装了/传了旧包** | `flutter build` 失败时输出目录里仍是上一版产物；`cp` 照样成功、`adb install` 也照样成功——全程零报错。必须按 R5b 先看构建结果、再核对产物版本 |
| 老机器装不上新版 | 只按基号判断，忘了每 ABI 的偏移叠加（R3） |
| 发版当天产物与源码不一致 | 构建启动后还在改代码（R5） |
| debug 一切正常、release 编译失败 | 没单独跑 release 编译（C1） |
| 覆盖安装报签名不一致 | 用 debug 包盖 release（R7） |
| 脚本打印的路径是错的 | 脚本里 `\a` 被写成 0x07 控制字符（历史遗留）；现在改成了变量拼接 |
| 构建失败（Maven 找不到 Flutter 引擎） | 镜像源缺构件，需要指定镜像变量（C3） |
| 发布后找不到 APK | `dist/` 被 gitignore 但没上传附件（R8） |
| Gitee release 只有正文没有 APK | `gitee release create` 不支持附件；须随后 POST `attach_files` 补挂（R11）。猜端点名（upload_file/attachers）会 404——通道在档就先查档再动手 |

## 8. 复用清单

1. 拷贝上面的脚本，改镜像变量与起始基号。
2. 把 `.build_number` 提交进仓库（发布序列需要团队共享）。
3. 在 CI 或本地按第 6 节逐项验收；**至少保留"逐个 aapt2 核对"这一步**——它是唯一能防住"传错旧包"的环节。
4. 若做国内平台镜像双推：照 R11 的 `attach_files` 三件套（查 id → 逐包 201 → api 复核非空），别猜端点名。
