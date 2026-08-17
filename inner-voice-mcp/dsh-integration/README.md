# Inner Voice × DeepSeek Harness（dsh）接入

> **要装全套三件套**（brain-memory / logic-thinking / inner-voice）？
> 用仓库根目录的三合一插件 [`dsh-plugin/`](../../dsh-plugin/)，一次装齐且
> 自动布线共享记忆库。本目录只用于"只要 inner-voice 单件"的场景。

把本项目作为 dsh 插件接入。**全程本地、零服务器**：

```
dsh（npx 本地起，关终端即停）
 └─ @deepseek-ai/dsh-mcp-client（stdio，参数数组直传）
     └─ python server.py（本项目，本地进程）
         ├─ voice.db（本地 SQLite，WAL）
         └─ daemon.py（懒拉起的本地守护进程，可选）
```

只有 dsh 与 MCP 子进程跟随终端：dsh 关了它们就退。闹钟守护进程是
`start_new_session` 脱离父进程的本机小进程——dsh 关了它仍在走（这正是
"会话全关闹钟不停"的设计），不需要时 `python daemon.py stop` 随时可停，
下次调用自动再拉起。

## 安装（两步）

```bash
# 1. 生成本机的 cordis.patch.yml（自动探测 python 与项目绝对路径）
python dsh-integration/install_dsh.py

# 2. 安装进 dsh 的 web profile 并启动
npx @deepseek-ai/dsh plugin --profile web add /绝对路径/inner-voice-mcp/dsh-integration
npx @deepseek-ai/dsh --profile web
```

启动后在 Web UI（默认 `http://127.0.0.1:3080`）里选择工作区即可。工具以
`mcp__inner-voice__` 前缀出现，如 `mcp__inner-voice__set_task_reminder`。

验证安装（不起 UI）：

```bash
npx @deepseek-ai/dsh --profile web --dump-config   # 应能看到 inner-voice-mcp 行
```

不装进 profile、只想试一次：

```bash
npx @deepseek-ai/dsh web --patch ./dsh-integration/cordis.patch.yml
```

## 前置要求

| 项 | 要求 |
|---|---|
| Node.js | ≥ 22.19（22.x）或 ≥ 24（dsh 官方要求） |
| Python | 3.10+，且装了官方 MCP SDK（`pip install -r requirements.txt`） |
| 令牌 | **本项目不需要任何令牌**；令牌只用于 dsh 调模型（见下） |

## 账号令牌怎么获取（以及哪里才需要它）

先分清两件事：

1. **inner-voice-mcp 本身**：零令牌、零服务器。SQLite 在本地，stdio 进程间
   通信，不访问任何云端。**你不需要给我任何令牌。**
2. **dsh 调用 DeepSeek 模型**：需要 DeepSeek API Key（这是 dsh 的模型侧
   需求，与本项目无关）。

DeepSeek API Key 获取步骤：

1. 打开 [platform.deepseek.com](https://platform.deepseek.com/)，注册并登录；
2. 左侧菜单进入 **API Keys**，点击创建，复制生成的 `sk-...`（**只完整显示
   这一次**，关掉就再也看不到了，务必当场保存）；
3. 填入 dsh：Web UI → **设置 → 模型 → DeepSeek 卡片**，粘贴保存。
   密钥落盘在 `$DSH_HOME/.credentials.yaml`（默认 `~/.dsh/`），页面此后
   只显示脱敏描述符，不再回显明文；
4. 无界面/headless 用法可走环境变量：`export DEEPSEEK_API_KEY=sk-...`。

安全提醒：

- 不要把 key 写进任何会提交到仓库的文件（本项目的 patch 与代码都不含密钥）；
- `.credentials.yaml` 不要提交、不要截图外发；
- 若怀疑泄漏，回平台直接删除该 key 重建即可（DeepSeek key 可随时吊销）。

## 为什么是这个形态（对应"不想开服务器"）

- dsh 用 `npx` 按需启动，关终端即停，符合"用哪个开哪个"；
- 本 MCP 是 stdio 子进程：dsh 启动它才存在，dsh 退出它跟着退；
- 闹钟守护进程虽然独立于会话，但只在本地，占用极小，且 `daemon.py stop`
  可停；`INNER_MIND_NO_DAEMON=1` 可彻底禁用（只用事件型任务提醒时甚至
  不需要它——`report_task_done` 是调用时即时触发的）。

## 常见问题

| 现象 | 处理 |
|---|---|
| 工具列表里没有 `mcp__inner-voice__*` | `--dump-config` 确认 patch 行在；看 dsh 日志里 MCP 连接/发现错误；重跑 `install_dsh.py` |
| MCP 子进程起不来 | 手动验证：`python /绝对路径/server.py` 应能启动（Ctrl+C 退出）；确认 `install_dsh.py` 自检无 ⚠ |
| 换了机器/搬了目录 | 路径写在 patch 里了，重跑 `python dsh-integration/install_dsh.py` 再 `plugin add` |

## 配置格式依据

`cordis.patch.yml` 的行结构（`@deepseek-ai/dsh-mcp-client` + stdio +
`command/args` 数组 + `reconnect`）与官方生态一致；`install_dsh.py` 只是
把本机绝对路径填进去。
