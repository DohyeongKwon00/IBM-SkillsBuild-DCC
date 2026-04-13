# CommCopilot

Real-time AI conversation assistant for international students. CommCopilot listens to live English conversations via the browser microphone, detects hesitations (pauses and filler words), and suggests contextually appropriate phrases the student can use to continue the conversation naturally.

Built as part of the **IBM SkillsBuild AI Experiential Learning Lab**.

## How It Works

```
Microphone → Web Speech API → Hesitation Detection → Orchestrate Agents → Phrase Suggestions
```

1. **Speech-to-Text** — Chrome's Web Speech API transcribes the live conversation in the browser
2. **Hesitation Detection** — Browser detects silence pauses (≥4000ms) and filler words (um, uh, like…)
3. **Context Inference** — ContextAgent infers speaker role, tone, and student intent from the transcript
4. **Phrase Generation** — PhraseAgent generates 3 short, natural phrases the student can say next
5. **Safety Filter** — SafetyAgent screens phrases for appropriateness before display

The pipeline is orchestrated via **IBM watsonx Orchestrate** with a SupervisorAgent that chains ContextAgent → PhraseAgent → SafetyAgent internally via collaborators.

## Tech Stack

- **Backend**: FastAPI + WebSocket
- **Agent Orchestration**: IBM watsonx Orchestrate ADK
- **LLM**: IBM watsonx (via Orchestrate agents)
- **Speech-to-Text**: Browser Web Speech API (Chrome)
- **Frontend**: HTML/CSS/JS

## Project Structure

```
├── agents/                    # IBM Orchestrate ADK agent definitions
│   ├── supervisor_agent.yaml  # Chains ContextAgent → PhraseAgent → SafetyAgent
│   ├── context_agent.yaml     # Infers role, tone, intent from transcript
│   ├── phrase_agent.yaml      # Generates 3 phrase suggestions
│   └── safety_agent.yaml      # Filters unsafe phrases
├── commcopilot/               # Core backend package
│   ├── config.py              # Environment variables and thresholds
│   ├── session.py             # In-memory session state (per WebSocket connection)
│   └── orchestrate.py        # Orchestrate API client (IAM auth + agent calls)
├── server/
│   └── app.py                 # FastAPI app with WebSocket endpoint
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js                 # Web Speech API, silence detection, phrase display
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

# Agent IDs (see section below for how to get these)
SUPERVISOR_AGENT_ID=
CONTEXT_AGENT_ID=
PHRASE_AGENT_ID=
SAFETY_AGENT_ID=

# true  = SupervisorAgent chains agents via collaborators (recommended)
# false = FastAPI calls 3 agents sequentially (fallback)
USE_SUPERVISOR=true
```

**Where to find your credentials:**

- `ORCHESTRATE_URL` + `ORCHESTRATE_API_KEY`: IBM Cloud → Resource list → watsonx Orchestrate → Service credentials → Create → download JSON
- Personal IBM Cloud API key (recommended for CLI): Manage → Access (IAM) → API keys → Create

---

## Connecting to Orchestrate Agents

### Option A: Use agents already created in the Orchestrate web UI

If the agents (ContextAgent, PhraseAgent, SafetyAgent, SupervisorAgent) are already set up in the Orchestrate web UI:

**1. Connect the CLI to your Orchestrate instance:**
```bash
orchestrate env add -n commcopilot-prod -u <ORCHESTRATE_URL> --type ibm_iam
orchestrate env activate commcopilot-prod
# Enter your IBM Cloud API key when prompted
```

**2. Get agent IDs:**
```bash
orchestrate agents list
```

Copy the UUID for each agent and paste into `.env`:
```env
SUPERVISOR_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
CONTEXT_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
PHRASE_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
SAFETY_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### Option B: Import agents from YAML definitions in this repo

If you want to create agents from the YAML files in `agents/`:

**1. Connect CLI (same as above):**
```bash
orchestrate env add -n commcopilot-prod -u <ORCHESTRATE_URL> --type ibm_iam
orchestrate env activate commcopilot-prod
```

**2. Import all 4 agents:**
```bash
orchestrate agents import -f agents/context_agent.yaml
orchestrate agents import -f agents/phrase_agent.yaml
orchestrate agents import -f agents/safety_agent.yaml
orchestrate agents import -f agents/supervisor_agent.yaml
```

**3. Get agent IDs and fill in `.env`:**
```bash
orchestrate agents list
```

The agents will also appear in the Orchestrate web UI where you can test them directly.

**Agent YAML format reference:**
```yaml
spec_version: v1
kind: native
name: AgentName
llm: watsonx/ibm/<model-id>
instructions: |
  Your agent instructions here.
tools: []
collaborators:
  - OtherAgentName   # SupervisorAgent only
```

---

## Run

```bash
source .venv/Scripts/activate   # if not already active
python -m server.app
```

Open [http://localhost:8000](http://localhost:8000) in **Chrome**.

> Web Speech API only works in Chrome. Other browsers do not support it.

---

## Tests

```bash
pytest
```

21 unit tests covering session state, Orchestrate client, and WebSocket behavior. All IBM service calls are mocked.

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `ORCHESTRATE_URL` | Yes | Orchestrate instance URL from service credentials |
| `ORCHESTRATE_API_KEY` | Yes | IBM Cloud IAM API key |
| `SUPERVISOR_AGENT_ID` | Yes (if USE_SUPERVISOR=true) | UUID of SupervisorAgent |
| `CONTEXT_AGENT_ID` | Yes (if USE_SUPERVISOR=false) | UUID of ContextAgent |
| `PHRASE_AGENT_ID` | Yes (if USE_SUPERVISOR=false) | UUID of PhraseAgent |
| `SAFETY_AGENT_ID` | Yes (if USE_SUPERVISOR=false) | UUID of SafetyAgent |
| `USE_SUPERVISOR` | No | `true` (default) or `false` |
