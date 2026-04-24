# CommCopilot

Real-time AI conversation assistant for international students. CommCopilot streams microphone audio to **IBM Watson Speech to Text**, which produces speaker-labeled transcripts. A **ContextAgent** on IBM watsonx Orchestrate reads each transcript chunk, identifies whether the student is hesitating, and surfaces short phrase suggestions in real time.

Built as part of the **IBM SkillsBuild AI Experiential Learning Lab**.

## How It Works

```
Microphone → MediaRecorder (webm/opus) → WebSocket (binary) → FastAPI
                                                                   │
                                                          IBM Watson STT (WebSocket)
                                                                   │
                                                    "[Speaker 0]: um, I was wondering..."
                                                                   │
                                                         ContextAgent (Orchestrate)
                                                                   │
                                              ├── fluent  → silent (empty string)
                                              └── hesitating → phrase_generation_agent
                                                               → safety_filter_agent
                                                               → JSON array → UI
```

1. **Audio capture** — The browser captures microphone audio via `MediaRecorder` (webm/opus, 250 ms chunks) and streams binary frames to the server over WebSocket. No STT or hesitation logic runs in the browser.

2. **IBM Watson STT** — The server forwards each binary frame to IBM Watson Speech to Text over a persistent WebSocket connection (one per session). Watson returns speaker-labeled final transcripts: `[Speaker 0]: I was wondering...` The server identifies the dominant speaker per utterance using Watson's `speaker_labels` array and formats transcripts accordingly.

3. **ContextAgent** — Every labeled transcript chunk is sent to a single **ContextAgent** on Orchestrate, tagged with a per-session `X-IBM-THREAD-ID` so the agent sees the running conversation. ContextAgent:
   - identifies **which speaker is the student** using user context already in session memory,
   - infers **role / tone / intent** from the conversation thread (no scenario input needed),
   - detects **hesitation** in the student's speech (filler words, elongated sounds, trailing sentences, repeated words, meta-questions),
   - returns an **empty string** when the student is fluent — the client sees nothing,
   - or uses **`phrase_generation_agent`** and **`safety_filter_agent`** (tools configured inside Orchestrate) to produce and return a JSON array of 2–3 safe phrase suggestions.

All hesitation detection, phrase generation, and safety filtering happen on the agent side.

## Tech Stack

| Component | Technology |
|---|---|
| Backend | FastAPI + WebSocket |
| Speech-to-Text | IBM Watson Speech to Text (streaming WebSocket) |
| Agent Orchestration | IBM watsonx Orchestrate |
| Auth | IBM Cloud IAM |
| LLM | IBM watsonx Granite (via Orchestrate agents) |
| Frontend | HTML / CSS / Vanilla JS |

## Agents

Only **ContextAgent** is called from the server. The other two agents are configured as tools of ContextAgent inside the Orchestrate web UI.

| Agent | Role |
|---|---|
| **ContextAgent** | Silent listener. Identifies the student speaker, detects hesitation, invokes the other two agents as tools, returns phrases or stays silent. |
| **phrase_generation_agent** | Generates 3 candidate phrases given role / tone / intent. Called by ContextAgent as a tool. |
| **safety_filter_agent** | Screens candidate phrases for appropriateness. Called by ContextAgent as a tool. |

## Project Structure

```
├── agents/
│   └── context_agent.yaml     # ContextAgent definition
├── commcopilot/
│   ├── config.py              # Environment variables and thresholds
│   ├── session.py             # In-memory session state
│   ├── orchestrate.py         # Orchestrate API client (IAM auth + call_context_listener)
│   └── watson_stt.py          # IBM Watson STT WebSocket client (per-session)
├── server/
│   └── app.py                 # FastAPI WebSocket endpoint
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js                 # MediaRecorder audio streaming + phrase/log rendering
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

- Python 3.11–3.13
- Google Chrome or Edge (MediaRecorder webm/opus required)
- IBM Cloud account with:
  - Watson Speech to Text instance (Lite plan is sufficient)
  - watsonx Orchestrate instance
  - `phrase_generation_agent` and `safety_filter_agent` already created in Orchestrate and configured as tools of ContextAgent

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd IBM-SkillsBuild-DCC

py -3.13 -m venv .venv
source .venv/Scripts/activate      # Windows
# source .venv/bin/activate        # Mac/Linux
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Fill in `.env`:

```env
# IBM Watson Speech to Text
# IBM Cloud Console → Speech to Text → Manage
WATSON_STT_API_KEY=<your-watson-stt-api-key>
WATSON_STT_URL=https://api.us-south.speech-to-text.watson.cloud.ibm.com/instances/<your-instance-id>

# IBM watsonx Orchestrate
ORCHESTRATE_URL=https://api.eu-gb.watson-orchestrate.cloud.ibm.com/instances/<your-instance-id>
ORCHESTRATE_API_KEY=<your-ibm-cloud-api-key>

# ContextAgent ID — run `orchestrate agents list` after importing to get this
CONTEXT_AGENT_ID=
```

**Where to find credentials:**
- **Watson STT** — IBM Cloud → Resource list → Speech to Text → Manage → copy API key and URL
- **Orchestrate** — IBM Cloud → Resource list → watsonx Orchestrate → Service credentials

---

## Importing ContextAgent into Orchestrate

**1. Connect the CLI:**
```bash
orchestrate env add -n commcopilot -u <ORCHESTRATE_URL> --type ibm_iam
orchestrate env activate commcopilot
```

**2. Verify tool agents exist:**
```bash
orchestrate agents list
# must show phrase_generation_agent and safety_filter_agent
```

**3. Import ContextAgent:**
```bash
orchestrate agents import -f agents/context_agent.yaml
```

**4. Get the agent ID and set it in `.env`:**
```bash
orchestrate agents list
```
```env
CONTEXT_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

---

## Run

```bash
# activate venv first if not already active
source .venv/Scripts/activate   # Windows
# source .venv/bin/activate     # Mac/Linux

uvicorn server.app:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in Chrome or Edge and press **Start Session**.

The server log will show `Watson STT connected and listening` once the STT connection is established.

---

## Tests

```bash
pytest
```

Covers session state, `call_context_listener` (silent / phrases / fenced JSON), and WebSocket behavior. All IBM service calls are mocked — no real credentials needed to run the test suite.

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `WATSON_STT_API_KEY` | Yes | IBM Watson STT API key |
| `WATSON_STT_URL` | Yes | IBM Watson STT instance URL (full URL from IBM Cloud Console) |
| `ORCHESTRATE_URL` | Yes | watsonx Orchestrate instance URL |
| `ORCHESTRATE_API_KEY` | Yes | IBM Cloud IAM API key |
| `CONTEXT_AGENT_ID` | Yes | UUID of ContextAgent |

## Tuning Reference

Constants in `commcopilot/config.py`:

| Constant | Default | Meaning |
|---|---|---|
| `PHRASE_AUTO_DISMISS_S` | `5` | Seconds phrase cards stay visible |
| `ORCHESTRATE_TIMEOUT_S` | `15.0` | Per-call timeout for ContextAgent |
| `TRANSCRIPT_WINDOW` | `10` | Sliding window of recent transcript segments in session state |
| `SESSION_TIMEOUT_S` | `1800` | Evict idle sessions after 30 min |
| `WATSON_STT_MODEL` | `en-US_BroadbandModel` | Watson STT recognition model |
