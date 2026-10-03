# Telegram 本地消息记录器：部署和使用说明

Python 3.12+、Telethon MTProto 普通用户账号、SQLite 和本地 JSONL。支持 macOS / Linux，
使用系统文件锁防止同一个 data 目录被多个进程同时写入。

默认被动记录 Telegram 下发的新消息、编辑和可识别的删除事件。可在 macOS 27 上显式启用
授权用户请求主显示器截图，并用当前账号私聊返回 PNG。没有 Bot API、Codex/OpenAI 调用
或语义判断；不转发、reaction、标记已读，也不下载媒体或递归获取被回复的消息。

## 安装与启动

本文所有命令都在项目根目录执行，不是在 `docs/` 内执行。当前项目路径为
`/Users/jack/python3/telegram-monitor`；部署到其他目录时，将下面的路径替换为实际路径。
先确认 `python3 --version` 为 3.12 或以上，并确保运行机器能够访问 Telegram 网络。

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
test -f .env || cp .env.example .env
test -f config.yaml || cp config.example.yaml config.yaml
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
  启用截图后，符合截图授权、来源与命令规则的请求始终记录，详见截图功能说明。
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

### 6. 可选：后台运行

先在前台完成第一次登录，看到 `Listening for messages...` 后按 `Ctrl+C` 正常退出。
确认没有其他实例运行，再在项目根目录执行：

```bash
umask 077
mkdir -p data
nohup .venv/bin/python -m app > data/recorder.log 2>&1 < /dev/null &
echo $! > data/recorder.pid
```

这里直接使用虚拟环境解释器，无需保持 virtualenv 激活。日志写入被 Git 忽略的 `data/`。
上述命令每次启动会覆盖旧的控制台日志，但不会覆盖 SQLite 或消息 JSONL；需要旧日志时先备份。
`nohup` 不负责进程崩溃后的自动拉起或机器重启后的自启动，网络重连由程序自身处理。
机器休眠或断网期间不会实时采集，恢复后的补收仍受下文说明的限制约束。

查看状态与日志：

```bash
ps -p "$(cat data/recorder.pid)" -o pid=,command=
tail -n 50 data/recorder.log
```

停止前，先确认上面显示的 PID 仍对应本项目记录器，避免 PID 文件过期后误停其他进程。
确认后发送 `SIGTERM`：

```bash
kill -TERM "$(cat data/recorder.pid)"
```

等待进程退出，并检查日志中的 `SQLite closed; recorder stopped.`。
同时检查有无最终归档失败或未提交事件的错误；关闭提示本身不代表所有归档都成功。
不要用 `kill -9` 作为正常停止方式。若后台出现认证失败，先停止后台实例，再在前台重新登录。

## 日常使用与排查

| 情况 | 处理方法 |
| --- | --- |
| 提示缺少 `TELEGRAM_API_ID` 等配置 | 检查项目根目录 `.env`，填写后运行 `python3 -m app --check-config`。该命令只验证格式，不验证账号是否有效。 |
| 提示 data 目录已有实例 | 检查已有进程；同一 session 和 data 目录只运行一个记录器，不通过删除锁文件绕过检查。 |
| 修改关注的 chat/user | 编辑 `config.yaml`，正常停止并重启；不需要删除 session 或数据库。 |
| 程序运行但没有新记录 | 检查账号是否收到新消息，以及 `watch_all` 与关注列表；第一次启动不抓取历史消息。 |
| 网络断开或限流 | 查看控制台状态；程序自动重连，并按 Telegram 返回的限流时间等待。 |
| 登录失效或首次登录需要终端 | 使用前台交互式终端运行并完成认证，再转后台运行。不要将验证码或 2FA 密码写入脚本。 |
| SQLite 写入或归档失败 | 检查磁盘空间、目录权限和日志；不要清空 `archive_outbox`，其中可能有尚未写入 JSONL 的事件。 |
| 出现 `IncompleteArchiveError` | 按下文的残缺 JSONL 恢复说明处理，保留原文件与数据库副本。 |

确认采集结果时，可使用下文的只读 SQLite 查询，检查消息数量、重点标记和待归档数量。
JSONL 文件保存原文；日常检查优先使用只读 SQL 或统计文件大小，避免将聊天内容直接输出到共享终端。

## 可选：Telegram 请求主显示器截图（macOS 27）

截图功能使用当前登录的 Telethon 普通用户账号；没有 AI、LLM、OpenAI API 或新增运行时依赖。
旧配置无需修改即可继续运行，缺少 `screenshot` 时功能关闭。要启用，在项目根目录
`config.yaml` 添加以下配置，并将示例 ID 换成实际授权用户的整数 ID：

```yaml
screenshot:
  enabled: true
  screenshot_uids:
    - 123456789
    - 987654321
  commands:
    - "截图"
    - "screenshot"
  temp_dir: "data/screenshots"
  allow_groups: false
  keep_failed: false
  cooldown_seconds: 5
```

- 示例配置文件默认 `enabled: false`、授权列表为空；必须主动开启并填写授权 ID。
  `important.user_ids` 不授予截图权限，两份列表各自独立。
- 消息去掉首尾空白并转小写后，必须与命令完全相等。`截图`、`SCREENSHOT` 可以匹配；
  `帮我截图一下`、`please screenshot` 不匹配。不做语义理解。
- 仅处理授权用户的入站新消息，默认仅私聊；自己发出的消息、编辑、重复更新不触发。
  未授权请求、冷却期内请求均静默忽略，不发送权限或限流提示。
