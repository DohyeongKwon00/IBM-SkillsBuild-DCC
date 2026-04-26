# CommCopilot

Real-time AI conversation assistant for international students. CommCopilot streams two separate microphone inputs to **AssemblyAI Speech to Text**, labels transcripts by source stream, and sends Carter's turns to a **ContextAgent** on IBM watsonx Orchestrate. The agent identifies hesitation in Carter's speech and surfaces contextually relevant phrase suggestions in real time.

Built as part of the **IBM SkillsBuild AI Experiential Learning Lab**.

## How It Works

```
Carter mic      → AudioContext PCM16 → WebSocket source=carter    → AssemblyAI STT session 1 → "[Carter]: ..."
Prof. Johnson mic → AudioContext PCM16 → WebSocket source=professor → AssemblyAI STT session 2 → "[Prof. Johnson]: ..."
                                                                                                      │
                                                                                         merged conversation history
                                                                                                      │
                                                                                         Carter turns → ContextAgent
                                                                                                      │
                                                                      ├── Carter fluent   → silent (empty string)
                                                                      └── Carter hesitates → phrase_generation_agent
                                                                                             → safety_filter_agent
                                                                                             → JSON array → UI
```

1. **Audio capture** — The browser lets the user select two microphone inputs. Carter's microphone and Prof. Johnson's microphone are captured as separate `AudioContext` + `ScriptProcessorNode` streams (PCM16, 16 kHz, 4096-sample chunks). No STT or hesitation logic runs in the browser.

2. **AssemblyAI STT** — The server opens two AssemblyAI real-time streaming WebSocket sessions, one per microphone source. CommCopilot does not rely on AssemblyAI speaker diarization for identity; transcripts are labeled from the source stream: `[Carter]: ...` and `[Prof. Johnson]: ...`.

3. **ContextAgent** — Transcript chunks from both sources are merged into one chronological conversation history. Carter chunks are sent to a single **ContextAgent** on Orchestrate, tagged with a per-session `X-IBM-THREAD-ID` and metadata including `current_user: Carter`, `ai_solution_user: Carter`, and `known_speakers: ["Carter", "Prof. Johnson"]`. ContextAgent has pre-loaded context about the student and session:

   **Student profile (built into agent instructions):**
   - Name: Carter Lee, international student living in the US
   - English proficiency: conversational but limited — struggles with technical/academic vocabulary
   - Goal: receive phrase suggestions when he hesitates so he can respond confidently

   **Session scenario (built into agent instructions):**
   - Carter is visiting his professor's office hours to ask about his exam grade
   - Role: student talking to professor (formal to semi-formal tone)
   - Carter's goal: understand his grade, ask how to improve, or request reconsideration

   On every chunk, ContextAgent:
   - trusts the source labels `[Carter]` and `[Prof. Johnson]`,
   - tracks **role / tone / current intent** as the conversation unfolds,
   - detects **hesitation** in Carter's speech only (filler words, elongated sounds, trailing sentences, repeated words, meta-questions),
   - treats Prof. Johnson's speech as context only,
   - returns an **empty string** when Carter is fluent — the client sees nothing,
   - or invokes **`phrase_generation_agent`** and **`safety_filter_agent`** to produce 2–3 phrases that Carter would **naturally say next** to continue his current thought, specific to the situation.

All hesitation detection, phrase generation, and safety filtering happen on the agent side.

## UI Features

- **Live Transcript** — Speaker-labeled transcript lines appear in real time as AssemblyAI returns final results.
- **Phrase cards** — When ContextAgent detects hesitation, 2–3 suggestion cards appear. They auto-dismiss after 5 seconds. Clicking a card highlights it as selected.
- **Suggested Phrases History** — A persistent panel records every batch of suggestions shown during the session, grouped by time. Phrases Carter selected are marked with a checkmark (✓) in blue.
- **Pipeline Log** — Full per-chunk agent activity log (prompt, raw output, parsed phrases) for debugging.
- **Session Recap** — Summary shown when the session ends: hesitation count, phrases used.

## Tech Stack

| Component | Technology |
|---|---|
| Backend | FastAPI + WebSocket |
| Speech-to-Text | Two AssemblyAI Streaming v3 sessions (Universal-3 Pro), one per mic source |
| Agent Orchestration | IBM watsonx Orchestrate |
| Auth | IBM Cloud IAM |
| LLM | IBM watsonx Granite (via Orchestrate agents) |
| Frontend | HTML / CSS / Vanilla JS |

