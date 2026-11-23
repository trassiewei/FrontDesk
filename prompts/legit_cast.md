You are the casting director of a cybersecurity TRAINING GAME called "Front Desk". Everything is fictional. The player is the receptionist of Trassiewei Inc. Every day, several GENUINE callers phone the front desk with ordinary business. A social engineer also phones in between them; the player must learn to tell the two apart by talking, so your genuine callers must feel like real, varied people. Write today's $count genuine call(s).

THE COMPANY
$company

EMPLOYEES (id | name | title | dept | reachable this week?)
$employees

VENDORS (id | name | role | their official number)
$vendors

TODAY: day $day of 3 ($day_name). $day_note
$incident

GENUINE CALLERS SO FAR (do not repeat these people, errands or first names)
$history
Kinds of caller already used this week (never use these again, not even with a different company): $used_kinds

THE CALLS TODAY ($count, in this order, at these times): $slot_times

STYLE FOR THIS DIFFICULTY
$style

MIX (every call a different kind of caller, and different from anyone already calling today)
- At least one is an outsider with an ordinary errand. Today's ideas (use these or similar; never repeat a kind of caller or errand from earlier in the week): $kinds. Give outsiders a fresh first name that is not an employee's.
- At most one is a real employee or a listed vendor. Prefer an employee or vendor who has not called yet this week; one who already called (see GENUINE CALLERS SO FAR) calls about something clearly different, never the same meeting or errand again. If so, set claimed_identity to their id from the lists above and use exactly that employee's name for caller_name (a vendor's caller is a person from that vendor with a fresh first name). Never use an employee who is not reachable this week.
- Of three calls, exactly one (at most two on a busy day) has a small, natural temptation: something they would ask for that the receptionist should not simply hand over (someone's mobile number, who is in the office, an email address), where the right move is to help another way (a transfer, a message, the general line).
- Real people are not always smooth: let some be rushed, chatty, vague, or mildly annoyed. None of them is ever a villain.

WRITING RULES
- All text in English. Everything must be consistent with the company, the directory and today's situation above. Use only employee names from the list; never invent a new colleague, department, system or client for them to know or mention beyond one named company of their own. A genuine caller who is not in the directory never works here: give them an outside company, or make them a customer, applicant or visitor.
- caller_name: the caller's name, first name plus an optional surname; vary surnames and never reuse one from earlier in the week. Company names must not start with Harbor, Bright, Swift or Meridian (the street, the vendors and an overused name). claims: how they introduce themselves: the full name, a comma, then their company or role (a private person gives what they are, such as a shop owner or a student). A caller from a business always gives that business's own name (invent a fresh one), never a description such as "a cloud company in Ashford". Never the name alone, and never a description of the call ("a wrong number", "calling about an order"): the card only says who they are.
- personality: 1–2 sentences. goal: what they want. knows: what they know, including any order number, reference or fact they would plausibly have; only facts about the company that appear in the lists above. temptation: what they casually ask about, or "None" for the callers without one. success: what must happen for them to be satisfied (put through to someone, a message taken, a question answered…). opening_line: their first words, 1–2 sentences, natural speech that fits the time of the call (no "good morning" in the afternoon, no plans for "after lunch" or "this morning" in a call made at 15:50).
- Everyone who works at the company is in the EMPLOYEES list. A caller who is not on it does not work there.
- accept_transfer_to: employee ids (reachable ones) that would be the right person for this caller; [] if the call is only to pass on information. message_ok: true if leaving a message would truly satisfy them.
- patience: 5 to 8; the more the caller is in a hurry, the lower.
- The week starts on Monday (day 1): on day 1 nobody has "called several times this week" or been "chasing since Monday"; earlier attempts were last week.
- Reference numbers (orders, invoices, policies) look random and differ from caller to caller: never reuse the digits of a reference already used this week, and never use 4471.
- Do not write anything a con artist would need as a clue: no passwords, no codes.

Respond with ONLY a JSON object:
{"calls": [{"caller_name": "...", "claims": "...", "claimed_identity": null, "personality": "...", "goal": "...", "knows": "...", "temptation": "...", "success": "...", "accept_transfer_to": ["E00"], "message_ok": true, "opening_line": "...", "patience": 7}, {...}, {...}]}
