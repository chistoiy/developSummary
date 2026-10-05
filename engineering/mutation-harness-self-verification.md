# 变异自证工装：让"绿测试"证明它真的会红（可复用）

| 项 | 值 |
|---|---|
| 标签 | `engineering` `测试` `守护门禁` `工装脚本` `Windows` `Flutter` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness（工装 `tmp/t3_mutations.py`、`tmp/b4_mutations.py`、`tmp/t10_mutations.py`，均 gitignored 临时件；被验证的常驻用例如 `test/t3_delete_reversible_test.dart`、`test/interaction_batch4_test.dart`、`test/t10_route_guard_test.dart`） |
| 参考实现 | `MUTS` 刀单（`name/file/old/new/test/expect`）+ `main()` 单刀循环（读原文→行尾折算→命中断言→改→跑→写回→md5 比对） |
| 适配成本 | 低（改 `ROOT`/测试命令/刀单即可；非 Flutter 项目把 `run()` 换成自己的测试调用） |

> 解决的问题：**全绿的测试套件并不能证明它覆盖了交付点**。
> 做法是把每处交付点做一次反向实验——摘掉它，对应用例必须红。红不出来，那条用例是摆设。
> 本篇的重点不在"要变异"，而在**变异工装自身的四条自证**：
> 行尾必须跟随目标文件、锚点命中数必须等于 1、回写后 md5 必须一致、红必须红在指定用例上。
>
> 不适用：需要跨文件语义重构的验证（变异粒度对不上）；跑一次测试成本极高（如全量集成/真机）时别做逐刀循环，
> 改成一次批跑或只做编译期守护；CI 里长期挂这套工装（它会临时改工作区，与并行写者冲突）——**工装是本地临时件**。

---

## 0. 一句话定义

一个几百行的本地脚本：按刀单把源码里的"决策点"临时改错，跑对应的单个测试文件，
要求**它必须红且红在指定的用例名上**，然后把原文一字不差地写回去并校验 md5。

## 1. 术语

| 术语 | 说明 |
|---|---|
| 刀 / 刀单 | 一次最小反向实验（`MUTS` 里的一条），含锚点串、替换串、要跑的测试文件 |
| 锚点 | 刀要摘掉的那段源码文本（含行尾与缩进，越独特越好） |
| 交付点 | 本轮真正承诺给用户的东西（一个软删调用、一条提示文案、一个接入点），一一对应一把刀 |
| 期望用例 | `expect=[...]` 里写的用例名子串，用来判定"红的原因对不对" |
| SKIP-WORKBENCH | 锚点命中数≠1 时的红灯：**没变异成功，本轮结论一律不采信** |

## 2. 需求规则（编号即验收项）

- **M1 每刀只摘"决策"，不摘"类型"。**
  *为什么*：摘掉空安全提升、`late` 声明、常量定义那类行会先撞**编译错误**——红是真的，原因不是被测行为，属假红。
  判据：红的那条用例名要和这刀的意图对得上（所以必须有 `expect`）。
- **M2 行尾跟随目标文件。**
  读用 `open(fp, encoding="utf-8", newline="")`，`eol = "\r\n" if "\r\n" in src else "\n"`，
  再把 `old`/`new` 里的 `\n` 折算成 `eol`。
  *为什么*：**同一个仓库里行尾按文件分裂是常态**，不是全局设置能推出来的。
  实测（LifeHarness，无 `.gitattributes`）：`lib/shared/widgets/ui_kit.dart` CR=612/LF=612（CRLF），
  `lib/features/read/.../reader_appearance_sheet.dart` CR=849（CRLF），
  `lib/features/ledger/presentation/ledger_page.dart` CR=0、`.../todo_list_view.dart` CR=0（LF）。
  这批刀单按 LF 写锚点去摘 CRLF 文件，**5 刀里 4 刀命中 0 次**。
- **M3 锚点命中数必须 == 1，否则 SKIP-WORKBENCH 记红灯并跳过结论。**
  *为什么*：这是"反向验证"最阴的假绿——变异没发生，测试当然还是绿的，
  读日志的人把它记成"验证过了"。**命中 0 不是"无事发生"，是工装失败。**
  命中 >1 是另一类：刀踩太浅，摘不干净，红也归因不到这刀。