## Agents

Only **ContextAgent** is called from the server. The other two agents are configured as collaborators of ContextAgent inside the Orchestrate web UI.

| Agent | Role |
|---|---|
| **ContextAgent** | Silent listener. Knows Carter's profile and the session scenario. Uses fixed Carter/Prof. Johnson source labels, detects hesitation in Carter's speech, invokes the other two agents, returns contextually relevant phrases or stays silent. |
| **phrase_generation_agent** | Generates 3 candidate phrases that Carter would naturally say next, given role, tone, and current intent. Called by ContextAgent as a collaborator. |
| **safety_filter_agent** | Screens candidate phrases for appropriateness. Called by ContextAgent as a collaborator. |

## Project Structure

```
├── agents/
│   └── context_agent.yaml       # ContextAgent definition (profile + scenario + instructions)
├── commcopilot/
│   ├── config.py                # Environment variables and thresholds
│   ├── session.py               # In-memory session state
│   ├── orchestrate.py           # Orchestrate API client (IAM auth + call_context_listener)
│   └── assemblyai_stt.py        # AssemblyAI real-time STT WebSocket client (one per mic source)
├── server/
│   └── app.py                   # FastAPI WebSocket endpoint
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js                   # Dual-mic AudioContext PCM16 streaming + phrase/history/log rendering
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
- Google Chrome or Edge
- AssemblyAI account (free tier works) — get API key at [assemblyai.com](https://www.assemblyai.com/dashboard)
- IBM Cloud account with:
  - watsonx Orchestrate instance
  - `phrase_generation_agent` and `safety_filter_agent` already created in Orchestrate and configured as collaborators of ContextAgent

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
# AssemblyAI Speech to Text
ASSEMBLYAI_API_KEY=<your-assemblyai-api-key>

# IBM watsonx Orchestrate
ORCHESTRATE_URL=https://api.eu-gb.watson-orchestrate.cloud.ibm.com/instances/<your-instance-id>
ORCHESTRATE_API_KEY=<your-ibm-cloud-api-key>

# ContextAgent ID — run `orchestrate agents list` after importing to get this
CONTEXT_AGENT_ID=
```

**Where to find credentials:**
- **AssemblyAI** — [assemblyai.com/dashboard](https://www.assemblyai.com/dashboard) → API Keys
- **Orchestrate** — IBM Cloud → Resource list → watsonx Orchestrate → Service credentials

---

## Importing ContextAgent into Orchestrate

**1. Connect the CLI:**
```bash
orchestrate env add -n commcopilot -u <ORCHESTRATE_URL> --type ibm_iam
orchestrate env activate commcopilot
```

**2. Verify collaborator agents exist:**
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

> After any change to `context_agent.yaml`, re-run `orchestrate agents import -f agents/context_agent.yaml` to apply updates.

---

## Run

```bash
# activate venv first if not already active
source .venv/Scripts/activate   # Windows
# source .venv/bin/activate     # Mac/Linux

uvicorn server.app:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in Chrome or Edge, select two microphone inputs, and press **Start Session**.

The server log will show `AssemblyAI STT connected (Carter)` and `AssemblyAI STT connected (Prof. Johnson)` once both STT connections are established.

---

## Tests

```bash
pytest
```

Covers session state, `call_context_listener` (silent / phrases / fenced JSON), and WebSocket behavior. All external service calls are mocked — no real credentials needed to run the test suite.

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `ASSEMBLYAI_API_KEY` | Yes | AssemblyAI API key |
| `ORCHESTRATE_URL` | Yes | watsonx Orchestrate instance URL |
| `ORCHESTRATE_API_KEY` | Yes | IBM Cloud IAM API key |
| `CONTEXT_AGENT_ID` | Yes | UUID of ContextAgent |

## Tuning Reference

Constants in `commcopilot/config.py`:

| Constant | Default | Meaning |
|---|---|---|
| `PHRASE_AUTO_DISMISS_S` | `5` | Seconds phrase cards stay visible before auto-dismissing |
| `ORCHESTRATE_TIMEOUT_S` | `15.0` | Per-call timeout for ContextAgent |
| `TRANSCRIPT_WINDOW` | `10` | Sliding window of recent transcript segments kept in session state |
| `SESSION_TIMEOUT_S` | `1800` | Evict idle sessions after 30 min |
