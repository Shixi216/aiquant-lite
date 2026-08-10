# WeCom local adapter

Stage 11 implements a testable local adapter only. It does not deploy a production WeCom bot and
does not send network messages.

Supported render types include text queries, parse confirmation, candidate lists and detail,
research summaries, decision summaries, missing-data notices, VETO, system status, and experiment
summaries. Candidate lists paginate, and repeated `message_id` values are idempotent in the local
process.

Credentials are reported only as configured/not configured. `NOT_CONFIGURED` is non-fatal:
Router, Data Hub, scanner, and experiments remain usable. Output removes secret assignments,
local paths, stack traces, and hidden reasoning.

Candidate messages say “research priority only.” Decision summaries require a second explicit
confirmation. There is no real-trading button, automatic buy/sell push, broker call, or order
tool. A manually recorded external trade is a user-confirmed fact and is not broker execution.
