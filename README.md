# Front Desk 《前台》

A text-based social-engineering training game. You are the receptionist at Trassiewei Inc, a regional freight company, three weeks into the job. Over three working days you answer fifteen phone calls by typing. Most callers are ordinary people with ordinary errands. A few are a fraud crew's con artists, who plan every new call around what you said before. When the week ends you see which calls were real, what the crew was after, and an incident report that quotes your own words back at you.

The game is played in English or Chinese (中文), in the browser or on the command line. Every caller, genuine or not, is played by an LLM; every rule that decides who wins is plain code.

<!-- Screenshots: add images of the call screen and the end page here. (placeholder) -->

## Contents

- [The game in one minute](#the-game-in-one-minute)
- [A day at the desk](#a-day-at-the-desk)
- [Scores and endings](#scores-and-endings)
- [Difficulty](#difficulty)
- [The con crew](#the-con-crew)
- [The genuine callers](#the-genuine-callers)
- [After the game](#after-the-game)
- [The company](#the-company)
- [How it is built](#how-it-is-built)
- [Where the ideas come from](#where-the-ideas-come-from)
- [Ethics and privacy](#ethics-and-privacy)
- [Limitations](#limitations)
- [Deployment](#deployment)

## The game in one minute

- **Three days, five calls a day.** Each morning brings a briefing from your manager and the company bulletin, then the phone starts ringing.
- **Most callers are genuine.** A driver who cannot find the dock, a supplier chasing an invoice, a student asking about a site visit. They need to be put through to the right person, and they get annoyed if you stonewall them.
- **Some callers are con artists.** A crew has been hired to get something out of the company this week: a supplier payment diverted, payroll records, a password reset, a shipment released to the wrong truck, the client list. They need two things from you: small internal facts that make them sound like insiders ("Alice is away, Bob is covering"), and in the end a transfer to the one person who can hand over what they want.
- **You cannot tell from the time, the card or the tone.** Con calls come at different times every day, genuine callers can be vague and pushy, and con artists can be friendly and patient. You find out by talking: asking the right question, checking the directory, calling someone back.
- **The crew learns.** Every night it reads what happened on its calls and plans the next day around your habits. If you always ask "who do you usually deal with?", it will come prepared with an answer.

## A day at the desk

The screen has the call on the left and your reference material on the right.

**On a call** you read the call card (who the caller says they are, and the caller ID) and type your replies. You can:

- **Reply**: say anything. You have 15 replies a day, shared across all calls, and at most 5 per call.
- **Transfer** the caller to an employee. This is how genuine callers get helped, and how a con artist gets in.
- **Take a message**. Some callers are happy with that; on Hard, genuine callers rarely are.
- **Hold & call back** a person or vendor from the directory to check the caller's story. Only a couple of holds a day are allowed (one on Normal and Hard), a genuine caller put on hold gets annoyed, and on Normal and Hard the person you ring is not always at their desk. A line that rings out proves nothing (and does not use up your allowance).
- **Number the caller gave**: dial the number the caller offered. It looks like a check, but whoever answers there vouches for them, and a con's accomplice answers too.
- **Hang up**.

**Between calls** you can **flag the last call** as suspicious. A right flag earns Security; a flag on a genuine caller costs Service, and each wrong flag costs more than the last. You are not told whether a flag was right until the game ends.

**The reference tabs**:

- **Directory**: every employee and vendor, with extension, mobile, status this week ("on vacation") and notes. Most checks start here.
- **Desk**: the company's public details, sticky notes, internal notes, and the **call log**: every finished call's time, card and caller ID. A caller who says "I rang on Monday" from a different number is worth a second look.
- **Policy**: the standing policy, the bulletin (policy updates and notices from colleagues) and, on Easy and Normal, a playbook of common con-artist moves.
- **My notes**: a scratch pad.

**Policy updates and notices.** From day 2 the briefing can carry a new rule that reacts to how the last day went, such as "verify outside callers with a callback before transferring them to IT" or "leaks now cost double". Some days a colleague warns about something odd, such as an outage on the loading dock. Notices are worth reading carefully: not everything on the bulletin is what it seems.

## Scores and endings

- **Security** falls when you tell a caller who has not been verified an internal or confidential fact, when you break a policy, and when the company is breached. Exposing a con and flagging one correctly earn points back.
- **Service** is how many genuine callers you actually helped. A caller who already got what they came for is not upset when you end the call, and one bad call among many does not wreck the score.
- Scores settle once at the end of each day, never live (a live number would tell you whether the last call was real). On Hard they stay hidden until the week is over.

The game ends early if the crew gets its transfer while it already knows enough to be believed: the **breach** ending. Otherwise it ends on Wednesday evening, and you **survive**. Surviving by refusing everyone is possible, and the ending (and your title) will say so: the game rewards a desk that is both safe and helpful.

## Difficulty

| | Easy | Normal | Hard |
|---|---|---|---|
| Con artists | Clumsy. They impersonate people from the directory, ask for the transfer at once, get details wrong, and panic and hang up as soon as you say you will check. | Smooth. They impersonate real staff or vendors and stay calm under checks. A directory callback or one small slip in their story gives them away. | Outsiders with believable errands, who sound like any real caller. Their slips are subtle, and when the crew is ready it simply asks to be put through, like a routine customer. |
| Genuine callers | Friendly and easy to place. | Ordinary, sometimes hurried. | As vague and pushy as the cons, and only satisfied by a real answer or the right transfer. One a day needs exactly the person the crew is after. |
| Callbacks | Two a day, always answered. | One a day, usually answered. | One a day, often unanswered. |
| Help | Playbook, visible scores. | Playbook, visible scores. | No playbook, scores hidden until the end. |

**Hard** adds four special cons, each with a fair, learnable counter:

- **The echo.** A con calls as a real customer from an earlier day, with their name, company and reference. It knows their details but not what was said on that call, and it rings from a different number (check the call log).
- **The decoy and the investigator.** A deliberately clumsy con calls first and is meant to be caught. Later the same day a calm "bank fraud investigator" follows up on "the suspicious call you had earlier" and asks to warn the right person directly. No outsider could know that call happened.
- **The forged notice.** A notice on the morning bulletin announces that an outside contractor will phone today, and that contractor calls. The notice always carries one flaw you can check against the directory: a sender nobody has heard of, a real name under the wrong department, or a contact address on a look-alike domain.
- **The debrief.** Each night the crew reads everything you said to its callers and prepares answers to your favourite questions. What it cannot prepare is what a real employee will say when you phone them.

On Hard the crew also does its own research overnight, so by day 2 or 3 it is often ready to strike whatever you said. Most players lose Hard. That is the point: it is meant to be beaten by attention, not by a rule of thumb.

## The con crew

Each game is a new **operation**:

- **The job.** The code picks one of seven targets, never the same as the last few games: diverting a payment (Finance), payroll or employee records (HR), a password reset or remote access (IT), releasing a shipment (the warehouse), a fake urgent request to the CEO's office, the client list (Sales), or taking over a customer's account (Customer Service).
- **The method.** One of nine gambits: the chain of small favours, fixing what we broke, the borrowed boss, the friendly survey, the lost new hire, the surprise inspector, the wrong-number funnel, the name relay, the vouching accomplice. If the desk catches the method, the crew switches to another one mid-week.
- **The story.** A director model writes the crew (a codename and a personality), who is paying them and the deadline, a three-day campaign (day 1 quiet, day 2 build, day 3 strike), the endings, and sometimes a staged incident: the crew breaks something first (the dock scanners freeze, the ERP goes down) and then calls as the person who can fix it.
- **The rules of success.** To breach, the crew must know the job's key facts and enough internal detail to be credible, and then be transferred to the right person. On some jobs, learning one particular fact (a direct extension) lets them skip the desk entirely.
- **The planner.** Before each day the crew's planner reads every call so far and writes a notebook page (cool, observant, a little worried about the deadline), an assessment of you, and a short script for each call, including a planned slip: the one small mistake an attentive receptionist could catch. It never gives up; an exposed crew just gets more careful and comes back with a new face.

## The genuine callers

Each morning a casting model writes the day's genuine callers: customers, drivers, suppliers, applicants, neighbours, journalists, sometimes a real colleague on an internal line or one of the listed vendors on its official number. Each has a goal, what they know (an order number, who they usually deal with), whether a message would do, and who they would accept being put through to. Some casually ask for something the desk should not hand out ("could I just have her mobile?"), where the right move is to help another way.

Names, errands and the crew's cover stories are kept fresh across games: the game remembers what the last few games used, renames repeat characters and rests overused errands.

## After the game

The end page turns every card over:

- **Every call, revealed**: genuine or con, what each con call was for, which facts you gave away and when, and, on Hard, which special con it was and how it could have been spotted.
- **The crew's notebooks**, page by page, including what they thought of you.
- **An incident report** that tells the story of the week as cause and effect, quoting your exact words ("Because you said X on Monday, the caller on Tuesday could…"), what you did best and where it slipped. Quotes are checked against what was really said.
- The scenario: the crew, their client, the method and any switches, and what the staged incident really was.
- A downloadable log of the whole game.

## The company

- **Trassiewei Inc**: a regional freight and warehousing company founded in 2009, about 140 staff, on the 4th floor of an office building in Riverton's harbour district.
- **Recent changes**: the new ERP system "Starboard" went live in September and still breaks often; in October overflow IT support was outsourced to NetCore IT Services. Both make "I'm new here" and "I'm from the IT contractor" believable.
- **This week**: CFO Alice Adams is on vacation and Bob Baker is covering Finance. The board meets on Wednesday at 2 pm, so the CEO and the CEO's assistant are busy.
- **You**: the receptionist, in your third week, reporting to HR manager Frank Foster. You do not know everyone's voice yet.

The company, its people and its facts are the same every game; the crew, the job and the callers are new each time.

## How it is built

The LLM does all the language; the code makes every decision.

LLM roles:

1. **Caller** plays every caller, genuine or con, following the day's script and improvising against your replies.
2. **Extractor** reads each of your replies and decides which non-public facts you disclosed (with the quote), and whether you openly doubted the caller's story.
3. **Planner** is the crew's strategist: the notebook, the assessment, the scripts and the special cons.
4. **Director** designs each game's operation and, each morning, casts the genuine callers.
5. **Reporter** writes the incident report after the game.
6. **Judge**, a small check: when you hang up on a genuine caller, had they already got what they needed?
7. **Translator**, Chinese games only: puts any card, script or notebook that came back in English into Chinese.

Everything else is deterministic code in `app/rules.py` and `app/engine.py`: what a callback says, whether a transfer breaches, how intel adds up, scoring, policy updates, when a con makes its slip, which calls are cons. The model's drafts are checked by code before use (a genuine caller must be a real, reachable colleague or a named outsider; a scenario must use real facts and people; a forged notice gets its flaw from the code), and anything unusable is replaced by a fixed fallback. Leaks are judged by the extractor and by plain pattern matching together, so one model miss does not hide a real leak. The browser only ever receives a filtered view of the game, so nothing about the crew is visible before the end.

That keeps the game fair and testable: a random model mood cannot decide who wins, and the engine and API tests run on a scripted fake LLM.

## Where the ideas come from

- **The Art of Deception** (Kevin D. Mitnick and William L. Simon): harmless-looking facts add up to something dangerous, inside jargon buys credibility, and calling back on a known number is the cheap defence. The game turns those ideas into mechanics.
- **Papers, Please**: a gatekeeper checks claims against a rulebook and documents under pressure, and every decision has a cost on both sides. The directory, the call log and the bulletin play that part here.
- **Contextual integrity**: whether sharing a fact is fine depends on who is asking and why. "She's on vacation" is polite chat to a colleague and a gift to a stranger, so disclosures are scored by sensitivity and by whether the fact was new to the crew.

## Ethics and privacy

- Every company, person, phone number and system in the game is fictional. Any resemblance to real ones is accidental.
- The game is for raising awareness of social engineering. It does not target any real person or organisation.
- Your replies are sent to the LLM service configured in `.env`. Tell anyone who tests the game before they start, and use a provider you are comfortable with.
- Game saves (`games/`) and request logs (`logs/`) stay on the machine running the server; the logs do not contain the API key. They do not pile up (see [Saved games](#saved-games)).

## Limitations

- The player knows it is a game, so they behave differently from an employee surprised by a real call.
- Typing is not a phone call: tone of voice, hesitation and background noise are missing.
- LLMs are random. Two games with the same choices can play out differently, and a caller can occasionally slip out of character or phrase something oddly.
- The extractor can misjudge a reply in either direction; the pattern matcher reduces misses but can add false hits.
- There is one company; games vary the crew, the job and the callers, not the company.
- In a Chinese game the rules, data and prompts stay in English: the code translates what the player reads (`app/i18n.py`, `data/zh/`), the roles are told to write player-facing text in Chinese, and the translator catches what still came back in English. The pattern layer of the leak check knows fewer Chinese phrasings, so it leans more on the extractor. The plain fallback report (used only if the reporter fails) is in English.
- Every reply waits for the model, so a full game takes a while.

## Deployment

### Requirements

- Python 3.11 or newer.
- An OpenAI-compatible LLM endpoint (base URL and API key) with a fast model and a stronger model.

### Install and configure

```bash
python -m venv .venv && source .venv/bin/activate     # or: uv venv --python 3.12
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set LLM_BASE_URL, LLM_API_KEY, MODEL_FAST and MODEL_SMART.
# Optional: MODEL_CALLER, MODEL_PLANNER, … override the model for a single role.

python scripts/check_llm.py      # sends one small request per role and reports what happened
```

If your provider rejects a request parameter, `check_llm.py` says which switch to change in `.env` (`LLM_JSON_MODE`, `LLM_SEND_TEMPERATURE`, `LLM_MAX_TOKENS_PARAM`). Keep `.env` out of version control: it holds your API key.

### Run

```bash
uvicorn app.main:app --reload                          # local play: http://127.0.0.1:8000
```

The command-line version plays the same game through the same engine:

```bash
python cli.py --difficulty normal --name Jordan
python cli.py --debug                    # also shows the extractor's findings, the intel table and the crew's thoughts
python cli.py --scenario fake_vendor     # play one of the preset scenarios in data/scenarios.json
```

### Hosting it for other people

```bash
FRONTDESK_MAX_GAMES_PER_IP_HOUR=5 uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- `FRONTDESK_MAX_GAMES_PER_IP_HOUR` limits how many new games one IP address can start per hour (0 or unset means unlimited). Set it before exposing the server: every game makes many model calls.
- Open the chosen port in your firewall or cloud security group.
- Restart the server after updating the code; games in progress resume from their saves.

### Saved games

Each game is saved to `games/<id>.json` and its model requests are logged to `logs/<id>.jsonl`. Both are pruned automatically, when the server starts and whenever a new game begins:

| Setting | Default | Meaning |
|---|---|---|
| `FRONTDESK_FINISHED_GAME_HOURS` | 24 | A finished game (and its log) is deleted this long after its last activity. Until then its end page and report can still be opened. |
| `FRONTDESK_IDLE_GAME_HOURS` | 72 | An unfinished game is deleted after this long without activity. |
| `FRONTDESK_MAX_SAVED_GAMES` | 50 | Never more than this many saves; the oldest go first. |

`games/recent.json` remembers which names, errands, cover stories, jobs and methods the last few games used, so the next game picks different ones.

### Tests and evaluation

```bash
pytest                  # the whole suite on a scripted fake LLM, no network needed
pytest -m llm -s        # real-LLM extractor check (needs a configured .env)
```

The extractor decides how much a player's careless words cost, so it is the part worth measuring:

1. Play some games, then run `python scripts/export_replies.py`. It writes every player message to `eval/replies.csv` with what the extractor found.
2. Fill in the `human_leaks` column by hand (fact ids separated by `;`, or `none`) and save as `eval/replies_labeled.csv`.
3. Run `python scripts/eval_extractor.py eval/replies_labeled.csv` for overall and per-fact precision, recall and F1 of the model, the pattern matcher and both merged, with every false positive and negative.
4. Add `--models model-a,model-b` to compare models on `tests/fixtures/extractor_cases.json` and your labelled replies.
