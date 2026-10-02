#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""developSummary 索引门禁：README 文档索引 ↔ 仓库文件 双向一致 + 行格式校验。
用法：仓库根运行 `python tools/lint.py`，红灯 exit 1。
检查项：
  L1 索引行指向的 .md 文件必须存在；
  L2 各域目录里的方案文档（排除 <domain>/README.md 占位）必须登记进索引；
  L3 表格行列数=5、标题列为链接、「什么场景用得上」列非空、「目录」列与路径前缀一致、「成熟度」列非空；
  L4 TEMPLATE.md / README.md 在位。
域 README 占位与 tools/ 不计入文档数。"""
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
DOMAINS = ["mobile", "web", "backend", "protocol", "engineering"]
red = []

if not os.path.isfile("README.md") or not os.path.isfile("TEMPLATE.md"):
    print("L4 RED: README.md / TEMPLATE.md 缺失"); sys.exit(1)

text = open("README.md", encoding="utf-8").read()
rows = [l for l in text.splitlines() if l.startswith("| [")]
indexed = []
for l in rows:
    cells = [c.strip() for c in l.split("|")]
    if len(cells) != 7:  # 前后空段 + 5 列
        red.append(f"L3 RED 列数 {len(cells)-2}: {l[:60]}…")
        continue
    _, title, domain, scene, tags, maturity, _tail = cells
    m = re.match(r"\[.+\]\(([^)]+\.md)\)\s*$", title)
    if not m:
        red.append(f"L3 RED 标题列非链接: {title[:40]}")
        continue
    path = m.group(1)
    indexed.append(path)
    if not os.path.isfile(path):
        red.append(f"L1 RED 索引指向不存在文件: {path}")
    if not scene:
        red.append(f"L3 RED 场景列为空: {path}")
    if not maturity:
        red.append(f"L3 RED 成熟度列为空: {path}")
    if not path.startswith(domain + "/"):
        red.append(f"L3 RED 目录列与路径不符: {path} vs {domain}")

present = []
for d in DOMAINS:
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".md") and f != "README.md":
                present.append(f"{d}/{f}")
for p in present:
    if p not in indexed:
        red.append(f"L2 RED 文档未登记进索引: {p}")
# 索引重复行检测
dup = {p for p in indexed if indexed.count(p) > 1}
for p in dup:
    red.append(f"L3 RED 索引重复行: {p}")

print(f"索引行 {len(indexed)} · 在库文档 {len(present)}")
if red:
    print("红灯:")
    for x in red:
        print("  " + x)
    sys.exit(1)
print("PASS 索引与仓库一致，行格式全过")
