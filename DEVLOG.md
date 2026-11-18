# Dev Log

记录用 AI 辅助开发这个项目的过程：给 AI 的关键提示、AI 写错或设计不好的地方、我是怎么发现和修正的、我做的设计决定。

## 模板
### YYYY-MM-DD — 主题
- 我让 AI 做什么：
- 结果：
- 出的问题 / 我怎么发现的：
- 我的修改 / 决定：

## 实现决定
（Claude Code 在实现过程中遇到规格没写到的细节时，在这里记一行）

- 决定：用 uv 安装 Python 3.12 并在项目内建 `.venv`。原因：本机系统 Python 是 3.10，规格要求 3.11+。
- 决定：`pytest.ini` 增加 `pythonpath = .`。原因：让 `tests/` 能直接 `import app`，不必额外放根目录 conftest。
- 决定：`story.json` 里 Day 2 简报用 `{phishing_warning}` 占位，钓鱼提醒单独存成 `day2_phishing_warning`，由引擎按难度决定是否填入。原因：规格要求 Easy/Normal 才有这句。
- 决定：骗子被转接到 IT 但判定失败时，来电结果记为 `refused_by_it`（不是 `transferred_away`）。原因：规格只给了 `transferred_away` 表示"转给其他人"，策划者看历史摘要时需要区分"被 IT 拒绝"和"被前台转走"。
- 决定：回拨识破时只把 `claimed_identity` 加入 `burned_identities`；骗子没有冒充任何人（只是提到了某同事名字）时不烧任何身份。原因：`burned_identities` 存的是可冒充的员工/供应商 id，没有冒充就没有身份可烧。
- 决定：真实来电的回拨/转接/留言结果需要的 `accept_transfer_to`、`message_ok` 通过 `CallRecord.legit_id` 到 `legit_calls.json` 里查，不复制进 `CallRecord`。原因：数据只有一份来源，避免不一致。
- 决定：`rules.py` 里的函数只返回结果对象（如 `CallbackResult`、`TransferResult`、`FlagResult`），不修改游戏状态，状态更新由 `engine.py` 统一做。原因：规格要求规则是纯函数，便于单元测试。
- 决定：`LLM_BASE_URL` 未设置时不在代码里写默认地址，直接交给 openai SDK 的默认值。原因：§17 要求 `app/` 里没有硬编码的服务商名称（`.env.example` 里的 OpenAI 地址只是示例）。
