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
- 观察（真实 API，DeepSeek `deepseek-chat` 同时作为 FAST 和 SMART）：`check_llm.py` 五个角色都能返回 JSON（caller 偶尔对"返回 ok:true"这种测试答 false，是模型随机性，不是连接问题）；开局（导演+策划者）约 9 秒，赛后报告约 7 秒；提取器评估 28 例 精确率 0.96、召回 0.96。
- 观察：用 LLM 扮演前台自动打 3 局（一次性脚本，不入库）：真实来电共 15 通，13/13/15 通被满足，最多用 4 条回复，没有一通需要超过 5 条；没被满足的 2 通都是模拟玩家自己挂了电话。骗子来电通常 2–4 轮就自己结束（gave_up），3 局都是 survived。骗子偏容易放弃，难度调整留到真人试玩之后再看。
- 决定：来电开始后（对话里只有来电者开场白时），输入框占位文字提示"来电者已经说明来意，不要用一条回复打招呼"。原因：真人试玩时打招呼会白白消耗回复预算，来电者还会把开场白再说一遍。
- 决定：参考《欺骗的艺术》加三处，其余（规格 §6.5 的来电显示不可伪装内部分机）不动。原因：用户要求借鉴该书优化整体逻辑与体验；伪造来电显示会违背规格，所以不做。
- 决定："拨打来电者给的号码"作为回拨菜单第一项（目标 token `CALLER_NUMBER`，不是目录条目）。对真假来电文本形式完全一致（"有人接听，确认某某在这里"），不戳穿、不结束通话，只扣回拨耐心，不占回复额度。对骗子来电置 `number_checked`，转接时所需可信度降低 `VOUCH_CREDIBILITY`（2，最低为 1），但 gate 事实仍必须具备；骗子提示词收到"同伙接了电话为你担保"，真人来电收到"那边确认了你的身份"。不计入 `callbacks`，所以 `it_verify` 仍视为未核实。原因：书里的经典陷阱是"回拨对方给的号码"，它看上去像核实，实际是为骗子背书；前端不能因此区分真假来电，所以界面和文本两边都相同，后果只在赛后揭示。
- 决定：赛后报告新增 `calls`（"Every call, revealed"）：逐通来电给出真假、冒充对象、使用的手法（含识别特征和对策）、结局、玩家的操作、骗子学到的事实，以及是否被同伙担保；没有玩家操作的真实来电压缩成一行。数据来自 `summary.call_reveal`，只在游戏结束后由 `get_report` 生成，`public_state` 不含。原因：让玩家复盘每一步为什么成功或失败。
- 决定：新增 `data/playbook.json`（10 种手法的"听起来像/怎么办"，三条经验法则，以及结局文案）。`/api/world` 提供除 `outcomes` 外的内容；Policy 页的"Playbook"在 easy 和 normal 显示，hard 隐藏（纯前端隐藏，内容不含任何关于当局的秘密）。测试保证手法 id 与 `tactics.json` 一致。原因：把书里的手法变成玩家可学的东西，难度越高提示越少。
- 决定：难度设计目标改为 easy 极易防住、normal 必须额外核实（回拨目录）才能防住、hard 几乎防不住且骗子与真人差别很小。为此 `config.Difficulty` 增加 `verify_note`（被核实时骗子的反应）、`planner_note`（策划者选什么身份）、`legit_note`（真人来电的口吻）、`closing`（骗子何时要求转接）、`callback_reach`（回拨能否接通本人）、`recon`、`strict_legit`，对应提示词里新增 `$verify_note`、`$closing_note`、`$legit_note`、`$planner_note`。这是对 SPEC §9 提示词的有意改动（用户明确要求重新设计提示词和流程）。原因：原来的提示词在三档都让骗子表现差不多，难度只靠数值区分，试玩里 hard 反而比 normal 更好防。
- 决定：easy 的骗子一被核实就慌张挂断（`gave_up`），并且一开口就要转接 IT；normal 的骗子被核实时保持冷静、提供替代方案，但仍然会被"回拨目录里被冒充的人"戳穿；hard 的骗子只扮演目录无法证实的外人（客户、别家快递、招聘、审计、承包商同事），核实时爽快配合，被拒绝后不结束通话而是换一个无害的问法。原因：三档要求的是"识别信号"逐级消失，而不是单纯调数值。
- 决定：骗子何时要求转接由代码决定（`caller.closing_note`：easy 立刻；其余在可信度够门槛、gate 事实齐全时才提；否则继续铺垫），不让 LLM 自己判断。原因：和既有"规则全在代码里"一致，也让 hard 的骗子不会过早暴露意图。
- 决定：回拨语义调整。来电被认定为"被戳穿"的条件是：回拨对象在来电的 references（声称身份或通话里提到的任何人）中；声称是某部门新人（无具体身份）时，该部门任何人都能戳穿。hard 的回拨只有 `callback_reach`（0.4，按种子/来电/对象确定性掷骰，非随机）的概率打得通，否则得到"在开会，可以留言"（对真假来电一视同仁）。原因：normal 要求"核实一下才能防住"，hard 要求"核实基本不可靠"。
- 决定：hard 新增 `recon`：每晚（进入第 2、3 天时、策划者运行之前）骗子团队自己再查到 3 条事实，优先补全 gate 事实，其次场外事实，再从 points ≥ 1 的内部事实里随机取（`rules.pick_recon_facts`，种子确定）。记为 `IntelRecord(source="recon", call_id="recon")`，不扣玩家 Security，报告时间线显示为"crew's own research"。`LeakSource` 增加 "recon"。原因：试玩显示 hard 下只要玩家谨慎/吝啬，骗子永远凑不够可信度（0/14 被攻破），与"几乎防不住"相反；骗子也该有自己的渠道。
- 决定：hard 的 `strict_legit`：真人来电只有被转接给正确的人或得到答案才算满意，"留言"不再算满意（`rules.take_message(strict=True)`），hard 真人来电提示词也写明这一点。hard 的 `legit_note` 让真人说话带点含糊、催促、提同事名字。原因：否则吝啬或一律拒绝的打法在 hard 下几乎不损失 Service（试玩 67–89），而真人和骗子只差一个"有没有被满足"的问题。
- 决定：hard 的 `breach_threshold` 从 SPEC 的 5 降到 4，`con_patience` 12，`exposures_to_give_up` 3；easy 阈值 8、耐心 8、1 次戳穿就放弃；normal 保持 6/10/2。原因：配合 recon 使 hard 对大多数打法"几乎防不住"，而全程拒绝转接给 IT 的玩家仍可能存活（代价是 Service 接近 0）。
- 观察（真实 DeepSeek，一次性脚本，不入库，并发 ≤4）：easy 谨慎玩家骗子立刻挂断，天真玩家安全但泄露很多（无一被攻破）；normal 天真玩家 3/5 被攻破，谨慎/混合玩家 0/10，吝啬玩家 0/2 被攻破；hard 吝啬玩家 3/3 被攻破，混合 4/7，谨慎 2/7，其中谨慎玩家存活时 Service 只有 0–47。样本很小，数值需要真人试玩再调。
- 观察：hard 的策划者偶尔仍会无视"不要冒充目录里的人"的提示（例如自称某个目录里的供应商），这类来电可被回拨戳穿；没有在代码里强行过滤，因为这相当于模型给了玩家一个真实的线索。

