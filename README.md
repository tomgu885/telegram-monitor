# telegram-monitor

Telegram Bot 菜单学习 CLI，使用自己的普通 Telegram 账号、Telethon MTProto、Typer 和严格 YAML。
提供登录、观察消息、安全菜单浏览，以及按已核实路径执行的单服务 `deploy`。
`deploy-many`、`services`、`service show` 和自动 `learn` 留待后续阶段；当前由 Codex 通过 CLI 逐层学习。
不接入 Jira，不依赖 GUI、截图或 AI 运行部署；实际环境路径保存在本机环境 YAML。

原有 `python -m app` 消息记录器及截图功能保留，见本文后半部分。两个入口共用根目录
`config.yaml`、`.env` 和 `data/telegram.session`；有效 session 不需要重复登录。

也可以统一使用 `python3 -m app` 入口，安装后在项目目录运行：

```bash
python3 -m app                          # 不带参数：启动原有消息记录器
python3 -m app --check-config           # 检查原有记录器配置
python3 -m app --help                   # 查看新 CLI 命令
python3 -m app auth login               # 新 CLI 首次登录
python3 -m app auth status --json
python3 -m app menu open --env testa --json
python3 -m app menu buttons --json
python3 -m app watch --chat testa --sender ugopsbot --json-lines
```

本文所有 `telegram-monitor ...` 均可等价改写为 `python3 -m app ...` 或
`python3 -m telegram_monitor ...`。不带参数的 `python3 -m app` 保留原有启动行为，
两个入口使用同一份 `config.yaml` / `data/telegram.session`；环境菜单分别位于根目录
`./testa.yaml`、`./uat.yaml`。共用 session 的两个进程必须轮流运行。

## CLI 安装和配置

在仓库目录执行，Python 3.12+，支持 macOS / Linux；已有 `.venv` 可直接复用：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
telegram-monitor --help
```

仅运行时可用 `pip install -e .`；`requirements.txt` 仍供旧记录器使用。
首次复制示例，已有文件不要覆盖：

```bash
cp config.example.yaml config.yaml
cp testa.example.yaml testa.yaml
cp uat.example.yaml uat.yaml
chmod 600 config.yaml testa.yaml uat.yaml
```

从 [my.telegram.org](https://my.telegram.org/) 的 API development tools 获取个人应用 API 信息。
在项目根目录 `.env` 中填入以下两项（保留其它现有配置），或设置同名环境变量：

```dotenv
TELEGRAM_API_ID=你的数字API_ID
TELEGRAM_API_HASH=你的32位API_HASH
```

环境变量优先于 `.env`；将 `.env` 设为 `0600`。CLI 不要求 `TELEGRAM_PHONE`，登录时会隐藏输入。
`api_id` 也可直接配置在 YAML；`api_hash` 只通过 `api_hash_env` 指向的变量读取，不写进 YAML。

已有 `config.yaml` 时保留 `telegram.watch_all`、`important`、`screenshot` 等设置，仅合并
下面的字段；不要覆盖原文件。testa / uat 的群 ID 统一放在 `targets`（以下 ID 需自行填写）：

```yaml
telegram:
  api_id_env: TELEGRAM_API_ID
  api_hash_env: TELEGRAM_API_HASH
  session_file: data/telegram.session
targets:
  testa:
    chat_id: null  # 填入 testa 发布群数字 ID
    bot_username: ugopsbot
  uat:
    chat_id: null  # 填入 uat 发布群数字 ID
    bot_username: uguatdeploybot
defaults:
  message_timeout_seconds: 30
  deploy_timeout_seconds: 900
  poll_interval_seconds: 1
```

账号必须已加入目标群；群 ID 使用 Telethon 带符号 ID，Bot 用户名可带 `@`。
Bot 会解析成 ID 并验证 Bot 身份；只操作该群中该 Bot 的消息，不自动猜测目标。
`deploy_timeout_seconds` 和 `poll_interval_seconds` 为后续阶段预留，本阶段使用事件监听。
环境 YAML 与 `config.yaml` 同目录；相对 session 路径和 `.env` 均以该目录为根目录解析。
在其它工作目录运行时用 `telegram-monitor --config /绝对路径/config.yaml ...`。
仍兼容显式 `--config /项目/config/telegram.yaml` 的旧布局：它的相对 session / `.env` 从项目目录解析。

根目录 `testa.yaml` / `uat.yaml` 设置 `environment`、`entry` 和 `services`。
默认引用 `targets` 中与环境同名的群，也可用 `target` 显式指定；群 ID 不放在菜单文件。
每个服务的完整 `menu_path` 也放在对应环境文件，支持字符串（默认 contains）和
`{match: exact|contains|regex, text: ...}`。普通菜单命令只浏览，显式 `deploy` 才执行最终发布按钮；
`services: {}` 表示尚未学习，不能据截图补全最终发布路径。文件结构：

```yaml
environment: testa
entry:
  command: /menu
  safe_buttons: []
