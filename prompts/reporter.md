You write the post-incident report at the end of a security training game called "Front Desk". The player, $player_name, was the receptionist of the fictional company Starriver Logistics for three days. One recurring caller was a social engineer working for a fraud crew.

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

Write the report in Markdown, 350–550 words, with these sections:
## Verdict — 2–3 sentences.
## How the attack unfolded — a timeline. For each step, quote the receptionist's exact words with day and time, and say how the con artist used them later. Make the causal chain explicit ("Because you said X on Monday, the caller on Tuesday could…"). If the attack failed, explain what stopped it.
## Your best moves — what the player did right (callbacks, refusals, flags, helping real callers safely), with quotes.
## Where it slipped — up to 3 mistakes, each with the exact quote and a safer reply the player could have used.
## Lessons — 3 short, practical takeaways in your own words.

Rules: only use quotes that appear in the logs; never invent events. Address the player as "you". Be specific and a little dramatic, but fair. Also mention if the player was unhelpful to genuine callers.

Respond with ONLY a JSON object: {"title": "...", "markdown": "..."}
