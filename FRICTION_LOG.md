# Friction log

Amazon awards up to a 10% judging bonus for detailed friction logs. Log every snag *when it happens*: what you tried, what you expected, what happened, how long it cost, and the workaround.

| Date | Product | What happened | Expected | Impact | Workaround |
|---|---|---|---|---|---|
| 2026-09-28 | Alexa+ MCP Toolkit | `alexa-ai` CLI and Alexa+ web simulator aren't available to hackathon participants (confirmed on the Devpost forum). The npm package returns 404. The docs read as if it's generally available. | Docs state availability, or the hackathon provides access | Can't test against real Alexa+ reasoning; must simulate the Alexa layer | Real MCP server + simulated Alexa+ UI (allowed per the forum answer) |
| 2026-09-28 | Alexa+ docs | The <500 ms tool round-trip requirement is only in the QuickStart, not the overview | Performance constraints on the overview/architecture page | Would have designed LLM calls inside tools | Precompute; tools read stored results |
| 2026-09-28 | Alexa+ docs | No documented way for an add-on to send proactive notifications | Clear statement of supported/unsupported proactive patterns | Unclear whether "morning recap" can be pushed | Owner asks; out-of-band alerts planned |
| 2026-09-28 | Ring Partner API docs | Webhook signing string and full v1.1 payload aren't shown verbatim on the API page | Complete signed example request + payload | Had to infer from a community emulator | Verified against `ring-sandbox` recordings |
| 2026-09-28 | Ring Playground | Motion events lack `sub_type` / `cv_detections`; one DoorbellPro only; no sensors (per the forum) | Playground parity with the documented payload | Can't rely on person classification | Count people from snapshots with Bedrock |
| 2026-09-28 | Ring docs | Testing path without a real device / Ring Protection subscription isn't documented on the dev guide | A documented simulator path | Unclear setup | Ring Playground + local simulator |
