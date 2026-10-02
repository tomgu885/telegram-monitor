# Telegram 本地消息记录器

Python 3.12+、Telethon MTProto 普通用户账号、SQLite 和本地 JSONL。支持 macOS / Linux，
使用系统文件锁防止同一个 data 目录被多个进程同时写入。

只被动记录 Telegram 下发的新消息、编辑和可识别的删除事件。没有 Bot API、
Codex/OpenAI 调用、prompt builder、Jira、workflow 或自动任务功能；不会发送、回复、
转发、reaction、标记已读，也不会下载媒体或递归获取被回复的消息。

## 安装与启动

在项目目录执行，先确认 `python3 --version` 为 3.12 或以上。

### 1. 创建 virtualenv

```bash
cd /Users/jack/python3/telegram-monitor
python3 -m venv .venv
source .venv/bin/activate
```

### 2. 安装

```bash
pip install -r requirements.txt
```

运行时只有 Telethon、python-dotenv、PyYAML 三个直接依赖，不需要 Docker 或外部数据库。

### 3. 配置

首次配置时复制示例；已有个人配置时不要覆盖。

```bash
cp .env.example .env
cp config.example.yaml config.yaml
chmod 600 .env config.yaml
```

登录 [my.telegram.org](https://my.telegram.org/)，进入 **API development tools**，
创建应用并获取 `api_id` 和 `api_hash`，填入 `.env`：

```dotenv
TELEGRAM_API_ID=你的数字API_ID
TELEGRAM_API_HASH=你的API_HASH
TELEGRAM_PHONE=含国家区号的手机号
```

使用自己的普通用户账号，不是 BotFather token。系统环境变量优先于 `.env`。
认证流程参考 [Telethon Signing In](https://docs.telethon.dev/en/stable/basic/signing-in.html)。

编辑 `config.yaml`，使用整数 Telegram ID：

```yaml
telegram:
  watch_all: true
important:
  chat_ids:
    - -1001234567890
    - -1009876543210
  user_ids:
    - 123456789
    - 987654321
```

- 默认配置列表为空，`watch_all: true` 记录账号实际收到的所有普通消息更新，
  包括在其他客户端发出的自己消息，使用 `is_outgoing` 区分。不是扫描 Telegram 所有群。
- `chat_ids` 与 `user_ids` 是 **OR**：分别标记 `chat_id`、`user_id`，同时命中标记
  `chat_id,user_id`。重点消息也照常进入完整 archive，不会漏掉普通消息。
- `watch_all: false` 只记录命中任一列表的消息；两个列表都为空时不采集普通消息。
  已有记录的编辑仍维护最新状态，即使后来调整了关注列表。
  删除事件只处理配置中的 chat 或数据库里已有记录；未知 chat 的删除无法过滤，故跳过。
- Chat 使用 Telethon 带符号的 ID：用户通常为正数，普通群为负数，频道/超级群为 `-100…`。
  User ID 必须为正整数；匿名管理员或频道身份发送的消息不会猜测为某个用户。
- 可从控制台的 `chat=... sender=...` 获取实际收到事件的 ID，然后更新配置并重启。
  配置变化只影响后续事件，不自动重标记历史数据。

可先离线检查配置，不连接 Telegram，也不输出凭据：

```bash
python3 -m app --check-config
```

### 4. 第一次登录

在交互式终端运行：

```bash
python3 -m app
```

程序按需请求登录验证码，验证码与 2FA 密码使用隐藏输入，不写入日志或配置。
验证码送达方式由 Telegram 决定。Session 持久化至 `data/telegram.session`。

### 5. 后续运行与停止

```bash
source .venv/bin/activate
python3 -m app
```

有效 session 会直接复用，不重复发送登录验证码。账号撤销 session 后需要重新认证。
`Ctrl+C` / `SIGTERM` 会断开 Telegram、补写已提交的待归档事件、flush/fsync 文件、关闭 SQLite。
新文件默认仅当前用户可读写，data 目录权限为 `0700`。

控制台只显示 ID、事件类别、文本长度与状态，例如：

```text
[INFO] Connected to Telegram
[INFO] Listening for messages...
[INFO] [MSG] chat=-100123 sender=12345 message=889 event=message_new text_length=123
[INFO] [IMPORTANT] chat=-100123 sender=98765 message=890 event=message_new text_length=45
```

异常仅记录异常类型和调用位置，不记录异常正文、局部变量或完整更新对象。
Telethon 底层日志被屏蔽，避免异常包含聊天内容或 RPC 参数。

## 目录与主要模块

```text
telegram-monitor/
├── app/
│   ├── __init__.py
│   ├── __main__.py          # 启动、单实例锁、最终归档与关闭
│   ├── config.py            # .env/YAML 读取、校验、OR 匹配
│   ├── telegram_client.py   # 登录、新消息/编辑/删除监听、重连、信号
│   ├── models.py            # 原文/身份/媒体解析、UTC 时间、JSON normalization
│   ├── storage.py           # SQLite 最新状态、索引、事务待归档队列
│   ├── archive.py           # append-only JSONL + fsync
│   └── logging_config.py    # 不泄露消息/凭据的日志
├── tests/                   # unittest；fake client、真实 TL 数据对象，无网络
├── config.example.yaml
├── config.yaml              # 本机配置，已忽略
├── .env.example
├── .env                     # 自行配置，已忽略
├── .gitignore
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml           # Ruff 配置
├── README.md
└── data/                    # 首次有效启动时创建，全部忽略
    ├── telegram.session
    ├── messages.db          # 运行时可能带 -wal、-shm
    ├── recorder.lock
    ├── archive/YYYY-MM-DD.jsonl
    └── important/YYYY-MM-DD.jsonl
```

`.env`、`.env.*`（除示例）、`data/`、所有 session 文件、本机 `config.yaml`、`.venv/`
均被 `.gitignore` 排除。数据库和 archive 保存聊天原文，不加密；本地文件与备份都应妥善保管。

## 数据语义

`telegram_messages` 保存消息最新状态，使用 `UNIQUE(chat_id, telegram_message_id)` 去重。
包含需求中的所有字段，另有 `edited_at`、`deleted_at`、`has_media`、`media_type`、
`file_name`、`mime_type`、`file_size`。媒体只存元数据，不下载。

- 所有时间保存为带 `+00:00`、固定微秒精度的 UTC ISO 8601。
  `message_date` / `edited_at` 来自 Telegram；`received_at` / `created_at` 是首次本地采集时间。
  `deleted_at` 是本地收到删除通知的时间，不冒充 Telegram 提供的删除发生时间。
- 原文直接来自 `event.raw_text`，保留换行、Unicode 与空文本。
- sender/chat 使用更新附带的实体信息；缺失名称时保存 `NULL`，不按转发来源或签名推断用户。
  编辑事件缺少实体时保留同一消息/作者的最后已知身份与名称。
  不额外访问网络补查实体，原始 ID 与字段仍保留，后续模块可以另行补充。
- `raw_json` 是 JSON 文本，包含 Telethon `to_dict()`、chat/sender ID、reply、entities、
  forward、media 等信息。datetime 转 ISO，bytes 转带标记的 Base64，未知类型/循环引用
  使用可见标记。不会 pickle，也不会因单个不支持字段直接丢弃整条消息。
- 编辑更新当前 SQLite 记录，同时追加 `message_edited`。未见过原消息的编辑也会插入。
  按 `edit_date` 可识别的旧编辑只归档并设 `applied=false`，不覆盖较新内容。
  时间相同且不同内容的更新按收到的顺序处理。
- 删除保留原文并设置 `deleted_at`；已知 chat 但未见原消息时写入占位记录。
  后到的新消息/编辑可补充内容，但不会清除删除标记。
- 无法解析的异常消息以 `message_unparsed` 保存原始证据到完整 archive/outbox，
  控制台报错；不会伪造一条成功解析的 SQLite 消息。

JSONL 一行一个事件，含 `schema_version=1`、稳定 `event_id`、`event_type`、事件 `received_at`
及消息快照。JSON 内部转义换行和 Unicode，读取 JSON 后还原原文。
文件名按**事件接收日 UTC**分组：次日收到的编辑写入次日文件，不修改历史行。
UTC 与新加坡时间相差 8 小时，因此本地凌晨的记录可能在前一个 UTC 日期文件中。

## 可靠性与明确限制

消息状态与 `archive_outbox` 在同一 SQLite 事务中提交，开启 WAL 和 `synchronous=FULL`。
后台每秒最多批量提取 100 个待归档事件，每个 JSONL append 都 flush + fsync，成功后再
确认该目标文件完成。普通 archive 成功、important 失败时，只补写 important。
SQLite 尚未提交时写入失败会每 5 秒重试同一事件；归档失败保留持久队列并重试，重启后继续。

SQLite 与文件系统无法组成一个原子事务，故归档是 **at least once**：
写完文件而未提交完成标记时崩溃，重启可能追加同一事件，按 `event_id` 去重即可。
普通重复新消息不会再次入库或归档。磁盘耗尽时应及时恢复空间；停止时若仍有未提交事件
或未完成归档，会明确报错并返回非零状态，不能当作采集完整。

如果突然断电/强杀留下没有换行符的最后一行，程序报 `IncompleteArchiveError`，
保留文件原字节和 outbox，暂停归档该文件；仍可继续提交 SQLite。
需停机后备份该 JSONL 与 SQLite，再人工恢复残缺尾行（或保留原文件副本后重建完整文件），
然后重新启动补写。程序不会擅自截断历史 archive。
备份 SQLite 时应停机后复制，或用 SQLite backup API；运行中不要只复制 `.db` 忽略 WAL。

短暂网络故障使用 Telethon 自动重连，连接任务退出后再尝试连接。监听器注册在连接之前，
开启 `catch_up=True` 尝试补收离线更新。但这不是全量历史同步：Telegram 不保证无限期保留
所有离线更新，首次登录不抓历史，强杀时尚未交给 handler 的更新也不能保证完整恢复。
没有周期性历史扫描或消息数对账，不能宣称零丢失。

删除更新本身不完全可靠，私聊/普通群常没有 chat_id。此时仅写入 `chat_id=null`、
`chat_id_known=false` 的删除 archive 事件，不按 message ID 猜测和修改任何消息。
因此缺少删除标记不代表消息仍存在。
见 [Telethon MessageDeleted 限制](https://docs.telethon.dev/en/stable/modules/events.html#telethon.events.messagedeleted.MessageDeleted)
和 [catch_up 说明](https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.updates.UpdateMethods.catch_up)。
第一版监听普通文本/媒体新消息及编辑、删除，不记录成员进退等全部 service/chat-action 事件。

## SQLite 查询示例

数据库可用只读模式打开，避免影响记录器：

```bash
sqlite3 -readonly data/messages.db
```

```sql
-- 某 chat 最近 100 条
SELECT * FROM telegram_messages
WHERE chat_id = -1001234567890 AND deleted_at IS NULL
ORDER BY message_date DESC LIMIT 100;

-- 某 sender 最近 100 条
SELECT * FROM telegram_messages
WHERE sender_id = 123456789
ORDER BY message_date DESC LIMIT 100;

-- UTC 时间范围（新加坡当天 09:00–10:00 对应 UTC 01:00–02:00）
SELECT * FROM telegram_messages
WHERE message_date >= '2026-10-02T01:00:00.000000+00:00'
  AND message_date < '2026-10-02T02:00:00.000000+00:00'
ORDER BY message_date;

-- 重点消息
SELECT * FROM telegram_messages
WHERE important = 1 ORDER BY message_date DESC LIMIT 100;

-- Jira 编号或其他子串；查询的是最新文本，不是编辑历史
SELECT chat_id, telegram_message_id, message_date, sender_username, text
FROM telegram_messages WHERE text LIKE '%UG-2530%'
ORDER BY message_date DESC LIMIT 100;

-- 应正常归零；归档故障时可查看待补写数量
SELECT count(*) FROM archive_outbox;
```

已建立 `(chat_id, message_date)`、`(sender_id, message_date)`、`(important, message_date)`
和 `message_date` 索引。第一版使用 `LIKE` 做原样子串搜索，不引入 FTS5 的分词与触发器维护；
`LIKE '%…%'` 会扫描文本，数据量大时可以加时间范围，未来再按需求增加全文索引。
历史编辑内容保存在 JSONL 中；此阶段没有 prompt builder。

## 本地验证

```bash
pip install -r requirements-dev.txt
python3 -m unittest discover -v
ruff check app tests
ruff format --check app tests
```

测试使用临时目录、fake Telegram event/client 及本地构造的 Telethon 类型，不读取真实凭据，
不连接 Telegram、不发送任何群消息。覆盖插入与跨 chat 去重、三种重点匹配、编辑/删除、
乱序事件、JSON fallback、媒体信息、SQL 查询、归档追加与失败恢复、登录模拟、重连、
日志隐私，以及子进程中的 SIGINT/SIGTERM 收尾。没有引入单独的 type checker。