services: {}
```

示例 `/menu` 是待核实入口，先确认真实 Bot 命令；群内必要时用 `/menu@实际Bot用户名`。
`safe_buttons` 初始为空，`back_button` 初始未配置，没有默认服务映射。

```bash
telegram-monitor config validate
telegram-monitor config validate --env testa --json
telegram-monitor auth login
telegram-monitor auth status --json
```

`config validate` 不联网、不读取凭据，检查 schema、重复 YAML 键、缺失/未知字段、
超时、环境名称、目标引用、空 `menu_path` 与非法正则；不验证实际 Bot 可达性。
空群 ID 不影响登录状态查询，但会阻止该环境的菜单操作和配置验收。
省略 `--env` 会检查配置目录内所有非 example 环境文件。
首次登录顺序为手机号 → Telegram 验证码 → 如有 2FA 则密码，全部隐藏输入、不记录到日志。
需要交互式终端；成功后保存 `data/telegram.session`。已有记录器 session 时，先执行
`python3 -m app auth status --json`，返回 `authorized: true` 就无需 `auth login`。
只有缺少 session 或认证失效时才需重新登录；即使主动运行 login，有效 session 也不会再索要验证码。

## CLI 命令和 Codex 学习流程

操作结果始终为 stdout JSON，`--json` 保留为显式机器接口；日志和提示走 stderr。
`watch` 始终输出 JSON Lines。全局 `--verbose` / `--config` 放在命令前。

```bash
telegram-monitor inspect --chat testa --json
telegram-monitor watch --chat testa --sender ugopsbot --json-lines
telegram-monitor menu open --env testa --json
telegram-monitor menu buttons --json
```

`inspect` 只读最近 10 条目标 Bot 消息（可用 `--limit` 调整）。watch 观察运行期间的新消息和编辑，
不指定 `--sender` 时输出群内全部发送者，Ctrl+C 停止。
配置多个群时，inspect / watch 需用 `--chat testa` 或 `--chat uat` 明确选择；
旧配置存在 `deployment` 时保留其默认行为，只有一个群时可省略 `--chat`。`menu open` 会发送该环境的入口命令。
消息 JSON 含 `chat_id`、`message_id`、`date`、`sender_id`、`sender_username`、`reply_to_msg_id`、
`text`、`edit_date`、`buttons`；按钮含 `row`、`column`、`text`、`kind`。
watch 另有 `event`（`message_new` / `message_edited`）；不输出 callback data 或认证信息。

Codex 查看真实标签，确认某按钮仅用于浏览后，将其完整标签（包含 emoji）写入该环境的
`entry.safe_buttons`，重新 `config validate --env testa`，再逐层点击：

```bash
telegram-monitor menu click --text '实际导航标签' --json
telegram-monitor menu click --index 0 --json
telegram-monitor menu click --message-id 18273 --text '实际导航标签' --match exact --json
telegram-monitor menu click --text '^实际标签$' --match regex --json
telegram-monitor menu buttons --message-id 18274 --env testa --json
telegram-monitor menu back --json
telegram-monitor menu reset --env testa --json
```

上述 ID/标签仅演示语法，不能当作真实菜单。`--text` 默认 contains，也支持 exact / regex；
`--index` 从 0 开始按行展开；文本和 index 二选一。匹配多处返回 `unexpected_menu`，不会猜测。
`back` 需配置 `entry.back_button: {match: exact, text: 实际返回标签}`，并把完整标签加入
`safe_buttons`；`reset` 重新发送入口命令，不点击“重置”按钮。
点击结果为 `{"status":"ok","clicked":"实际标签","next_message":{...}}`。

菜单 cursor 持久化在 session 目录，后续命令可省略环境/message ID；切换环境时先重新 open，
或明确指定环境和 message ID。更换群/Bot 后旧 cursor 被拒绝。`buttons` 重新获取并刷新当前消息；
上次观察后菜单变化会阻止直接 click。点击 RPC 前再次核对文字、按钮及 callback 摘要。

**普通 menu click 无危险动作开关。** 非允许列表标签、发布/部署/删除/回滚/重启/确认等危险标签、
URL/登录/付款/联系人/位置等非普通导航按钮均返回 `action_not_allowed`；危险标签加入允许列表也不点击。
只信任已核实行为的 Bot：标签允许列表无法证明 Bot 后端没有副作用。遇到未知行为就停下核实。
点击前 stderr 记录环境、service（普通菜单浏览为 `-`）、message ID 与转义后的按钮文字。

收到 `button_not_found` / `menu_not_found` / `unexpected_menu`，先 `inspect` / `menu open` /
`menu buttons` 观察真实菜单，核实配置并 validate，不先修改代码、不猜测其它服务按钮。

## 已核实路径的单服务部署

```bash
python3 -m app deploy --env testa --service payment-rpc --json
```

此命令会真实发布，须有该环境/服务的操作授权。读取本机 `testa.yaml` 中的
`services.payment-rpc.menu_path`，从入口重放导航，最后点击精确匹配的发布按钮。
前面的每一步仍须属于 `entry.safe_buttons`；最后一步必须 `match: exact`，并拒绝删除、回滚、
重启和批量动作。发布前，页面必须包含 `services.payment-rpc.expected_context` 的全部文字，
用它锁定服务、分支和环境；缺少上下文、按钮歧义或菜单变化都停止，不猜测、不重试发布动作。
支持用服务的 `aliases` 解析名称；尚未核实的 uat 或其他服务不能直接部署。

在同一个环境 YAML 配置完成规则，结构示例（菜单和通知文字必须按真实 Bot 核实）：

```yaml
deployment_result:
  sender_username: your_deploy_bot
  timeout_seconds: 900
  success:
    contains: ["部署成功"]
  failure:
    contains: ["部署失败", "FAILURE", "ABORTED"]
  correlation:
    payment-rpc:
      service_patterns:
        - '(?m)^服务: payment-rpc$'
      key_patterns:
        build_id: '构建号: (?P<value>\d+)'
