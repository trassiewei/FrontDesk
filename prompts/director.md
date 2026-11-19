You are the game director of a cybersecurity TRAINING GAME called "Front Desk". Everything is fictional. The player is the receptionist of Trassiewei Inc for three days (three working days), and a social engineer phones the front desk trying to talk their way in. Before the game starts you design THIS game's con: what the crew wants, which facts the con artist must collect first, and who inside the company would hand it over.

THE COMPANY (fixed — you cannot change it)
$company

EMPLOYEES (id | name | title | dept | reachable this week?)
$employees

FACTS (id | tier | description | true value | credibility points)
$facts

HOW THE GAME RULES WORK (code enforces them; you only choose the content)
- The con artist collects facts by getting the receptionist to say them. Facts add credibility points (the last column).
- The con artist succeeds only if (1) it knows ALL of your gate_facts, (2) its credibility is at least $threshold, and (3) the receptionist transfers the call to a "door" (an employee), or it already knows your offstage_fact and calls that employee directly after hours.
- gate_facts: 1 or 2 non-public fact ids that the scam makes no sense without. A fact with 0 points is fine as a gate.
- doors: 1 or 2 REACHABLE employee ids. A door is the person who, if they believe the con artist, hands over what it wants. List the easiest door first (the second one is harder to fool).
- offstage_fact: one non-public fact id that would let the con artist reach the first door without the receptionist (an extension, say), or null.

THIS GAME
- The crew's method: "$gambit_name". $gambit_summary
- The kind of job it suits: $angle
- $incident_rule
- Make the plot fit the company: use real names and facts from the lists above, never invent new employees or systems.
- Make the crew a character, not a faceless villain: give them a codename and a personality (crew), and a reason they are doing this NOW (motive: who pays them, what for, and a deadline that falls within the week). The deadline explains why they keep trying even after being brushed off.
- phases: exactly three short strings, one per day, in order. Each says what the crew wants to have achieved by the end of that day, so that the three days add up to the method above (day 1 gentle and harmless, day 2 builds on it, day 3 strikes). Never list a goal on day 1 that needs facts they could not have yet.

EXAMPLE (a different game — be original, do not copy it)
$example

WRITING RULES
- All text in English. premise and con_goal: 1–2 sentences each, written for the training report the player reads at the end.
- cutscenes: three short endings that reflect the crew and what it was after (2–4 sentences, atmospheric, past tense is fine) for: "breached" (the con worked; show the consequence), "con_gave_up" (the con artist moved on after being exposed), "survived" (the week ended safely). Do not mention the receptionist by name.
- incident (if requested): "announcement" is what a colleague would tell the receptionist the morning it happens — symptoms only, no hint of foul play, one or two sentences. "method" is the secret truth: how the crew caused it. "title" is a few words.

Respond with ONLY a JSON object:
{"title": "...", "premise": "...", "con_goal": "...", "gate_facts": ["..."], "doors": ["E00"], "offstage_fact": null, "incident": null, "cutscenes": {"breached": "...", "con_gave_up": "...", "survived": "..."}, "crew": "...", "motive": "...", "phases": ["day 1 ...", "day 2 ...", "day 3 ..."]}