- **M4 改前把原文读进内存并记 md5，跑完原样写回、再读回比对 md5；禁用 `git checkout` 回退。**
  *为什么*：① 回退必须只碰这一份文件；`git checkout -- <file>` 会连同一文件里其它未提交改动一起丢，
  并行会话共用工作区时还会卷走别人的暂存；② md5 不一致说明写回改动了别的东西（编码/行尾/BOM），
  本轮之后的所有结论都不再可信。
- **M5 红判据 = `rc != 0` 且失败输出里出现 `expect` 的用例名（两个条件都要满足）。**
  *为什么*：`flutter test <不存在的文件>` 也返回 rc=1；`grep` 到别的红灯也会 rc=1。
  只看 rc 会把工装自身的问题当成"覆盖到了"。**一片红先怀疑工装，别急着改产品代码。**
- **M6 只跑受影响的单个测试文件，且同一时刻只允许一个测试进程。**
  *为什么*：全量套件是独占资源（本项目约 12 分钟），并发起第二个 `flutter test` 会互相拖死
  （表现为 sqlite3 原生库空转、耗时数倍）。刀数 = 本轮交付点数，不要顺手加刀。
- **M7 按真实编码读写（UTF-8），不许靠系统默认码页。**
  *为什么*：错误编码回写会把文件里已有的中文写成非法字节，症状是**别的用例读不动这个文件**，
  排查成本远高于省下的一个参数。
- **M8 Windows 起 `.bat`：Python `subprocess` 可以直接跑 `flutter.bat`；Node `spawnSync` 不行**
  （必须 `cmd /c`，否则 `status=null`，整批被判红）。跨语言移植工装时这条最先踩。
- **M9 结果要留可复核记录**：每刀一行「刀名 · 红=是/否 · 回写一致=是/否 · 红在哪条用例」，
  并写进工单/交接文档。没留痕的变异等于没做。

## 3. 状态机 / 数据模型

刀单元组（脚本里就是 `dict`）：

| 字段 | 含义 | 约束 |
|---|---|---|
| `name` | 刀名，人话写清摘什么 | 唯一，日志与工单都用它 |
| `file` | 目标源码绝对路径 | 存在；本轮改过也行，锚点按**工作区当前内容**找 |
| `old` / `new` | 锚点串 / 变异串 | `old` 在文件内**恰好命中 1 次**（M3）；`new` 可以是空串 |
| `test` | 要跑的单个测试文件 | 必须是真实路径（M5 的假红来源） |
| `expect` | 期望红的用例名子串列表 | 非空；判定"红得对"（M1/M5） |

单刀状态流转：

```
读原文 → 折算行尾 → 命中数==1?
    否 → SKIP-WORKBENCH（红灯，不采信）
    是 → 写变异 → 跑测试 → 记 rc 与输出
              → 写回原文 → md5 比对
                    不一致 → DIRTY-WRITEBACK（红灯）
              → rc!=0 且 expect 命中 → PASS
              → rc==0            → NOT-RED（说明这条用例没覆盖该交付点）
              → rc!=0 但 expect 不命中 → WRONG-RED（M1，假红）
```

## 4. 关键流程 / 判定条件

- **交付点从"承诺"里来，不从 diff 里来**：先列本轮给用户的东西（软删入口、撤销文案、接入点、路由闸），
  每样配一刀。凭 diff 加刀会变成"改了啥就测啥"，覆盖不到"该有但没有"的东西。
- **锚点要独特**：带上前导缩进与整行尾；同一段代码在多份文件里重复时，锚点里加一个只在该文件出现的邻近标识。
- **`new` 选"看起来合理但错"的**：比如把 `softRemove(todo)` 换成 `remove(todo)`（回潮），
  把"判脏基准"从已生效配置换成初始值（第二次取消静默丢）——这类型错误能测出用例的真实灵敏度。
  直接删空（`new=""`）适合"接线是否存在"类断言。
- **不要为了少跑一次测试而合并多刀**：合并后红了无法归因。
- **改 UI/文案时同步**：提示条文案与守护用例里的 `contains('已移入回收站')` 是一对，
  刀单里的文案串要跟着改，否则刀红了是因为文案对不上，不是行为变了。

