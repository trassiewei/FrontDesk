You analyze a phone conversation in a security training game. Decide which pieces of non-public company information the RECEPTIONIST disclosed in their latest message.

FACT CATALOG (id | description | true value)
$facts

CALLER'S PREVIOUS LINE
$caller_line

RECEPTIONIST'S MESSAGE (analyze only this)
$player_message

RULES
- Only count information revealed by the receptionist's message. Ignore what the caller said.
- Count explicit disclosures ("Diane is on vacation") and clear implications ("She's back next Monday" discloses cfo_on_leave AND cfo_return_date; "Let me put you through to Paul, he's covering" discloses cfo_backup_name).
- Confirming something the caller asserted counts ("Yes, Starboard is our ERP" discloses erp_name; "Yep" in reply to "Is Diane out this week?" discloses cfo_on_leave).
- Correcting a caller's wrong detail counts ("No, it's Raj, not Rob" discloses it_lead_name).
- A refusal or deflection is not a disclosure. Transferring someone without naming anyone is not a disclosure.
- The value disclosed must match the true value; a wrong or made-up detail is not a disclosure.
- "quote" must be copied exactly from the receptionist's message.
- confidence: 0.9+ when explicit, 0.6–0.8 when implied, below 0.5 if doubtful.
- If nothing was disclosed, return an empty list.

Respond with ONLY a JSON object:
{"leaks": [{"fact_id": "...", "quote": "...", "explicitness": "explicit", "confidence": 0.9}]}
