# CommCopilot

Real-time AI conversation assistant for international students. CommCopilot listens to a live English conversation through the browser microphone, streams each transcript chunk to a silent agent on IBM watsonx Orchestrate, and surfaces short phrase suggestions only when the agent detects that the student is hesitating.

Built as part of the **IBM SkillsBuild AI Experiential Learning Lab**.

## How It Works

```
Microphone → Web Speech API → WebSocket → ContextAgent (silent listener)
                                              │
                                              ├── fluent  → stay silent
                                              └── hesitating → phrase_generation_agent
                                                               → safety_filter_agent
                                                               → phrase suggestions
```

1. **Speech-to-Text** — Chrome's Web Speech API transcribes the conversation in the browser (interim + final results). The browser does **not** run any hesitation logic — no filler regex, no prosody analysis, no pause timer. It just forwards each final STT chunk to the server.
2. **Listener Agent** — The server POSTs every chunk to **ContextAgent** on Orchestrate as a silent message, tagged with a per-session `X-IBM-THREAD-ID` so the agent sees the running conversation. ContextAgent:
   - infers **role / tone / intent** from the thread itself (no scenario is provided),
   - decides per chunk whether the student is hesitating (filler words, elongated sounds, trailing sentences, meta-questions, or a `[pause]` marker),
   - returns an **empty string** when the student is fluent — the client sees nothing,
   - or invokes **`phrase_generation_agent`** and **`safety_filter_agent`** as collaborators and returns a JSON array of 2–3 safe phrases.
3. **Pause heartbeat** — A background task on the server injects a `[pause]` marker chunk into the same thread when the student has been silent for longer than `HESITATION_PAUSE_MS` (default 3000 ms), so prolonged silence is visible to ContextAgent even without any new STT output.

All hesitation detection lives on the agent side. The browser is dumb by design.

## UI Features

The session screen surfaces everything happening end-to-end so the agent flow is transparent:

- **Start screen** — one button. No scenario picker; ContextAgent infers context from what it hears.
- **Phrase Cards** — clickable suggested phrases, auto-dismiss after 5 s.
- **Live Transcript** — STT results render as you speak: finalized text in black, interim (not-yet-final) text in italic grey.
- **Pipeline Log** — a dark log panel that streams each listener event in real time, timestamped and color-coded per stage:
  - `[listener]` — `pause_heartbeat` when a `[pause]` marker is injected
  - `[context_agent]` — `calling` → `responded` (raw prompt + full model output) → `silent` / `parse_failed` / `parsed` (final phrases)
- **Inline recap** — pressing *End Session* does **not** navigate away. The recap (hesitation count + phrases used) appears as an inline banner while the Live Transcript and Pipeline Log stay on screen so you can review the full run afterwards. *New Session* reloads the page.

Log events are pushed over the same WebSocket as phrase suggestions — see `on_event` in `commcopilot/orchestrate.py` and the `emit_log` closure in `server/app.py`.

## Tech Stack

- **Backend**: FastAPI + WebSocket (`httpx` async client)
- **Agent Orchestration**: IBM watsonx Orchestrate — IBM Cloud SaaS REST API
  (`POST {ORCHESTRATE_URL}/v1/orchestrate/{agent_id}/chat/completions`, OpenAI-compatible payload, `X-IBM-THREAD-ID` for thread persistence)
- **Auth**: IBM Cloud IAM — API key exchanged for a Bearer access token (cached 1h)
- **LLM**: IBM watsonx (via Orchestrate agents)
- **Speech-to-Text**: Browser Web Speech API (Chrome)
- **Frontend**: HTML/CSS/JS (no framework)

## Project Structure

```
├── agents/                    # IBM Orchestrate ADK agent definitions
│   └── context_agent.yaml     # Silent listener; collaborators: phrase_generation_agent, safety_filter_agent
├── commcopilot/               # Core backend package
│   ├── config.py              # Environment variables and thresholds
│   ├── session.py             # In-memory session state (session_id + thread_id + last_transcript_at)
│   └── orchestrate.py         # Orchestrate API client (IAM auth + call_context_listener)
├── server/
│   └── app.py                 # FastAPI WebSocket endpoint + pause heartbeat
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js                 # Web Speech API STT + WebSocket + log/transcript rendering
├── tests/
│   ├── conftest.py
│   ├── test_session.py
│   ├── test_orchestrate.py
│   └── test_websocket.py
├── requirements.txt
└── .env.example
```

`phrase_generation_agent` and `safety_filter_agent` are managed in the Orchestrate web UI and invoked as collaborators by ContextAgent — they are not defined in this repo.

---

## Setup

### Prerequisites

