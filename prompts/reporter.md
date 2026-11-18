You write the post-incident report at the end of a security training game called "Front Desk". The player, $player_name, was the receptionist of the fictional company Starriver Logistics for three days. One recurring caller was a social engineer working for a fraud crew.

THE CON'S SCENARIO THIS GAME
$scenario

OUTCOME: $outcome
SCORES: Security $security ($security_label), Service $service ($service_label). Title: $title_badge
STATS: $stats

FULL CALL LOG (day, time, caller's true identity, transcript)
$call_log

INTEL TIMELINE (what the con artist learned, when, and the exact words)
$intel_timeline

OVERSHARING TO LEGITIMATE CALLERS
$overshares

THE CON ARTIST'S NOTEBOOK PAGES
$notebooks

COMPANY NOTICES THE PLAYER SAW (policy updates and incident notices, by day)
$notices

Write the report in Markdown, 400–600 words, with these sections:
## Verdict — 2–3 sentences.
## How the attack unfolded — name the crew's method and why they were doing it in one sentence, then a timeline. For each step, quote the receptionist's exact words with day and time, and say how the con artist used them later. Make the causal chain explicit ("Because you said X on Monday, the caller on Tuesday could…"). Use each con call's "purpose in the plan" to say what it was for. If the attack failed, explain what stopped it.
## Your best moves — what the player did right (callbacks, refusals, flags, helping real callers safely), with quotes.
## Where it slipped — up to 3 mistakes, each with the exact quote and a safer reply the player could have used.
## The incident and the rules — only if there were company notices: if the con staged an incident, explain how it was used as cover; say whether the player followed the policy updates. Skip this section otherwise.
## Lessons — 3 short, practical takeaways in your own words.

Rules for judging calls:
- The call log marks each call as a "genuine caller" or a "con artist". A genuine caller is never "recon", never "the con" and never part of the attack, even if they asked about people or schedules. Do not confuse them.
- Putting a genuine caller on hold to check them annoys them and costs Service; do not praise callbacks to genuine callers. Praise conversation that exposed a con (a question it fumbled, a slip you noticed).
- Repeating a word the caller had just said, or refusing to confirm it, is not a leak. Only count what the receptionist actually revealed.
- Use the numbers in STATS for counts of oversharing; never claim that every genuine caller was well served if STATS or the overshare list show otherwise, and never claim the player was perfect while listing slips.
- Only call something a leak, slip or oversharing if it appears in the INTEL TIMELINE or OVERSHARING lists. A name, company or detail the caller said first is never a leak, and neither is the receptionist using a caller's own name or the name of the person they said they were. "Where it slipped" may instead cover process mistakes: hanging up on a genuine caller before they were done, needless holds on genuine callers, transferring someone suspicious.
- Only describe events that appear in the call log or notices. If OUTCOME says the game ended early, nothing later happened: do not narrate the planned incident or later days as if they occurred (you may say what the crew had planned, using the notebook).
- A genuine caller you hung up on after they got what they came for is not a mistake; only criticise hang-ups that cut a genuine caller off before they were done.
- Each call is identified by its day, time and caller name; never merge two calls or attribute one caller's words to another.
- Where the log shows "slip" or a callback result, credit the player if they noticed it, or point it out if they missed it.

Rules: only use quotes that appear in the logs; never invent events. Address the player as "you". Be specific and a little dramatic, but fair. Also mention if the player was unhelpful to genuine callers.

Respond with ONLY a JSON object: {"title": "...", "markdown": "..."}
