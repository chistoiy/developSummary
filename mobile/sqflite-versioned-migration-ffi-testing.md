# sqflite 版本驱动迁移框架 + FFI 测试全链路（可复用）

| 项 | 值 |
|---|---|
| 标签 | `mobile` `Flutter` `sqflite` `数据库迁移` `测试` |
| 成熟度 | ✅ 已落地并验证 |
| 来源项目 | LifeHarness（`lib/core/database/`、`test/database_schema_test.dart`） |
| 参考实现 | `DbSchema`（DDL+migrations 表）、`AppDatabase`（open/升级）、`DaoBase`、`TodoDao/EventDao` |
| 适配成本 | 低（纯模式，无外部依赖） |

> 单文件 sqflite 项目的建库/升版/测试标准做法：**版本常量 + 全量建表 DDL + 表驱动逐级迁移 + 内存库单测**。
> 不适用：需要响应式查询/跨进程并发的场景（考虑 drift/Realm）。

---

## 0. 一句话定义

`DbSchema.version` 每次改表 +1，`migrations[from] = [DDL...]` 登记增量语句，`onUpgrade` 逐级执行；测试用 `sqflite_common_ffi` 内存库跑同一份 DDL，保证 schema 与代码永不脱节。

## 1. 术语

| 术语 | 说明 |
|---|---|
| createStatements | v1 全量建表 DDL（新装用户路径） |
| migrations | `Map<int, List<String>>`，key=升级前版本号，value=该级增量 SQL |
| ffi 内存库 | `databaseFactoryFfi.openDatabase(inMemoryDatabasePath)`，桌面跑真 SQLite |

## 2. 需求规则（编号即验收项）

- **R1 建库即预留**：首版就把"确定会做但没排期"的表建出来（字段可粗，后续 ALTER）。*为什么：晚建表=用户升级时才有数据迁移风险，早建表零成本。*
- **R2 单一 DDL 源**：建表语句只写在 `DbSchema.createStatements`，测试与运行共用；禁止测试里另抄一份。
- **R3 逐级迁移**：`for (v = from; v < to; v++) execute(migrations[v])`，支持跨版本升（1→3 自动串 1→2、2→3）。*为什么：用户可能从任意旧版本直升最新。*
- **R4 时间统一 epoch 毫秒 INTEGER**；布尔用 INTEGER 0/1；枚举存 index 或数字串。*为什么：SQLite 无日期类型，混格式让查询与迁移噩梦化。*
- **R5 列类型与 Dart 读写严格对应**：TEXT 列存数字要 `toString()`、读要 `int.tryParse('$v')`。*为什么：SQLite 动态类型不会拦你，`as int` 直接崩（本项目真实踩坑：color_tag）。*
- **R6 迁移自检**：升级前后核对行数/关键列存在（测试断言"旧数据保留 + 新列存在且为 NULL"）。

## 3. 状态机 / 数据模型

```
open(path, version: N,
  onConfigure: PRAGMA foreign_keys=ON,
  onCreate:    batch(createStatements),
  onUpgrade:   for v in from..to-1: batch(migrations[v]))
```

## 4. 关键算法或流程

插件化预留：隔离表统一 `plugin_` 前缀，宿主迁移脚本永不触碰该前缀（`expect(table.startsWith('plugin_'), isFalse)` 守护宿主表）。

## 5. 与数据层的边界规则

- **D1** UI 只经 Repository→DAO→Database；DAO 提供 `rawXxx` 通用 CRUD（继承 `DaoBase`），业务查询方法在 DAO 内写。
- **D2** Repository 层隔离 DB 实现（换库/加库只改 DAO 构造，UI 零感知）。

## 6. 性能规则

- **P1** 高频查询列建索引（`done`/`due_at`/外键列），建表 DDL 里直接跟 `CREATE INDEX`。
- **P2** 多语句用 `batch.commit(noResult:true)`。

## 7. 扩展点

- **E1** 迁移前自动快照备份（复制 .db 文件为 `.bak_v{from}`），失败可回滚。
- **E2** 迁移表驱动可升级为 schema 对象生成 DDL（防手写漂移）。

## 8. 实现骨架

```dart
class DbSchema {
  static const version = 2;
  static const createStatements = [ '''CREATE TABLE todos(...tag TEXT...)''',
    'CREATE INDEX idx_todos_done ON todos(done)', /* ... */ ];
  static const migrations = { 1: ['ALTER TABLE todos ADD COLUMN tag TEXT'] };
}

class AppDatabase {
  Future<Database> open({String? pathOverride}) => _db ??= await openDatabase(path,
    version: DbSchema.version,
    onConfigure: (d) => d.execute('PRAGMA foreign_keys = ON'),
    onCreate: (d, v) async { final b = d.batch(); for (final s in DbSchema.createStatements) b.execute(s); await b.commit(); },
    onUpgrade: (d, from, to) async { for (var v = from; v < to; v++) { final b = d.batch();
      for (final s in DbSchema.migrations[v] ?? []) b.execute(s); await b.commit(); } });
}

// 测试（纯 test，非 widget）
setUp: db = await databaseFactoryFfi.openDatabase(inMemoryDatabasePath);
       for (final s in DbSchema.createStatements) await db.execute(s);
// 迁移测试：手工建 v1 表+旧数据 → 跑 migrations[1] → 断言行保留+新列 NULL
```

## 9. 验收用例清单

- [ ] 全新安装：全部表+索引建出（`sqlite_master` 查询断言）。
- [ ] 单测覆盖：CRUD / 排序语义 / 迁移后旧数据可读。
- [ ] 跨版本升级：构造 v1 库 → open(version=3) → 1→2→3 全执行。
- [ ] TEXT 列存 int 的字段：写入读出往返一致（tryParse 路径）。
- [ ] CI/本机断网也能跑（见坑表 workaround）。

## 10. 已知坑

| 现象 | 根因 |
|---|---|
| `type 'String' is not a subtype of type 'int?'` 读行崩溃 | TEXT 列存了 int（SQLite 照收），Dart `as int` 崩；按 R5 双向显式转换 |
| testWidgets 里 DB 操作卡死 10 分钟 + "database has been locked" | `databaseFactoryFfi` 走 Isolate，与 widget test 的 FakeAsync 死锁；**widget 测试必须用 `databaseFactoryFfiNoIsolate`**（纯 test 不受影响） |
| 测试构建期报"Cannot open .../libsqlite3.arm.android.so"或下载超时 | sqlite3 pub 包的构建钩子要下载原生库；用 `hooks.user_defines.sqlite3: {source: test-sqlite3, directory: tools/sqlite3/}` 指向本地预置产物；**directory 值必须带尾斜杠**（Uri.resolve 无斜杠会吞掉末段拼错路径）；Android 构建同样会跑该钩子，需把各 ABI .so 一并预置 |
| GitHub 不可达时拿不到预置产物 | 走 ghproxy 类镜像下载 release 资产，校验魔数（PE `MZ` / ELF `\x7fELF`）后入库 `tools/`，README 记录来源与版本对应关系 |
| `db.select()` 不存在 | sqflite API 是 `db.query()` |

## 11. 复用清单

1. 复制 `DbSchema / AppDatabase / DaoBase` 三件，替换表名与 DDL。
2. dev_dependencies 加 `sqflite_common_ffi`；网络受限则按坑表配 hooks user_defines + 预置目录。
3. 每次改表：version+1 → createStatements 同步（新装）→ migrations 登记（老用户）→ 补迁移断言测试。