```

success / failure 均支持 `contains` 和 `regex`，至少一个非空规则；failure 优先，避免把含有
“完成时间”的失败通知判作成功。关联必须满足群、Bot、触发前最后消息 ID 和开始时间过滤，
并匹配当前服务、已知 reply 链或已绑定的构建标识。Bot 编辑原菜单时可以提供构建标识，
但旧消息不能作为完成结果；捕获构建标识后，其他构建不能满足本次等待。
同一服务无 reply、无构建标识的并行外部部署仍无法绝对区分，应串行操作该 Bot。

发布前订阅消息，最终通知无需按钮。回调超时继续等待，绝不自动重复点击；缺少终态超时返回
`status: timeout` / exit 11，不代表 Bot 没有执行。需要额外确认时停止并返回当前菜单，交给操作者
核实流程。异常或中断可能已经触发发布，先 inspect / 查 Jenkins，不直接重新 deploy。
成功返回 `status: success` / exit 0，失败返回 `status: failed` / exit 10；JSON 包含服务、时间、
耗时、触发与结果消息 ID、完整结果文字、命中规则以及 correlation_keys。

`data/deploy-history.jsonl` 保存触发意图和终态；异常时保存 `unknown`，不保存认证信息。
复用现有 session/recorder 排他锁，当前同 session 的所有部署串行；并发 CLI 返回 exit 43。
不实现跨机器锁、自动重试、取消 Jenkins 或部署后业务验收。

## 等待、并发和边界

动作前注册新消息和编辑监听，支持 Bot 编辑原菜单；过滤群、Bot ID、旧消息 ID/时间及明确指向
其它消息的 reply。等待只接受包含按钮的菜单；仅 callback 提示、无新菜单或菜单未变化时超时。
超时返回 `menu_not_found`，附带 `action_may_have_completed: true`：动作可能已经执行，不自动重试。
点击尝试后旧 cursor 清除，失败时先 inspect 查看实际状态。限流直接返回等待秒数，不静默重试点击。
按钮 API 参考 [Telethon Message.click](https://docs.telethon.dev/en/stable/modules/custom.html#telethon.tl.custom.message.Message.click)。

**无 reply 的 Bot 新菜单无法证明属于当前操作者。** 同群/Bot 请串行学习，避免其他人同时操作。
菜单浏览过滤不等于上述部署关联，不能单凭菜单响应判定部署成功。watch 不保证补收历史。

默认复用记录器的 `data/telegram.session`，CLI 同时遵守现有 `data/recorder.lock`。
记录器正在运行时，CLI 返回 `session_busy`；应先正常停止记录器，再运行 CLI，完成后可重新启动记录器。
watch 同样持续占用 session，先停止 watch 再操作菜单。如需同时观察，另建配置目录与独立 session，
分别登录，不复制正在运行的 session。锁不协调其他客户端或其他机器。
状态目录 `0700`、新 session/cursor/lock 文件 `0600`；实际 YAML 和运行状态均被 Git 忽略。

## 稳定 exit code

| Exit | error / 含义 |
| --- | --- |
| 0 | 成功，`status: ok`；部署为 `status: success` |
| 2 | CLI 语法错误，Typer 使用说明走 stderr |
| 10 / 11 / 12 | deployment_failed / deploy_timeout / 预留 deployment_already_running |
| 20 / 21 / 22 | environment_not_found / service_not_found / invalid_config |
| 30 / 31 / 32 | menu_not_found / button_not_found / unexpected_menu |
| 33 | action_not_allowed |
| 40 / 41 / 42 | telegram_connection_error / telegram_auth_error / telegram_rate_limit |
| 43 | session_busy |
| 50 | internal_error |
| 130 | Ctrl+C 或 SIGTERM 中断，`status: interrupted` |

错误 JSON 为 `{"status":"error","error":"稳定名称",...}`，不输出底层异常正文或凭据。

## 文件和验证

- `src/telegram_monitor/`：CLI、配置、消息模型、异常、按钮 matcher、菜单业务层、Telethon IO、私有状态。
- `deployer.py` / `watcher.py`：单服务部署、结果关联、超时和历史。
- `config.example.yaml`、`testa.example.yaml`、`uat.example.yaml`：共用配置和环境菜单骨架。
- `data/`：复用 session、锁和菜单 cursor；`tests/phase1/`：离线测试。
- `pyproject.toml`：安装、命令入口、pytest/Ruff 配置；项目未配置 mypy。

```bash
python -m pytest -q
ruff check app src tests
ruff format --check app src tests
```

测试全部 mock Telegram，覆盖 schema、匹配/安全、菜单状态、新消息/编辑、竞态、无关消息过滤、
超时清理、登录/2FA、JSON/exit code 和 session 锁；不读取真实凭据，不发送群消息。
真实验收需配置目标后完成 `auth status → menu open → buttons → 安全导航 click`，再观察 watch。
只有核实该环境的完整路径和结果格式后，才可以在获得授权时执行 deploy；不能用 testa 的验证代替 uat。

## 原有 Telegram 本地消息记录器

Python 3.12+、Telethon MTProto 普通用户账号、SQLite 和本地 JSONL。支持 macOS / Linux，
使用系统文件锁防止同一个 data 目录被多个进程同时写入。

默认被动记录 Telegram 下发的新消息、编辑和可识别的删除事件。可在 macOS 27 上显式启用
授权用户请求主显示器截图，并用当前账号私聊返回图片消息。没有 Bot API、Codex/OpenAI 调用
或语义判断；不转发、reaction、标记已读，也不下载媒体或递归获取被回复的消息。

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

记录器使用 Telethon、python-dotenv、PyYAML；同仓 CLI 另使用 Typer 和 Pydantic。
不需要 Docker 或外部数据库。

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
- 私聊通过 `event.respond(file=..., reply_to=event.id, force_document=False)` 发送图片消息，
  可在聊天中直接预览；群聊触发后的私发同样使用图片消息。图片可能被 Telegram 压缩，
  不保证保留原始 PNG 格式和画质。本地仍生成临时 PNG，发送后清理。
  不附带机器名、用户名、完整本地路径或额外 caption。
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

1. 授权用户私聊发 `截图`，应在原私聊收到回复该消息的图片预览（非文件附件）；超过 5 秒再发 `screenshot`
   也应成功。发送后检查 `data/screenshots` 无残留 PNG。
2. 未授权用户私聊发 `截图`，应无截图、无任何回复；日志可见 ignored metadata。
3. 授权用户在群内发 `截图`（`allow_groups: false`），应无截图或回复。
4. 同一用户在 5 秒内连续请求，第二次应静默忽略；普通文字不触发。
5. 连接多个显示器，使用扩展桌面，在主屏和副屏各放不同的测试标记。
   请求截图，确认图片只有主屏内容，没有副屏标记或多屏拼接。
6. 配置授权用户同时命中 `important.user_ids`，确认请求存在于 SQLite、普通 JSONL
   与 important JSONL；截图失败也应留下请求记录。

自动测试全部 mock `subprocess.run` 和 Telegram 发送，不会真实截屏、上传或连接 Telegram。
真实 Screen Recording 权限、Telegram 收图和多显示器效果需按以上步骤验收。

## 目录与主要模块

```text
telegram-monitor/
├── app/
│   ├── __init__.py
│   ├── __main__.py          # 统一入口：CLI 子命令分发；原有记录器启动与关闭
│   ├── config.py            # .env/YAML 读取、校验、OR 匹配
│   ├── telegram_client.py   # 登录、新消息/编辑/删除监听、重连、信号
│   ├── screenshot.py        # 固定主显示器截图、临时文件清理
│   ├── screenshot_handler.py # 授权、精确匹配、冷却、并发锁与私聊发送
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
日志隐私、截图授权/命令/群聊限制/冷却/并发/失败与取消清理，以及子进程中的 SIGINT/SIGTERM 收尾。没有引入单独的 type checker。
