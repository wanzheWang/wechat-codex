# WeChat Codex MVP

This is a local-first MVP for connecting a WeChat Official Account test account to Codex CLI.

The implementation uses only Python standard library modules:

- `gateway/server.py`: receives WeChat callback messages, creates tasks, exposes runner APIs.
- `runner/runner.py`: polls tasks from the gateway and executes `codex exec`.
- `shared/`: command parsing, task storage, WeChat signature/XML helpers.
- `data/`: local task JSON and runner logs.

## Current Scope

Implemented:

- WeChat Official Account plain-mode URL verification.
- WeChat text message parsing.
- Commands:
  - `任务 工程结构 项目 artical 只读 总结当前工程结构`
  - `任务 工程结构 分类 微信接入 项目 artical 只读 总结当前工程结构`
  - `任务 修复按钮 项目 artical 修改 修复某个问题`
  - `项目 artical 只读 总结当前工程结构`
  - `项目 artical 修改 修复某个问题`
  - `任务列表`
  - `分类列表`
  - `分类 <分类名>`
  - `归类 <任务名或ID> <分类名>`
  - `取消分类 <任务名或ID>`
  - `状态 <任务名或ID>`
  - `全文 <任务名或ID>`
  - `全文 <任务名或ID> 2`
  - `文件 <任务名或ID>`
  - `完整 <任务名或ID>`
  - `批准 <任务名或ID>`
  - `取消 <任务名或ID>`
  - `命名 <任务名或ID> <新任务名>`
- Local JSON task queue.
- Runner polling.
- Runner dry-run mode.
- Real runner mode through `codex exec --json`.
- `codex exec --skip-git-repo-check` for projects that are not Git repositories yet.
- Long WeChat replies are truncated by `WECHAT_MAX_REPLY_CHARS`.

Not implemented yet:

- Encrypted WeChat message mode.
- Active customer-service message push.
- Cloud deployment.
- Persistent database.
- Web dashboard.

## 1. Configure Environment

Copy `.env.example` and export the values in your shell, or source them with your preferred env loader.

For local testing, the defaults are enough except `PROJECT_ARTICAL`:

```bash
export RUNNER_SHARED_SECRET=<random-runner-secret>
export WECHAT_TOKEN=dev-token
export PROJECT_ARTICAL=/path/to/your/project
```

When connecting to a real WeChat test account, also set:

```bash
export WECHAT_TOKEN=<the-token-you-entered-in-wechat-admin>
export ALLOWED_WECHAT_OPENIDS=<your-openid>
```

## 2. Start Gateway

From this directory:

```bash
python3 gateway/server.py
```

It listens on:

```text
http://127.0.0.1:3000
```

Health check:

```bash
curl http://127.0.0.1:3000/healthz
```

## 3. Expose Gateway With ngrok

For quick WeChat callback testing:

```bash
ngrok http 3000
```

Copy the HTTPS URL and set the WeChat Official Account test-account callback URL:

```text
https://xxxx.ngrok.app/wechat/callback
```

Use the same `WECHAT_TOKEN` value in both your shell and WeChat admin.

## 4. Run Runner

Dry run, one task:

```bash
python3 runner/runner.py --once --dry-run
```

Real Codex execution, one task:

```bash
python3 runner/runner.py --once
```

Long-running runner:

```bash
python3 runner/runner.py
```

## 5. WeChat Commands

Read-only task:

```text
项目 artical 只读 总结当前工程结构
```

Named read-only task:

```text
任务 工程结构 项目 artical 只读 总结当前工程结构
```

Named read-only task in a category:

```text
任务 工程结构 分类 微信接入 项目 artical 只读 总结当前工程结构
```

Write task:

```text
项目 artical 修改 修复登录页按钮样式问题
```

Named write task:

```text
任务 修复按钮 项目 artical 修改 修复登录页按钮样式问题
```

Write tasks require approval:

```text
批准 <任务名或ID>
```

Check status:

```text
状态 <任务名或ID>
```

Read the complete answer in WeChat by chunks:

```text
全文 <任务名或ID>
全文 <任务名或ID> 2
```

Get a mobile-viewable Markdown file link:

```text
文件 <任务名或ID>
```

Show the local full-result file path:

```text
完整 <任务名或ID>
```

List recent tasks:

```text
任务列表
```

List categories:

```text
分类列表
```

List tasks in one category:

```text
分类 微信接入
```

Move a task into a category:

```text
归类 工程结构 微信接入
```

Remove a task from its category:

```text
取消分类 工程结构
```

Cancel:

```text
取消 <任务名或ID>
```

Rename an existing task:

```text
命名 <任务名或ID> <新任务名>
```

## 6. Local Test Without WeChat

Start gateway:

```bash
python3 gateway/server.py
```

In another terminal, simulate a WeChat text message by POSTing XML to `/wechat/callback`.

Then run:

```bash
python3 runner/runner.py --once --dry-run
```

Finally query the task status by sending `状态 <任务ID>` through the same XML simulation.

## Privacy Notes

Do not commit runtime data or credentials:

- `data/tasks.json`
- `data/logs/`
- `data/results/`
- `.env`
- WeChat `AppSecret`, `Token`, `EncodingAESKey`, `OPENID`
- Cloudflare credentials or downloaded binaries

These are ignored by `.gitignore`.
