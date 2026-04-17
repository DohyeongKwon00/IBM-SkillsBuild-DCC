# CommCopilot

Real-time AI conversation assistant for international students. CommCopilot listens to a live English conversation through the browser microphone, streams each transcript chunk to a single ContextAgent on IBM watsonx Orchestrate, and surfaces short phrase suggestions only when the agent detects that the student is hesitating.

Built as part of the **IBM SkillsBuild AI Experiential Learning Lab**.

## How It Works

```
Microphone → Web Speech API → WebSocket → ContextAgent (silent listener)
                                              │
                                              ├── fluent  → stay silent (empty string)
                                              └── hesitating → phrase_generation_agent (tool)
                                                               → safety_filter_agent (tool)
                                                               → JSON array of phrases → UI
```

1. **Speech-to-Text** — Chrome's Web Speech API transcribes the conversation in the browser (interim + final results). The browser does **not** run any hesitation or filtering logic. It just forwards each final STT chunk to the server.
2. **ContextAgent** — The server POSTs every chunk to a single **ContextAgent** on Orchestrate as a silent message, tagged with a per-session `X-IBM-THREAD-ID` so the agent sees the running conversation. ContextAgent:
   - distinguishes **who is speaking** (student vs. the other party) from the thread context,
   - infers **role / tone / intent** from the thread itself (no scenario is provided),
   - decides per chunk whether the student is hesitating (filler words, elongated sounds, trailing sentences, repeated words, meta-questions),
   - returns an **empty string** when the student is fluent — the client sees nothing,
   - or uses **`phrase_generation_agent`** and **`safety_filter_agent`** (configured as tools in the Orchestrate web UI) to produce and return a JSON array of 2–3 safe phrases.

All hesitation detection, phrase generation, and safety filtering happen on the agent side. The browser is a dumb STT pipe by design.

## Tech Stack

- **Backend**: FastAPI + WebSocket (`httpx` async client)
- **Agent Orchestration**: IBM watsonx Orchestrate
- **Auth**: IBM Cloud IAM 
- **LLM**: IBM watsonx (via Orchestrate agents)
- **Speech-to-Text**: Browser Web Speech API (Chrome)
- **Frontend**: HTML/CSS/JS

## Agents

Only **ContextAgent** is called from the server. The other two agents are configured as tools of ContextAgent inside the Orchestrate web UI — they are not called directly by this codebase.

| Agent | Where defined | Role |
|---|---|---|
| **ContextAgent** | Orchestrate | Silent listener. Distinguishes student from the other speaker, detects hesitation, invokes the other two agents as tools, returns phrases or empty. |
| **phrase_generation_agent** | Orchestrate | Generates 3 candidate phrases given role/tone/intent context. Called by ContextAgent as a tool. |
| **safety_filter_agent** | Orchestrate | Screens candidate phrases for appropriateness. Called by ContextAgent as a tool. |

## Project Structure

```
├── agents/
│   └── context_agent.yaml     # ContextAgent definition (tools: phrase_generation_agent, safety_filter_agent)
├── commcopilot/
│   ├── config.py              # Environment variables and thresholds
│   ├── session.py             # In-memory session state (session_id + thread_id)
│   └── orchestrate.py         # Orchestrate API client (IAM auth + call_context_listener)
├── server/
│   └── app.py                 # FastAPI WebSocket endpoint
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js                 # Web Speech API STT + WebSocket + phrase/log rendering
├── tests/
│   ├── conftest.py
│   ├── test_session.py
│   ├── test_orchestrate.py
│   └── test_websocket.py
├── requirements.txt
└── .env.example
```

---

## Setup

### Prerequisites

- Python 3.11–3.13 (3.14 is not supported by `ibm-watsonx-orchestrate`)
- Google Chrome (Web Speech API required for STT)
- IBM Cloud account with watsonx Orchestrate instance
- `phrase_generation_agent` and `safety_filter_agent` already created in your Orchestrate tenant and configured as tools of ContextAgent

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

# ContextAgent ID — the only agent called from the server.
# phrase_generation_agent + safety_filter_agent are its tools in Orchestrate.
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

**2. Make sure the tool agents already exist:**
```bash
orchestrate agents list
```

You must see both `phrase_generation_agent` and `safety_filter_agent` in the output. They should already be configured as tools of ContextAgent in the Orchestrate web UI.

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

Unit tests cover session state, `call_context_listener` (silent / phrases / fenced JSON), and WebSocket behavior (`transcript → thinking → phrases` vs `transcript → thinking → idle`). All IBM service calls are mocked.

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
| `PHRASE_AUTO_DISMISS_S` | `5` | How long phrase cards stay visible on the client |
| `ORCHESTRATE_TIMEOUT_S` | `15.0` | Per-call timeout — ContextAgent may invoke phrase_generation_agent + safety_filter_agent as tools |
| `MIN_SPEECH_CONFIDENCE` | `0.6` | Minimum Web Speech API confidence to forward a final transcript |
| `TRANSCRIPT_WINDOW` | `10` | Sliding window of recent transcript segments kept in session state |
| `SESSION_TIMEOUT_S` | `1800` | Evict sessions idle longer than this (30 min) |
