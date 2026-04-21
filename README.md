# CommCopilot

Real-time AI conversation assistant for international students. CommCopilot listens to a live English conversation through the browser microphone, labels each sentence as *student* vs. *other* using a text-based DiarizationAgent, feeds the labeled chunks to a ContextAgent on IBM watsonx Orchestrate, and surfaces short phrase suggestions only when the agent detects that the student is hesitating.

Speaker diarization is done purely from text (no audio embeddings), using the streaming MPM variant from *Wu & Choi, "Do We Still Need Audio? Rethinking Speaker Diarization with a Text-Based Approach" (2025, arXiv:2506.11344)* — an 8-sentence sliding window with per-chunk majority voting, assuming exactly two speakers.

Built as part of the **IBM SkillsBuild AI Experiential Learning Lab**.

## How It Works

```
Microphone → Web Speech API → WebSocket ──▶ SlidingWindowAggregator (buffer)
                                                │
                                                ▼
                                      DiarizationAgent  ──▶ ["student"|"other", ...]
                                                │              (per-chunk votes)
                                                ▼
                                      pop_finalized()  (chunks exiting the 8-sentence window)
                                                │
                                                ▼
                               for each finalized chunk: "[label] [ts] text"
                                                │
                                                ▼
                                      ContextAgent (silent listener)
                                                │
                                                ├── [other]                 → update role/tone only (empty)
                                                ├── [student], fluent       → empty
                                                └── [student], hesitating   → phrase_generation_agent
                                                                             → safety_filter_agent
                                                                             → JSON array → UI
```

1. **Speech-to-Text** — Chrome's Web Speech API transcribes the conversation in the browser (interim + final results). The browser forwards each final chunk to the server; it never runs hesitation or diarization logic.
2. **Diarization (text-based MPM)** — Each chunk is buffered and sent to **DiarizationAgent** inside the last 8 sentences. For every window, the agent returns a JSON array of `"student"`/`"other"` labels aligned with the window. Each chunk accumulates votes while it stays in the window. Once it exits the window, the chunk is finalized by majority vote (ties break to `student`, controlled by `MPM_TIE_BREAK_LABEL`).
3. **ContextAgent** — The finalized, labeled chunk `"[student] [ts] text"` or `"[other] [ts] text"` is posted to **ContextAgent** on Orchestrate with a per-session `X-IBM-THREAD-ID`. ContextAgent:
   - uses `[other]` turns to infer **role / tone / intent** and stays silent,
   - on `[student]` chunks, decides whether the student is hesitating (filler words, elongated sounds, trailing sentences, repeated words, meta-questions),
   - returns an **empty string** when the student is fluent,
   - or uses **`phrase_generation_agent`** and **`safety_filter_agent`** (configured as tools in the Orchestrate web UI) to produce a JSON array of 2–3 safe phrases.

All hesitation detection, phrase generation, and safety filtering happen on the agent side. The browser is a dumb STT pipe by design. Each session gets its own pair of Orchestrate thread ids (one for ContextAgent, one for DiarizationAgent) so the two agents keep independent conversation histories.

## Tech Stack

- **Backend**: FastAPI + WebSocket (`httpx` async client)
- **Agent Orchestration**: IBM watsonx Orchestrate
- **Auth**: IBM Cloud IAM 
- **LLM**: IBM watsonx (via Orchestrate agents)
- **Speech-to-Text**: Browser Web Speech API (Chrome)
- **Frontend**: HTML/CSS/JS

## Agents

The server calls **DiarizationAgent** and **ContextAgent** directly. `phrase_generation_agent` and `safety_filter_agent` are configured as tools of ContextAgent inside the Orchestrate web UI — they are not called directly by this codebase.

| Agent | Where defined | Role |
|---|---|---|
| **DiarizationAgent** | Orchestrate | For each window of up to 8 sentences, returns a JSON array of `"student"`/`"other"` labels aligned with the window. Two-speaker assumption. |
| **ContextAgent** | Orchestrate | Silent listener. Consumes pre-labeled chunks (`[student]`/`[other]`), detects hesitation on `[student]` chunks, invokes the other two agents as tools, returns phrases or empty. |
| **phrase_generation_agent** | Orchestrate | Generates 3 candidate phrases given role/tone/intent context. Called by ContextAgent as a tool. |
| **safety_filter_agent** | Orchestrate | Screens candidate phrases for appropriateness. Called by ContextAgent as a tool. |

