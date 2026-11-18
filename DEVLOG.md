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
- 决定：删掉 `CallRecord.pending_system_note` 和 `GameState.offstage_attempt_today`。原因：回拨的 `$system_note` 直接作为参数传给当次骗子回合，不需要存；场外突破检查在 `end_day` 里一次性完成，不需要跨请求记状态。
- 决定：`DAY_END` 阶段是瞬时的——`end_day` 在同一次调用里生成当日总结并直接进入下一天的 `DAY_BRIEFING`（或结束游戏）。原因：前端拿到 `day_summary` 和 `briefing` 一次响应就够，多一个需要再次请求的中间阶段没有价值。
- 决定：玩家名字去首尾空格、最长 30 字，为空则用 "Jordan"。原因：规格没写，取最简单的防护。
- 决定：LLM 调用失败时，骗子/真实来电用固定台词 "Sorry, you're breaking up — I'll call back later." 并结束来电；抽取器失败只剩正则命中；策划者失败用固定的 Alex 计划；报告员失败生成纯文本报告。原因：游戏不能因为一次 LLM 故障卡死，规则全在代码里，LLM 只是语言层。
- 决定：`end_day` 要求队列为空，否则返回 409。原因：防止玩家跳过来电。
- 决定：报告 HTML 由 markdown 库生成前先把 `<` 转成 `&lt;`。原因：LLM 输出不可信，避免原始 HTML 进入页面。
- 决定：游戏状态存在内存里，同时 best-effort 写入 `games/{id}.json`；`get` 内存里没有时从磁盘读，id 用正则校验防路径穿越。原因：重启服务后仍能导出，且不引入数据库。
- 决定：CLI 在 LLM 没配置时直接打印问题并以退出码 2 结束，而不是进入游戏。原因：没有 `.env` 时每个角色都会走兜底值，玩起来毫无意义。
- 决定：CLI 第 1 天简报后顺带打印当前难度的 Policy 文本。原因：命令行没有右侧"Policy"标签页，玩家需要在某处看到规则。
- 决定：同一局游戏同时来第二个修改请求时返回 409（非阻塞锁），而不是排队。原因：防止双击"Send"重复发送同一句话，规格没写并发行为。
- 决定：`/api/world` 不返回员工的 `persona` 和 `available` 字段，只返回通讯录上本来就能看到的字段。原因：这两个字段是给 LLM / 规则用的，会暗示谁会在回拨时接电话。
- 决定：`/report` 的响应里额外带上 `cutscene`；`GET /api/game/{id}` 在 `DAY_BRIEFING` 阶段额外带上 `briefing`。原因：页面刷新后前端需要重新显示它们，都是增加字段，不改变规格里的字段。
- 决定：`public_state` 里的 `clock` 在来电中用该来电时间，来电之间用下一通来电时间，当天来电结束后固定显示最后时段 15:50。原因：规格没说来电之间的时钟，不能暴露任何关于来电内容的信息。
- 决定：开始页文案和"LLM 未配置"横幅文案放在 `story.json`，通过 `/api/world` 的 `ui` 字段交给前端，不在 JS 里重复写一份。原因：§11 的文案只有一个来源。
- 决定：前端把当前 game_id 存在 localStorage，刷新页面后自动恢复；游戏不存在（404）则回到开始页。原因：规格没写刷新行为，这是最小的防丢进度办法。
- 决定：来电结束后的"Last call"对话记录由前端在本地保留到下一通来电开始，服务端 `public_state` 不返回已结束来电的内容。原因：`public_state` 的字段是固定的，不能为此泄露更多信息。
- 决定：服务端所有文本在前端用 `textContent` 插入，只有报告员生成的 HTML（已转义 `<`）用 `innerHTML`。原因：避免 LLM 输出造成 XSS。
- 决定：启动时如果 LLM 没配置，只在日志里打印警告，不阻止服务启动；页面顶部显示横幅。原因：没有 `.env` 时仍可浏览界面和兜底台词。