## 5. 与数据层 / 仓库的边界规则

- **D1 工装与其快照落 gitignored 目录**（本项目是 `tmp/`），不入仓；**结论入工单**。
  *为什么*：刀单里的锚点会随代码演进失效，入仓就变成一份会误导后来者的"伪常驻守护"。
  真正要长期钉住的东西请写成源码级守护用例（`contains/isNot(contains)`），那才进 `test/`。
- **D2 只碰本轮自己改过的文件**；并行会话的文件不动不提交（`git checkout`/`stash`/`clean` 一律禁止）。
- **D3 跑完必须 `git status` 复核**：目标文件应显示"无改动或与变异前一致"，
  意外多了改动就是 M4 失守，先修工装再谈结论。

## 6. 性能规则

- **P1 一刀 = 一次测试进程启动**（本项目约 20–60s/文件），刀数按交付点控制；批内绝不跑全量。
- **P2 支持点名重跑**：`main()` 读 `sys.argv`，只跑失败的那几刀，改完锚点不用整批重来。
- **P3 输出别用 `| tail` 之类缓冲**——长任务要能看到逐刀进度（每刀立刻 `print` + flush）。

## 7. 扩展点

- **E1 与"棘轮守护"配套**：棘轮登记项（白名单）要加"失效自检"——登记的文件不再命中该模式就报红，
  否则白名单变永久免罪牌。见 [`engineering/ratchet-guard-multi-writer-policy.md`](ratchet-guard-multi-writer-policy.md)。
- **E2 死循环类缺陷用编译期守护**代替运行时变异（`main()` 里静态检查源码文本）。
  见 [`engineering/sync-spin-hang-forensics.md`](sync-spin-hang-forensics.md)。
- **E3 想升级成 CI 闸**：把刀单变成数据（JSON/YAML）、把 `run()` 换成 CI 的单测命令、
  并给每刀加"隔离工作区"（`git worktree`），避免改工作区这条本地前提。

## 8. 实现骨架

```python
# -*- coding: utf-8 -*-
"""变异自证：逐刀摘掉交付点 → 对应用例必须红 → 原样写回并校验 md5。"""
import hashlib, os, subprocess, sys

ROOT    = r"D:/path/to/project"
TESTCMD = [r"D:/path/to/flutter/bin/flutter.bat", "test"]   # M8：Python 可直接跑 .bat

MUTS = [
    dict(name="刀1 滑删回潮成真 DELETE",
         file=os.path.join(ROOT, "lib", "features", "todo", "todo_list_view.dart"),
         old="repo.softRemove(todo)", new="repo.remove(todo)",
         test="test/t3_delete_reversible_test.dart",
         expect=["滑掉：行还在库里但 deleted_at 非空"]),
    # 每处交付点一刀（M1/M4…），锚点写法按目标文件真实行尾（工装会折算）
]

def run(test):
    p = subprocess.run(TESTCMD + [test], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")   # M7
    return p.returncode, (p.stdout or "") + (p.stderr or "")

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    only, bad = sys.argv[1:], []                     # P2：点名重跑
    for m in MUTS:
        if only and not any(o in m["name"] for o in only):
            continue
        fp = m["file"]
        with open(fp, encoding="utf-8", newline="") as f:      # M2/M7
            src = f.read()
        eol = "\r\n" if "\r\n" in src else "\n"
        old, new = m["old"].replace("\n", eol), m["new"].replace("\n", eol)
        md5 = hashlib.md5(src.encode("utf-8")).hexdigest()      # M4
        hits = src.count(old)
        if hits != 1:                                           # M3
            print("SKIP-WORKBENCH %s 锚点命中 %d 次（eol=%r）" % (m["name"], hits, eol))
            bad.append(m["name"]); continue
        with open(fp, "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(old, new, 1))
        rc, out = run(m["test"])                                # M6
        with open(fp, "w", encoding="utf-8", newline="") as f:
            f.write(src)                                        # M4 回写
        back = hashlib.md5(open(fp, encoding="utf-8", newline="")
                           .read().encode("utf-8")).hexdigest() == md5
        hit_names = [e for e in m["expect"] if e in out]        # M5
        red = rc != 0 and bool(hit_names)
        print("%s | 红=%s(%s) 回写一致=%s" % (m["name"], red,
              ",".join(hit_names) or "无期望用例", back))
        if not red or not back:
            print(out[-900:]); bad.append(m["name"])            # M9
    print("MUT-RESULT", "OK" if not bad else "BAD " + "; ".join(bad))
    return 0 if not bad else 1

if __name__ == "__main__":
    sys.exit(main())
```