## Project Structure

```
├── agents/
│   ├── context_agent.yaml        # ContextAgent (tools: phrase_generation_agent, safety_filter_agent)
│   └── diarization_agent.yaml    # DiarizationAgent (per-window student/other classifier)
├── commcopilot/
│   ├── config.py                 # Environment variables, thresholds, MPM window settings
│   ├── session.py                # In-memory session state (context + diarization thread ids, aggregator)
│   ├── diarization/
│   │   ├── types.py              # LabeledChunk dataclass
│   │   └── aggregator.py         # SlidingWindowAggregator (streaming MPM)
│   └── orchestrate.py            # Orchestrate client (call_context_listener, call_diarization_agent)
├── server/
│   └── app.py                    # FastAPI WebSocket endpoint (per-session chunk worker)
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js                    # Web Speech API STT + WebSocket + phrase/log rendering
├── tests/
│   ├── conftest.py
│   ├── test_session.py
│   ├── test_diarization_aggregator.py
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

# Agent IDs — the server calls DiarizationAgent first, then ContextAgent.
# phrase_generation_agent + safety_filter_agent are tools of ContextAgent in Orchestrate.
DIARIZATION_AGENT_ID=
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

**3. Import the server-called agents:**
```bash
orchestrate agents import -f agents/diarization_agent.yaml
orchestrate agents import -f agents/context_agent.yaml
```

**4. Get the agent ids and paste them into `.env`:**
```bash
orchestrate agents list
```
```env
DIARIZATION_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
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

Unit tests cover session state (split thread ids + per-session aggregator), the MPM `SlidingWindowAggregator` (voting / window rollover / tie-break / flush), `call_context_listener` (silent / phrases / fenced JSON), `call_diarization_agent` (label parsing / window formatting / thread id pass-through), and WebSocket pipeline behavior (student chunk → phrases, other chunk → idle, diarization failure → tie-break, MPM window rollover). All IBM service calls are mocked.

> Note: the REST endpoint used by `commcopilot/orchestrate.py` is the IBM Cloud SaaS path
> `/v1/orchestrate/{agent_id}/chat/completions` (no `/api` prefix). The ADK next-gen spec uses
> `/api/v1/orchestrate/...` — do not confuse the two.

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `ORCHESTRATE_URL` | Yes | Orchestrate instance URL from service credentials |
| `ORCHESTRATE_API_KEY` | Yes | IBM Cloud IAM API key |
| `DIARIZATION_AGENT_ID` | Yes | UUID of DiarizationAgent (per-window student/other classifier) |
| `CONTEXT_AGENT_ID` | Yes | UUID of ContextAgent (the silent listener) |

## Tuning Reference

Thresholds live in `commcopilot/config.py`:

| Constant | Default | Meaning |
|---|---|---|
| `PHRASE_AUTO_DISMISS_S` | `5` | How long phrase cards stay visible on the client |
| `ORCHESTRATE_TIMEOUT_S` | `15.0` | Per-call timeout — ContextAgent may invoke phrase_generation_agent + safety_filter_agent as tools |
| `MIN_SPEECH_CONFIDENCE` | `0.6` | Minimum Web Speech API confidence to forward a final transcript |
| `TRANSCRIPT_WINDOW` | `10` | Sliding window of recent labeled transcript segments kept in session state |
| `SESSION_TIMEOUT_S` | `1800` | Evict sessions idle longer than this (30 min) |
| `MPM_WINDOW_SIZE` | `8` | Sentences per DiarizationAgent window (paper-optimal; chunks finalize by majority vote once they exit the window) |
| `MPM_TIE_BREAK_LABEL` | `"student"` | Majority-vote tie-break on diarization — defaults to `student` so hesitation detection errs toward firing |
