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
- 决定：`human_leaks` 为空表示"还没标注"，评估脚本跳过；人工确认没有泄露时填 `none`。原因：导出的 CSV 初始全是空白，如果把空白当成"无泄露"，所有未标注的行都会被算成假阳性。
- 决定：`--models` 通过设置环境变量 `MODEL_EXTRACTOR` 切换模型，对每个模型同时报告"仅 LLM"和"LLM+字符串匹配"两组指标。原因：`config.model_for` 本来就读这个变量，不用改配置代码；两组指标能看出字符串匹配兜底带来的提升。
- 决定：`tests/conftest.py` 的自动隔离 fixture 对 `llm` 标记的测试不再换成测试配置和禁网客户端，直接使用 `.env`；没配置时测试自动跳过。原因：否则 `pytest -m llm` 永远连不上真实 LLM。
- 观察：规格 §13.6 的用例 "Sure, it's SR-20417." 期望只有 `cfo_employee_id`，但 `emp_id_format` 的 "SR-" 模式也会命中它，所以字符串匹配层在这条上必然多报一个。数据按规格原样保留，这条会拉低精确率，评估时要知道原因。
- 决定：回复预算——每天共享 12 条（`DAILY_REPLY_BUDGET`，每天开始时重置，存在 `GameState.replies_left`），每通来电最多 5 条（`MAX_PLAYER_TURNS_PER_CALL`，原为 8）。池子用完后 `say` 返回 409，玩家仍可转接、留言、回拨、挂断。原因：玩家要在"试探骗子"和"留回复给真实来电"之间取舍；回拨只扣耐心，不扣回复池。
- 决定：给来电者提示词里的 `$patience` 传 `min(耐心, 本通剩余回复数)`。原因：每通上限只有 5 条，来电者需要知道自己快没时间了，才会在结束前把话说完。
- 决定：`public_state` 新增 `replies_left_today` 和 `active_call.replies_left_in_call`（由每通上限算出，两者都与是否骗子无关）。原因：前端需要显示预算，且不能泄露来电性质。
- 观察：每通上限降到 5 之后，耐心只有在回拨（扣 3）或真实来电耐心 < 5 时才会先耗尽。耐心机制变弱了，但没有删，因为回拨还要用它。
- 决定：界面减字——通讯录改成每人一行（姓名、职位、部门），点开才显示分机、手机、工号、状态、备注；回拨和转接菜单不再显示号码，改显示部门/角色。原因：号码只在核对时偶尔用到，默认不需要铺满屏幕；数据和机制不变。
- 决定：新增"导演"（director）作为第五个 LLM 角色，每局开始时设计骗子的剧本（`Scenario`：标题、前提、目标、必须先掌握的 gate 事实、可转接的目标人物 doors、场外突破事实、可选的事件、三段结局文案）。输出必须通过 `rules.clean_scenario` 校验，失败或 LLM 出错则回退到 `data/scenarios.json` 的第 0 个预设（`bec_cfo`，即原剧情）。原因：用户要求每局的骗子性质由 LLM 设计；规则仍然全在代码里，LLM 只提供内容。
- 决定：gate 事实和 doors 从 `world.json`（原 `Fact.gate`、硬编码 E05）搬到 `Scenario`。gate 事实必须是非 public 事实，doors 必须是当天可接电话的员工；第一个 door 门槛为难度阈值，之后每个 door 加 `SECONDARY_DOOR_BONUS`（2）。原因：不同剧本需要不同的"终点"，而判定公式仍是同一个纯函数 `check_breach`。
- 决定：结局文案（cutscenes）从 `story.json` 搬到各剧本里；导演给出的文案缺任何一段就整体用默认剧本的三段。原因：三段结局必须和剧本对得上，缺一段比拼凑更安全。
- 决定：导演只能决定"是否有事件"的内容，事件发生在第几天由代码掷骰决定（`INCIDENT_DAYS=(2,3)`，概率 `INCIDENT_CHANCE=0.6`），并写进导演提示词。原因：保证事件当天在游戏范围内，且概率可控、可测试。
- 决定：事件的效果只有一个：事件当天回拨 IT 部门员工只得到"忙线"（`CALLBACK_BUSY`），该次回拨不会戳穿冒充者，但仍扣耐心。事件公告只写症状，真实原因（`method`）只给策划者、骗子来电提示词和赛后报告。原因：规格没写事件的机制，取最简单且最贴合"先制造故障再冒充 IT"的一种。
- 决定：每日政策更新目录放在 `story.json["policy_updates"]`（`it_verify`、`leak_audit`、`callback_fast`），第 2 天起每天最多一条，按"昨天的结果"在 `rules.pick_policy_update` 里选（泄露→`leak_audit`，被戳穿或有事件→`it_verify`，其余按优先级补，且不重复）。效果由 `rules.effects_for` 汇总成 `Effects`：`leak_audit` 让内部（internal）泄露扣分翻倍，`it_verify` 对未经回拨核实就转接 IT 的行为额外扣 4 点 Security，`callback_fast` 让回拨只扣 2 点耐心。原因：政策逐日变化要有真实的机制影响，又不能是随机的；选择规则是确定的纯函数。
- 决定：公告和政策通过 `public_state.bulletin` 与简报的 `notices` 给前端，两者都不含任何关于哪通来电是骗子的信息。赛后报告多一个 `scenario` 字段，揭示剧本和事件原因。原因：和既有的"赛前不泄露"约束一致。
- 决定：转接前增加二次确认（前端内联"Yes, transfer / Back"）。原因：转接不可撤销且代价高，误点一次菜单项不应直接结算。
- 决定：CLI 增加 `--scenario <id>` 直接使用预设剧本（不调用导演），未知 id 以退出码 2 结束。原因：方便复现和调试，API 不暴露这个参数。