- 显式设置 `allow_groups: true` 才允许普通群/超级群触发，截图仍只私发给请求者，
  不发到群内，也不把群消息 ID 用作私聊回复 ID。广播频道不支持触发。
- 同一 sender 默认 5 秒内最多一次，使用单调时钟，排队后的执行也检查冷却。
  全局锁覆盖截图、上传和清理，多个请求依次执行。冷却状态保存在内存，重启后重置。
- 私聊通过 `event.respond(file=..., reply_to=event.id, force_document=True)` 发送原始 PNG
  文件，不附带机器名、用户名、完整本地路径或额外 caption。
  [Telethon 文件发送参数](https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.uploads.UploadMethods.send_file)。
- 请求先写入 SQLite 和持久化 JSONL outbox，然后启动截图任务；JSONL 继续由后台归档。
  命中 `important` 的请求还会进入 important archive。即使 `watch_all: false`，符合截图
  授权、来源与命令规则的新请求也会记录，以保留操作依据；普通消息仍遵循原有过滤规则。
- 截图失败不撤销已保存的消息，也不停止监听器。截图与发送在后台处理，消息记录继续运行。
  截图动作不是持久化任务：退出会取消未完成任务，已记录的请求不会在重启后重试。
  尚未记录的离线新消息可能通过现有 `catch_up` 机制触发一次截图。

只调用固定命令 `/usr/sbin/screencapture -m -x <程序生成的临时PNG>`，不使用 `shell=True`。
`-m` 只截主显示器，`-x` 静音；文件名唯一，不接受 Telegram 指定的路径、显示器或参数。
截图命令超时为 15 秒，发送超时为 60 秒；不实现其他平台截图。
`temp_dir` 相对项目根目录解析，与启动目录无关，也支持本机配置的绝对路径。
新建截图目录权限为 `0700`，PNG 为 `0600`。默认目录在 Git 忽略的 `data/` 内，
自定义目录时也应放在 Git 忽略且访问受限的位置。

正常完成、捕获失败、发送失败或任务取消都会尝试清理临时文件，失败只记录脱敏错误。
仅当 `keep_failed: true` 时保留已生成但发送失败的 PNG 供本机调试，成功发送仍删除。
取消时会等待正在执行的截图子进程结束后再清理；强杀/断电无法执行 `finally`，
可能留下文件，需要停机后检查临时目录。日志只记录 sender/message ID、大小和状态，路径脱敏。

### 屏幕录制权限

首次截图可能需要 macOS 授权。到 **System Settings → Privacy & Security →
Screen & System Audio Recording**，为实际运行 Python 的宿主程序授权，例如 Terminal、
iTerm 或 Ghostty；以后封装为 app 时给对应 app 授权。按系统提示重启宿主程序。
程序不会绕过权限系统，捕获失败会在日志提示检查 Screen Recording permission。
[Apple 权限说明](https://support.apple.com/en-gb/guide/mac-help/mchl592e5686/mac)。

### 手动验收

在运行目录执行 `python3 -m app --check-config`，再执行 `python3 -m app`。
配置修改后需要重启。先使用无敏感内容的测试桌面：

1. 授权用户私聊发 `截图`，应在原私聊收到回复该消息的 PNG；超过 5 秒再发 `screenshot`
   也应成功。发送后检查 `data/screenshots` 无残留 PNG。
2. 未授权用户私聊发 `截图`，应无截图、无任何回复；日志可见 ignored metadata。
3. 授权用户在群内发 `截图`（`allow_groups: false`），应无截图或回复。
4. 同一用户在 5 秒内连续请求，第二次应静默忽略；普通文字不触发。
5. 连接多个显示器，使用扩展桌面，在主屏和副屏各放不同的测试标记。
   请求截图，确认 PNG 只有主屏内容，没有副屏标记或多屏拼接。
6. 配置授权用户同时命中 `important.user_ids`，确认请求存在于 SQLite、普通 JSONL
   与 important JSONL；截图失败也应留下请求记录。

自动测试全部 mock `subprocess.run` 和 Telegram 发送，不会真实截屏、上传或连接 Telegram。
真实 Screen Recording 权限、Telegram 收图和多显示器效果需按以上步骤验收。

## 目录与主要模块

```text
telegram-monitor/
├── app/
│   ├── __init__.py
│   ├── __main__.py          # 启动、单实例锁、最终归档与关闭
│   ├── config.py            # .env/YAML 读取、校验、OR 匹配
│   ├── telegram_client.py   # 登录、新消息/编辑/删除监听、重连、信号
│   ├── screenshot.py        # 固定主显示器截图、临时文件清理
│   ├── screenshot_handler.py # 授权、精确匹配、冷却、并发锁与私聊发送
│   ├── models.py            # 原文/身份/媒体解析、UTC 时间、JSON normalization
│   ├── storage.py           # SQLite 最新状态、索引、事务待归档队列
│   ├── archive.py           # append-only JSONL + fsync
│   └── logging_config.py    # 不泄露消息/凭据的日志
├── docs/
│   └── README.md            # 本部署和使用说明
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
    ├── screenshots/         # 临时 PNG，默认发送后清理
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
后台每批最多提取 100 个待归档事件；满批时继续处理，未满批时等待 1 秒，每个 JSONL append 都 flush + fsync，成功后再
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
日志隐私、截图授权/命令/群聊限制/冷却/并发/失败与取消清理，以及子进程中的 SIGINT/SIGTERM 收尾。没有引入单独的 type checker。
