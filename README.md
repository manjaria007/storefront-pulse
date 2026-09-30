# Storefront Pulse

**Turn a small business's Ring entrance camera into foot-traffic and operations insight, and let the owner ask for it through Alexa+.**

Ring cameras at shops today answer one question: *is something wrong?* Storefront Pulse answers the ones owners actually ask every day:

- "How was yesterday?"
- "Why was Saturday so slow?"
- "How should I staff next Saturday?"
- "Anything happen after we closed?"

Built for **Build, Ship, Shape: Amazon Developer Hackathon 2026**. Tracks: Ring and Alexa+. Mini-challenges: AWS Builder and Open Source.

---

## How it works

```
Ring camera ──webhook v1.1 (HMAC)──▶ ingest  ──▶ events (dedup)
                                        │ ack < 5 s
                                        ▼ background / SQS
                         snapshot at event time ─▶ people counter (Bedrock vision)
                                        ▼
                                    visits store  ◀── weather context (Open-Meteo / sim)
                                        ▼
                       insight engine (baselines, explanations, forecasts)
                                        ▼
            MCP server (spec 2025-11-25, Streamable HTTP) ──▶ Alexa+ (simulated UI for the hackathon)
```

- **Fast acknowledgement.** Ring requires a `200` within 5 s. The receiver only verifies, dedupes and stores (~3 ms). Counting happens afterwards.
- **Counting from snapshots, not motion labels.** The Ring Playground omits `sub_type` and `cv_detections`, and motion ≠ customers anyway. For each event we pull the snapshot and ask a Bedrock vision model how many people are *entering*. Passers-by count as 0.
- **Tools answer in milliseconds.** Alexa+ requires a round trip under 500 ms. Tools read stored data; the slowest measured call is about 27 ms.
- **Honest by design.** Visits are *estimates* with ranges. Explanations are "what lines up with the change", never "the cause". Low-confidence detections are hidden and counted. Camera outages are flagged as data gaps, not as a drop in demand.

## Quickstart (local, simulated store)

```bash
pip install -e ".[dev]"            # add ".[aws]" for Bedrock
export PYTHONPATH=src PULSE_TODAY=2026-10-05

# 1. Build 6 weeks of a simulated cafe's Ring events, then push them through the real ingest code
python -m storefront_pulse.simulator.cli build --out demo --end 2026-10-04
python -m storefront_pulse.simulator.cli load  --data demo --db pulse.db

# 2. Ask the questions directly
python scripts/ask.py

# 3. Serve them over MCP and test like an MCP client (Alexa+) would
PULSE_DB=pulse.db python -m storefront_pulse.mcp_server      # http://127.0.0.1:8000/mcp
python scripts/mcp_smoke.py                                   # handshake, tools, latency check

# 4. Run the webhook receiver and replay events over HTTP (signed like Ring does)
RING_HMAC_KEY=sim-signing-key PULSE_COUNTER=simulated PULSE_SIM_TRUTH=demo/truth.json \
  python -m storefront_pulse.ingest.app                        # POST /ring/webhook on :8080
RING_HMAC_KEY=sim-signing-key python -m storefront_pulse.simulator.cli load --data demo --post http://127.0.0.1:8080/ring/webhook
RING_HMAC_KEY=sim-signing-key python -m storefront_pulse.simulator.cli live --post http://127.0.0.1:8080/ring/webhook

python -m pytest -q                                           # 19 tests
```

The demo world includes two storylines for the insights to find:

- **Saturday Oct 3:** rain all morning *and* the shop opened 45 minutes late.
- **Wednesday Sep 30:** two visits near midnight.

Sample output:

> *Saturday had about 198 visits, 25% below a typical Saturday, mostly between 8 am-12 pm. What lines up with it: late opening (about 16 visits) and rain (about 39 visits). That covers most of the gap.*

The rain effect is learned from the shop's own history, not hard-coded (≈33% learned vs 40% simulated).

## MCP tools

| Tool | Answers |
|---|---|
| `get_daily_recap` | How was yesterday / a given day vs a typical same weekday |
| `explain_traffic_change` | Why a day was slower or busier: when, plus late opening, rain, holiday or camera outage, with visits attributed and the rest left unexplained |
| `forecast_staffing` | Hour-by-hour forecast for an upcoming day, including forecast rain, compared with scheduled staff |
| `get_after_hours_activity` | People at the entrance outside opening hours, with snapshot references |
| `get_store_profile` / `update_store_profile` | Opening hours, staff per day, staffing assumptions |

Every insight returns the same shape:

- `speech`: 1–2 sentences for voice
- `headline` and `facts` for the screen
- `confidence` and `confidence_reason`
- `caveats`
- `card`: chart-ready data
- `window`: the dates the answer covers

## Adding a new storefront scenario (the protocol)

1. Create `src/storefront_pulse/insights/<your_insight>.py`.
2. Subclass `Insight` and set `name`, `title` and `description`. The description is what Alexa+ reads to decide when to call the tool.
3. Implement `tool(self, <typed keyword args>) -> InsightResult` using the helpers in `analytics/traffic.py`.
4. Decorate it with `@register` and import the module in `insights/__init__.py`.

The MCP server exposes it automatically. Add a simulator storyline in `simulator/traffic.py` (`STORYLINES`) so the demo has something for it to find, plus a test.

Ideas queued:

- Promotion impact (before/after with a matched baseline)
- Weather-adjusted week-over-week comparison
- Queue and dwell alerts from repeated motion
- Multi-location comparison
- Delivery-window tracking

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PULSE_DB` | `pulse.db` | SQLite path (DynamoDB adapter planned for AWS) |
| `PULSE_PROFILE` | `profiles/demo_cafe.yml` | Store hours, staffing |
| `RING_HMAC_KEY` | – | Webhook signing key from the Ring Developer Portal |
| `RING_ACCESS_TOKEN`, `RING_API_BASE` | –, `https://api.amazonvision.com` | Snapshot downloads |
| `PULSE_COUNTER` | `motion` | `bedrock` \| `simulated` \| `motion` |
| `BEDROCK_MODEL_ID`, `AWS_REGION` | `us.amazon.nova-lite-v1:0`, `us-east-1` | Vision counting |
| `PULSE_TODAY` | today | Pin "today" for demos |
| `PULSE_MCP_HOST`, `PULSE_MCP_PORT`, `PULSE_MCP_ALLOWED_HOSTS` | `127.0.0.1`, `8000`, – | MCP server binding; allowed hosts for a tunnel |

## Roadmap to submission (Oct 23, 12:00 PT)

- [x] Ring webhook v1.1 receiver: HMAC, dedupe, fast ack
- [x] Storefront simulator with storylines; ingest via the real signed path
- [x] Insight protocol + 4 insights; MCP server (2025-11-25, Streamable HTTP), latency-checked
- [ ] Connect the Ring Playground doorbell (developer app, webhook URL via tunnel, OAuth token)
- [ ] Validate Bedrock counting on 30–50 storefront images; tune the prompt
- [ ] Alexa-style web UI (voice/text + cards) as the simulated Alexa+ layer
- [ ] AWS deploy: API Gateway + Lambda (ingest), SQS + Lambda (counting), DynamoDB, EventBridge Scheduler (nightly baselines), MCP on AgentCore Runtime or Lambda
- [ ] Open Source: storefront scenario PRs to `ring-sandbox`; publish the visit-estimation library
- [ ] Demo video (<3 min), submission write-up, friction log (see `FRICTION_LOG.md`)

## License

MIT
