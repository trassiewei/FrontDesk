# Front Desk

A text-based social-engineering training game. You are the receptionist at Starriver Logistics, three weeks into the job. Over three working days you answer a dozen phone calls by typing, and one of the callers is a con artist who plans each new call around every careless thing you said before. When the game ends you get an incident report that quotes your own words back at you, plus the con artist's notebook.

<!-- Screenshots: add images of the call screen and the end page here. (placeholder) -->

## Quick start

Requirements: Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate     # or: uv venv --python 3.12
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set LLM_BASE_URL, LLM_API_KEY, MODEL_FAST and MODEL_SMART for any OpenAI-compatible provider.

python scripts/check_llm.py                           # connection self-test for the four LLM roles
uvicorn app.main:app --reload                         # then open http://127.0.0.1:8000
```

A command-line version plays the same game through the same engine:

```bash
python cli.py --difficulty normal --name Jordan
python cli.py --debug        # also shows the extractor's findings, the intel table and the con artist's thoughts
```

If your provider rejects a request parameter, `check_llm.py` says which `.env` switch to change (`LLM_JSON_MODE`, `LLM_SEND_TEMPERATURE`, `LLM_MAX_TOKENS_PARAM`).

Run the tests (no network needed):

```bash
pytest                       # everything except the real-LLM tests
pytest -m llm -s             # real-LLM extractor check, needs a configured .env
```

## How to play

- Each day starts with a briefing, then five calls arrive. Click **Answer next call**, read the call card (who they claim to be and the caller ID), and type your replies.
- During a call you can **Hold & call back** a person or vendor from the directory, **Transfer** to an employee, **Take a message**, or **Hang up**. Calling back is a last resort, not the main tool: you only get a couple of holds a day (fewer on harder levels), each costs one of your replies and some of the caller's patience, a real caller who is put on hold and checked is annoyed and costs you Service, and on Normal and Hard the person you phone is not always at their desk (a hold that reaches nobody does not use up your allowance, and proves nothing).
- The main way to catch a con is to talk to them. They are bluffing: ask something only the real person would know (extension, employee ID, who their manager is), and listen for slips, such as a wrong name, a vague answer, or a detail that does not fit. If you suspect a call, you can **Flag last call**: a flag on a call where the caller slipped burns their crew's identity; a flag on a real caller costs Service, and each wrong flag costs more than the last. You are never told whether a flag was right until the game ends.
- The callback menu also has **Number the caller gave**. It looks like a check, but whoever answers there will vouch for the caller, and a con's accomplice answers too, so it can make a con more convincing. Check people through the directory.
- Between calls you can **Flag last call** as suspicious. A right flag earns Security points (more if the caller had slipped); a wrong one costs Service points.
- Two scores run all game, and both are settled once at the end of each day (a live number would tell you whether a call was real). **Security** drops when you reveal internal or confidential facts to the con artist and when the company is breached. **Service** is how many real callers you actually helped. A caller who already got what they came for is not upset if you end the call, and one slip among a few calls will not wreck the score. On Normal and Hard a caller posing as one of the listed vendors may even show that vendor's real number, so a matching caller ID proves nothing.
- From day 2 the briefing can carry a **policy update** that reacts to how the last day went (for example, double Security penalties after a leak, or a rule against transferring unverified callers to IT). Some days also open with a **heads-up** about something odd at the company, such as an outage. Both stay readable in the Policy tab.
- You get 12 replies per day, shared across all calls, and at most 5 per call. Spend them on checking suspicious callers, but keep some for real colleagues who need help.
- On Easy and Normal the Policy tab also holds a **Playbook** of common con-artist moves and how to counter them. After the game, the report lists every call with its hidden truth ("Every call, revealed").
- The **Policy** tab and the directory, desk notes and your own notes tab are on the right. Use them.
- The game ends when the con artist gets what they need (breach), gives up after being exposed enough times, or when Wednesday ends.
- Difficulty changes the whole feel of the con artists:
  - **Easy**: clumsy cons who impersonate people from the directory, ask for the transfer right away and panic the moment you say you will check. Verifying is enough to win, and their slips are obvious.
  - **Normal**: smooth cons who impersonate real staff or vendors. They stay calm if you verify, so you must check them: call the directory entry back (not the number they gave) or catch the one small slip in how they talk, before helping.
  - **Hard**: outsiders with believable errands, who read just like real callers (and real callers are as vague and pushy as they are). Their only slip is subtle, and a directory callback often does not reach anyone, the crew also does its own research overnight (shown in the report as "crew's own research"), and real callers are only satisfied if you really put them through or answer them. Refusing everything keeps you safe but wrecks Service; helping everyone gets you breached.

## What the LLM does, and what it does not

Five LLM roles, all language work:

1. **Caller** plays every caller. The con artist improvises against your questions and pushes back on your challenges.
2. **Extractor** reads each of your free-text replies and decides which non-public facts you disclosed, with the quote.
3. **Planner** (the con artist's planning side) reads everything learned so far before each day, writes the notebook, an assessment of the receptionist and the day's arc, and writes each call as a short script (a purpose in the plan, beats, a fallback if refused, a reaction to being verified). The caller follows the script, and the code decides which beat is current. It is told how the crew works (see below), what the receptionist has shown so far (never a flag, only what a caller could notice: callbacks, hang-ups, questions, what she let slip) and how close the crew is to quitting.
4. **Reporter** turns the real transcript into a cause-and-effect incident report after the game.
5. **Director** designs the con artist's scenario for each new game: the pretext, the goal, which facts they must learn first, who they are trying to reach, and sometimes a staged incident. Its output is checked by code (`rules.clean_scenario`); if it is invalid or the call fails, a built-in preset is used. `data/scenarios.json` holds six presets; `python cli.py --scenario fake_vendor` plays one directly.

Each game is an *operation*: the code picks one of nine **gambits** from `data/gambits.json` (chain of favours, fixing what we broke, the borrowed boss, the friendly survey, the lost new hire, the surprise inspector, the wrong-number funnel, the name relay, the vouching accomplice), and the director writes the crew (a codename and a profile), the motive (who is paying and the deadline) and a three-day campaign around it. The planner follows the campaign, chains each call onto the last, and the end page and report reveal the method, the motive, each call's purpose and how to spot that kind of attack.

Every rule is ordinary deterministic code in `app/rules.py`: callback and transfer results, breach checks, intel merging, scoring and ratings. The engine applies those results to the game state, and the LLM never decides them. That keeps the game predictable and testable (the engine and API tests run on a scripted fake LLM) and means a random LLM mood cannot change who wins. The extractor's output is merged with plain string matching on a list of patterns per fact, so one miss from the model does not hide a real leak. The browser only ever gets a filtered public view of the state, so nothing about the con artist is visible before the game is over.

## Background story

- **Starriver Logistics**: a regional freight and warehousing company founded in 2009, about 140 employees, on the 4th floor of an office building in Riverton's harbour district.
- **Recent changes**: the new ERP system "Starboard" went live in September and still breaks often, and many staff don't know it well. In October IT support was outsourced to NetCore IT Services. Both make "I'm new here" and "I'm from the IT contractor" very believable excuses.
- **This week**: CFO Diane Mercer is on vacation in Portugal, and Paul Nguyen is covering for her from Monday afternoon. The quarterly board meeting is Wednesday at 2 pm, so the CEO and the CEO's assistant will be busy.
- **You**: the receptionist in the third week of the job, reporting to HR manager Laura Chen. You don't know everyone's voice yet.
- **The con artist**: a freelance social engineer working for a fraud ring. The default scenario is business email compromise: the ring wants the CFO's mailbox so that, while she is away, they can email Finance in her name and ask for an urgent wire transfer, and the con artist's job is to get IT to reset Diane's email password. Each game the director can pick a different angle (a fake vendor, a payroll diversion, ...). The company, employees and facts stay the same.
- **Staged incidents**: in some games the con artist causes a fault first (a login flood that takes the ERP down, a spoofed phone-system alert) and then calls pretending to be IT. While the incident is active, calling back someone in IT only gets "busy". The announcement describes symptoms only; the post-game report reveals the cause.

## Where the ideas come from

- **The Art of Deception** (Kevin Mitnick): harmless-looking facts add up to something dangerous, inside jargon buys credibility, and calling back on a known number is the cheap defence. The game turns those three ideas into mechanics.
- **Papers, Please**: a gatekeeper checks claims against a rulebook and documents under time pressure, and every decision has a cost on both sides. The Policy tab, the directory and the caller ID play that part here.
- **Contextual integrity**: whether sharing a fact is fine depends on who is asking and why. "She's on vacation" is polite chat to a colleague and a gift to a stranger. The game scores disclosures by sensitivity and by whether the fact was new to the con artist.

## Ethics and privacy

- Every company, person, phone number and system in this game is fictional. Any resemblance to real ones is accidental.
- The game is for raising awareness of social engineering. It does not target any real person or organisation.
- Your conversation is sent to the LLM service configured in `.env`. Tell anyone who tests the game before they start, and use a provider you are comfortable with.
- Game records (`games/`) and request logs (`logs/`) are stored only on your machine. The logs do not contain the API key. Both folders are git-ignored.

## Evaluation

The extractor decides how much a player's careless words cost, so it is the part worth measuring.

1. Play some games, then run `python scripts/export_replies.py`. It writes every player message to `eval/replies.csv` together with what the extractor found.
2. Fill in the `human_leaks` column by hand: fact ids separated by `;`, or `none` when the message disclosed nothing. Save the file as `eval/replies_labeled.csv`.
3. Run `python scripts/eval_extractor.py eval/replies_labeled.csv`. It compares the human labels with the LLM output, with the pattern matcher, and with both merged. It prints overall and per-fact precision, recall and F1, and lists every false positive and false negative with the player's exact words.
4. To compare models, add `--models model-a,model-b`. The extractor is re-run for each model on `tests/fixtures/extractor_cases.json` and on your labelled replies, and a comparison table is printed.

`pytest -m llm -s` runs the same extractor on the fixture cases and requires precision and recall of at least 0.8.

Results: _to be added_.

## Limitations

- The player knows it is a game, so they behave differently from a real employee who is surprised by a real call.
- Typing is not a phone call. Tone of voice, hesitation and background noise are missing.
- LLMs are random. Two games with the same choices can play out differently, and a caller can occasionally slip out of character.
- The extractor can misjudge a reply, in both directions. The pattern matcher reduces misses but can also add false hits.
- There is only one company; scenarios vary the con artist's method, crew and motive, not the company.
- Different models can behave very differently as the caller and as the extractor, so check a new provider with `check_llm.py` and the evaluation script before relying on it.

## Next steps

Ideas deliberately left out of this version:

- Role reversal: the player is the con artist
- AI player mode: an LLM plays the receptionist to run many games automatically
- Voice
- A Chinese version