## 9. 验收用例清单

- [ ] 每刀都有「红=True 且红在 `expect` 用例上」的一行记录（M5）。
- [ ] 每刀「回写一致=True」，且跑完 `git diff` 对这些文件为空（M4/D3）。
- [ ] **工装自检**：故意把某刀锚点的行尾写错（LF 锚点打 CRLF 文件）→ 必须报 `SKIP-WORKBENCH 命中 0 次`，
  整体红灯，而不是"全部通过"。这一条是本篇的核心，建议每次换项目移植时做一次。
- [ ] 故意把一个不存在的路径填进 `test` → 工装必须判它红因不对（不许把 rc=1 直接算作覆盖）。
- [ ] 摘掉交付点后**没红**的用例：三种处置，必须当场选一种并记录——
  ① 补用例；② 承认该交付点没有覆盖；③ **判定"这条规则本来不是硬约束"**，把规则与代码注释一起降级。
  本项目实例：摘掉"撤销提示条推到下一帧"的 `addPostFrameCallback`，5 条常驻用例全绿 + 独立探针也不抛
  ⇒ 结论是③（原来写在注释里的"同帧必炸"是误判），而不是给一条不该存在的规则补守护。
- [ ] 单进程：跑工装期间没有其它 `flutter test`/构建在并发（M6）。
- [ ] 结果登记：刀名 + 红在哪条用例，进了工单或交接文档（M9）。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| 一片 `锚点命中 0 次`，改完还是绿的 | 锚点行尾与目标文件不符（M2）。同一仓库按文件混用 CRLF/LF 是常态；无 `.gitattributes` 时更不会一致 |
| "变异跑完了，全绿"被当成验证通过 | 命中 0 次时**变异根本没发生**（M3）——反向验证的假绿；工装必须把命中数≠1 判成红灯 |
| 红了，但红在编译错误上 | 摘的是类型/空安全行而不是决策行（M1） |
| `git checkout -- 文件` 之后别的改动没了 | 整文件回退会吞同文件其它未提交改动，并行会话还会卷走别人暂存（M4/D2） |
| 回写 md5 对不上 | 写了不同编码/统一了行尾/加了 BOM（M2/M7）；此时后续结论全部作废 |
| 别的用例突然"读不到/解析不了"这个文件 | 脚本用了系统默认码页回写，中文变成非法字节（M7） |
| `rc=1` 却没有任何用例失败 | 传了不存在的测试文件路径，或 grep/管道吃掉了输出后误判（M5） |
| Node 版工装 `status=null` | `.bat` 不能直接 `spawnSync`，要 `cmd /c`（M8）；Python 版没这问题 |
| 耗时突然翻倍、sqlite 空转 | 并发开了第二个测试进程（M6）；测试类工装一律单轨 |
| 一整批刀都"没红" | 交付点与用例对不上（先怀疑测试写错、再怀疑产品代码），不是"代码更健壮" |

## 11. 复用清单

1. 复制 §8 骨架，改 `ROOT`/`TESTCMD`/刀单三处即可。
2. 先写**交付点清单**（人话），一条一刀，再动代码——顺序反了就会只测自己改过的地方。
3. 移植后第一件事做 §9 的「工装自检」：故意写错行尾，确认它报 `SKIP-WORKBENCH`。
4. 长期要钉的语义另写源码级守护用例（`contains/isNot(contains)` + 白名单失效自检），刀单本身别入仓。
5. 与被验证方案互相引用：本篇配合
   [`mobile/flutter-dismissible-optimistic-removal.md`](../mobile/flutter-dismissible-optimistic-removal.md)
   的 §9 最后一条使用。