- Python 3.11–3.13 (3.14 is not supported by `ibm-watsonx-orchestrate`)
- Google Chrome (Web Speech API required for STT)
- IBM Cloud account with watsonx Orchestrate instance
- `phrase_generation_agent` and `safety_filter_agent` already created in your Orchestrate tenant

### 1. Install Python 3.13

Download from [python.org/downloads](https://www.python.org/downloads/) — choose **Python 3.13.x Windows installer (64-bit)**.

During installation: check **"Add Python to PATH"**.

Verify:
```bash
py -0        # should list 3.13 in the output
```

### 2. Clone and create virtual environment

```bash
git clone <repo-url>
cd IBM-SkillsBuild-DCC

py -3.13 -m venv .venv
source .venv/Scripts/activate      # Windows
# source .venv/bin/activate        # Mac/Linux
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Verify the Orchestrate CLI installed:
```bash
orchestrate --version
```

### 4. Configure environment variables

Copy the example file and fill in your values:
```bash
cp .env.example .env
```

```env
# IBM watsonx Orchestrate
ORCHESTRATE_URL=https://api.eu-gb.watson-orchestrate.cloud.ibm.com/instances/<your-instance-id>
ORCHESTRATE_API_KEY=<your-ibm-cloud-api-key>

# ContextAgent is the only agent called from the server; it invokes
# phrase_generation_agent + safety_filter_agent as collaborators.
CONTEXT_AGENT_ID=
```

**Where to find your credentials:**

- `ORCHESTRATE_URL` + `ORCHESTRATE_API_KEY`: IBM Cloud → Resource list → watsonx Orchestrate → Service credentials → Create → download JSON
- Personal IBM Cloud API key (recommended for CLI): Manage → Access (IAM) → API keys → Create

---

## Importing ContextAgent into Orchestrate

**1. Connect the CLI to your Orchestrate instance:**
```bash
orchestrate env add -n commcopilot-prod -u <ORCHESTRATE_URL> --type ibm_iam
orchestrate env activate commcopilot-prod
# Enter your IBM Cloud API key when prompted
```

**2. Make sure the collaborators already exist:**
```bash
orchestrate agents list
```

You must see both `phrase_generation_agent` and `safety_filter_agent` in the output. Their **exact names** (including any `_XXXXMS` suffix ADK may have attached) must match the `collaborators:` entries in `agents/context_agent.yaml`. If the suffixes differ, edit the yaml before importing.

**3. Import ContextAgent:**
```bash
orchestrate agents import -f agents/context_agent.yaml
```

**4. Get the ContextAgent id and paste it into `.env`:**
```bash
orchestrate agents list
```
```env
CONTEXT_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

---

## Run

```bash
source .venv/Scripts/activate   # if not already active
python -m server.app
```

Open [http://localhost:8000](http://localhost:8000) in **Chrome** and press *Start Session*.

> Web Speech API only works in Chrome. Other browsers do not support it.

---

## Tests

```bash
pytest
```

Unit tests cover session state, `call_context_listener` (silent / phrases / fenced JSON / pause marker), and WebSocket behavior (`transcript → thinking → phrases` vs `transcript → thinking → idle`). All IBM service calls are mocked.

> Note: the REST endpoint used by `commcopilot/orchestrate.py` is the IBM Cloud SaaS path
> `/v1/orchestrate/{agent_id}/chat/completions` (no `/api` prefix). The ADK next-gen spec uses
> `/api/v1/orchestrate/...` — do not confuse the two.

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `ORCHESTRATE_URL` | Yes | Orchestrate instance URL from service credentials |
| `ORCHESTRATE_API_KEY` | Yes | IBM Cloud IAM API key |
| `CONTEXT_AGENT_ID` | Yes | UUID of ContextAgent (the silent listener) |

## Tuning Reference

Thresholds live in `commcopilot/config.py`:

| Constant | Default | Meaning |
|---|---|---|
| `HESITATION_PAUSE_MS` | `3000` | Server-side idle time before injecting a `[pause]` marker into the listener thread |
| `PHRASE_AUTO_DISMISS_S` | `5` | How long phrase cards stay visible on the client |
| `ORCHESTRATE_TIMEOUT_S` | `15.0` | Per-call timeout — ContextAgent may chain phrase_generation_agent + safety_filter_agent internally, so keep headroom |
| `MIN_SPEECH_CONFIDENCE` | `0.6` | Minimum Web Speech API confidence to forward a final transcript |
| `TRANSCRIPT_WINDOW` | `10` | Sliding window of recent transcript segments kept in session state |
| `SESSION_TIMEOUT_S` | `1800` | Evict sessions idle longer than this (30 min) |