- 决定：hard 的策划者输出由代码检查（`Difficulty.outsider_only`、`rules.outsider_violations`）：声称身份是目录里的人、`claims` 里点名目录供应商、或 `persona_name` 与员工同名都算违规。出现违规时带上具体违规项让策划者重来一次（只重试一次）；仍有违规则丢掉违规的来电，若全部违规就保留原计划。原因：上一轮试玩里 hard 的策划者偶尔无视提示冒充 NetCore 之类，被一次回拨就戳穿，与"真假难分"矛盾；只靠提示词不可靠，但又不能因此让某天没有骗子来电。点名员工（name_drop）不算违规，因为真人来电也会提同事的名字。
- 决定：hard 的夜间 recon 一旦实际查到事实，第二天简报多一条"Heads up"公告（`story.json["recon_hints"]`，两条含糊文案，种子确定地选一条），不透露任何具体事实或哪通来电。原因：玩家否则会困惑"我没泄露怎么被攻破"，又不能直接告诉他骗子知道了什么。公告 kind 沿用 `incident`，前端无需改动。
- 决定：结局为 survived 但 Service < 50（`COLD_SERVICE_BELOW`）时，结局文案末尾追加一段"守住了公司，但把客户都拒之门外"（`story.json["survived_cold"]`）。原因：hard 下全程拒绝能活下来但 Service 接近 0，结局文案不应让这看起来像完胜。
- 决定：策划者现在写"台本"：`PlannerResult.arc`（当天几通来电如何衔接）和每通来电的 `beats`（开场白之后每一轮要做的一件小事，最多 4 条，每条 ≤ 240 字）、`if_refused`（被拒绝时的退路）、`if_verified`（被核实时的反应）。新字段都有默认值，缺失时骗子按旧方式即兴发挥（提示词里写"No written script"）。骗子提示词新增 `$script_text`：当前该做哪一条 beat 由代码按"玩家已回复轮数"决定（`caller.script_text`），模型只负责措辞；`if_verified` 与难度的 `verify_note` 冲突时以后者为准。赛后"Every call, revealed"多一行"Their script"。这是对 SPEC §9 策划者/来电者提示词的有意改动（用户要求台本结构）。原因：让骗子的行动有预先设计的节奏，而不是每轮从头即兴；同时 beat 进度仍由代码控制，不依赖模型自觉。
- 决定：回拨削弱。每次回拨（包括"拨打来电者给的号码"）消耗当天 1 条回复额度（`CALLBACK_REPLY_COST`），额度为 0 时返回 409，前端按钮同步禁用并标注"(1 reply)"；normal 的 `callback_reach` 从 1.0 降到 0.7（easy 1.0，hard 0.4）。原因：用户反馈回拨太强——此前它不占额度，一次就能确定性戳穿冒充者；现在核实要和"留回复给真人"竞争，且联系不上本人时什么也证明不了。
- 观察（真实 API，并发 4，12 局）：回拨削弱后 normal 谨慎玩家 0/3 被攻破（Service 53–80），混合 0/2，天真 1/2；hard 谨慎 1/2、混合 1/2 被攻破；easy 谨慎玩家骗子立刻放弃。仍符合 normal"核实就能防住、但要付出代价"的目标。

- 决定：回拨再削弱并改为"以对话识别为主"。(1) 每天回拨次数上限 `Difficulty.callbacks_per_day`（easy 2、normal 1、hard 1），超出返回 409；联系不上本人（忙线/语音信箱）的回拨不占次数（但仍占回复和耐心），"拨打来电者给的号码"算一次。(2) 对真人来电回拨会让真人不爽：`CALLBACK_ANNOY_SERVICE = 3` 记入 `service_penalty`，并在给真人来电者的系统提示里写"你有点不高兴"。对骗子不扣。原因：用户反馈一直回拨太无赖，且打扰真人理应降低服务分。
- 决定：骗子的"露马脚"机制。策划者给每通来电写 1–2 条 `tells`（对话里能听出的小破绽：叫错同事名字、说错部门、前后矛盾、被问到只有本人才知道的事时含糊，不能是靠目录裁决的东西，每条 ≤160 字）。何时露出由代码决定（`Difficulty.tell_turn`：easy 第一句、normal 玩家回复 1 次后、hard 2 次后；`caller.slip_text`），难度决定破绽明显程度（`tell_note`）；模型露出后在 JSON 里报 `slip_made`，代码置 `call.slip_shown`（没有 tells 的来电忽略该字段）。`rules.flag` 对"已露破绽的骗子"返回 `caught`：+`BONUS_EXPOSURE`（15）Security、烧掉所冒充身份、`it_alert`、`exposures` +1，满 `exposures_to_give_up` 次则结局 `con_gave_up`；flag 的文案对任何来电都不变，所以玩家看不出自己是否抓对。flag 接口因此返回 `game_over`/`cutscene`。原因：用户希望靠和当事人对话找破绽，而不是机械回拨；是否抓到仍由玩家自己判断（flag 有误判代价）。赛后揭示多一行"Their slip"。
- 决定：骗子提示词增加两条硬规则：不许写方括号/花括号占位符；被问到只有本人才知道的细节（分机、工号、直属上级、楼层）而上文没给时必须含糊、搪塞，不许凭空给出精确细节。代码侧兜底：策划者输出的 claims/pretext/opening_line/beats/tells 都会 `scrub_placeholders`；骗子一句话含占位符时重试一次，仍有就删掉占位符。真人来电（声称是目录里的员工/供应商）的提示词注入他们自己的分机/手机/工号/官方号码（`caller.identity_text`），被问到必须自然答对，被核实时可以稍显不耐烦。原因：上次试玩里骗子把 "[backup name]" 原样念出来；同时让"问身份细节"成为对真人安全、对骗子有效的探测手段。
- 决定：泄露判定防"回声"：玩家这句里命中的模式词若全部已出现在来电者上一句里，则该事实不按模式记泄露（`rules.merge_leaks(..., caller_text)`，逐词判断，所以骗子说"Raj"而玩家补上"Patel"仍算）；提取器是否认定"确认"仍照常算，提取器提示词也写明"仅重复来电者说过的词不算披露"。原因：试玩里玩家说"我不能确认 Starboard"也被扣 Security，不公平。
- 决定：报告提示词增加判例：真人来电不是 recon、回声不是泄露、"所有真人都被服务好"不能与过度分享的统计矛盾、不要夸奖对真人的回拨；呼叫日志里标出骗子是否露了破绽、真人是否被回拨打扰、是否被 flag。桌面面板展示公司地址、网站和 general emails（hr/info/ap），因为求职者会索要 HR 邮箱；对方结束通话时前端按 `end_reason` 显示"对方挂断/失去耐心/已无话可说"，不再一律"The call has ended"。
- 决定：分数改为"日终统一结算"。`GameState.settled_security/settled_service/settled_exposures` 只在 `end_day` 里按真实分数刷新；`public_state` 通过 `summary.shown_scores` 展示结算值（游戏结束后才展示真实值），`exposures` 同理，CLI 也一样（`--debug` 才看实时值）。`end_day` 返回 `day_scores`（Security/Service 及相对前一晚的变化），前端"End of day"页和 CLI 显示"Tonight's tally"。原因：用户指出实时服务分一掉就等于告诉玩家这通来电是真人，Security 的变化同理会暴露泄露和 flag 对错。
- 决定：flag 抓到露破绽的骗子后"是否放弃"推迟到 `end_day` 开头判定（`exposures >= exposures_to_give_up` 则 `con_gave_up`），`flag_last_call` 不再即时结束游戏；`flag` 接口返回的 `game_over` 因而恒为当前状态。原因：否则"flag 后游戏突然结束"会直接告诉玩家抓对了。
- 决定：错误 flag 的 Service 代价递增：`PENALTY_FALSE_FLAG_SERVICE + PENALTY_FALSE_FLAG_ESCALATION * 之前的错误次数`（5、8、11…，`rules.flag(call, earlier_false_flags)`）。原因：flag 结果现在看不见，若每次代价固定，对每通来电都 flag 一次就是低风险刷分；递增的代价让"每通都 flag"不划算，必须真的凭对话里的破绽判断。
- 决定：真人来电新增 `goal_met`（模型在目标达成的那一句报告，`CallerTurn.goal_met`/`CallRecord.goal_met`；`outcome == "satisfied"` 也算）。玩家在目标已达成后挂电话记为满意；目标未达成时挂电话仍是不满意；来电者明确 `unsatisfied`/`gave_up` 时不被 `goal_met` 覆盖。真人提示词要求：目标达成后最多再追问一个小问题，然后道谢结束，不再无限追加请求。原因：试玩里一位应聘者答完就被追问第二个问题，礼貌挂断被算成失败，第 2 天 Service 一次掉 17 分，且不公平。
- 决定：Service 加先验平滑：`(satisfied + 3) / (handled + 3)`（`SERVICE_PRIOR`），一次失败在样本少时不再是断崖（5/6 为 89 而不是 83）。全部拒绝的玩家仍会落到很低（0/9 为 25）。原因：每天只有 3 通左右的真人来电，原公式单通波动 11–17 分。这是对 SPEC 评分公式的有意改动。
- 决定：日终汇总多一句 "N caller(s) left unhappy today."（`day_scores.unhappy_callers`）。原因：玩家看到 Service 下降却不知道发生了什么；这个数本来就能从 Service 变化推出，所以不增加信息泄露，也不指明哪一通。
- 决定：normal/hard 的 `spoof_chance`（0.5；easy 0）：冒充列表供应商（`claimed_identity` 为供应商，或 `claims` 里出现供应商名）的骗子有一定概率显示该供应商的真实号码。内部分机和员工手机号仍不伪造。原因：试玩里"来电显示号码对不上"过于直白，normal 的谨慎玩家完全不需要对话就能挡掉；现在号码一致也不能证明身份，仍要靠对话或回拨。
- 决定：策划者的角色名由代码去重（`rules.dedupe_personas`）：与目录员工同名的名字（除非是在冒充该员工）、此前来电用过的名字、同一天重复的名字，都换成名字池里的新名字，并在 claims/pretext/开场白/beats/tells 等所有台本文字里替换；策划者提示词也要求"不重复角色名、同一雇主或套路不连续用三次"。原因：试玩里出现了叫 Marcus 的快递员和员工 Marcus Lee 撞名，连续三天都是"NetCore 的 Marcus"。
- 决定：报告提示词收紧：只有 INTEL TIMELINE/OVERSHARING 中出现的才能叫泄露；来电者先说出的词、对来电者本人复述其名字都不算；"Where it slipped"可以写流程错误（对真人提前挂断、不必要的回拨、转接可疑来电）；每通来电用日期、时间、名字区分，不得串线。原因：试玩的报告把 "I couldn't verify you with NetCore" 当泄露（NetCore 是对方先说的），还批评对 Raj 本人说 "thanks Raj"。
