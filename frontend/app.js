/**
 * CommCopilot Frontend
 *
 * The browser captures microphone audio via AudioContext (PCM16, 16 kHz)
 * and streams binary frames to the server over WebSocket. The server forwards
 * audio to AssemblyAI STT, which returns speaker-labeled transcripts.
 * ContextAgent then decides whether the student is hesitating and returns
 * phrase suggestions.
 *
 * No STT or hesitation detection happens in the browser — it only captures and streams.
 */

const WS_RECONNECT_DELAYS = [1000, 2000, 4000];
const SAMPLE_RATE = 16000;
const BUFFER_SIZE = 4096;  // ~256ms at 16 kHz

let ws = null;
let mediaStream = null;
let audioContext = null;
let scriptProcessor = null;
let reconnectAttempt = 0;
let dismissTimer = null;
let isSessionActive = false;
let AUTO_DISMISS_MS = 5000;

// --- DOM refs ---
const startScreen = document.getElementById("start-screen");
const sessionScreen = document.getElementById("session-screen");
const startBtn = document.getElementById("start-btn");
const statusIndicator = document.getElementById("status-indicator");
const phraseContainer = document.getElementById("phrase-container");
const selectedPhraseEl = document.getElementById("selected-phrase");
const errorBar = document.getElementById("error-bar");
const inlineRecapEl = document.getElementById("inline-recap");
const transcriptEl = document.getElementById("transcript-final");
const logPanelEl = document.getElementById("log-panel");
const phraseHistoryEl = document.getElementById("phrase-history");

// tracks which phrases were selected, for history highlighting
const selectedPhrases = new Set();

// --- Session start ---
async function startSession() {
    if (!window.AudioContext && !window.webkitAudioContext) {
        showError("AudioContext not supported. Please use Chrome or Edge.");
        return;
    }

    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (e) {
        showError("Mic permission denied. Please allow microphone access.");
        return;
    }

    startScreen.style.display = "none";
    sessionScreen.style.display = "block";
    isSessionActive = true;

    connectWebSocket();
}

// --- WebSocket ---
function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/ws`);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
        reconnectAttempt = 0;
        hideError();
        ws.send(JSON.stringify({ type: "start" }));
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.type === "session_ready") {
            if (msg.phrase_auto_dismiss_s) AUTO_DISMISS_MS = msg.phrase_auto_dismiss_s * 1000;
            statusIndicator.textContent = "Listening...";
            startAudioStreaming();

        } else if (msg.type === "thinking") {
            statusIndicator.textContent = "Thinking...";
            statusIndicator.className = "processing";

        } else if (msg.type === "idle") {
            statusIndicator.textContent = "Listening...";
            statusIndicator.className = "";

        } else if (msg.type === "phrases") {
            showPhrases(msg.phrases);

        } else if (msg.type === "log") {
            appendLog(msg);

        } else if (msg.type === "recap") {
            showRecap(msg.recap, msg.phrases_used);

        } else if (msg.type === "transcript") {
            appendTranscriptLine(msg.text);

        } else if (msg.type === "error") {
            showError(msg.message || "An error occurred.");
        }
    };

    ws.onclose = () => {
        if (!isSessionActive) return;
        if (reconnectAttempt < WS_RECONNECT_DELAYS.length) {
            const delay = WS_RECONNECT_DELAYS[reconnectAttempt];
            showError(`Connection lost. Reconnecting in ${delay / 1000}s...`);
            setTimeout(() => {
                reconnectAttempt++;
                connectWebSocket();
            }, delay);
        } else {
            showError("Connection lost. Please refresh the page.");
        }
    };

    ws.onerror = () => {};
}

function sendMessage(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
    }
}

// --- Audio streaming (PCM16 via AudioContext -> AssemblyAI) ---
function startAudioStreaming() {
    const AC = window.AudioContext || window.webkitAudioContext;
    audioContext = new AC({ sampleRate: SAMPLE_RATE });

    const source = audioContext.createMediaStreamSource(mediaStream);
    scriptProcessor = audioContext.createScriptProcessor(BUFFER_SIZE, 1, 1);

    scriptProcessor.onaudioprocess = (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        const float32 = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++) {
            const s = Math.max(-1, Math.min(1, float32[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        ws.send(int16.buffer);
    };

    source.connect(scriptProcessor);
    scriptProcessor.connect(audioContext.destination);
}

// --- Phrase display ---
function showPhrases(phrases) {
    phraseContainer.innerHTML = "";
    statusIndicator.textContent = "Listening...";
    statusIndicator.className = "";

    phrases.forEach((phrase) => {
        const card = document.createElement("div");
        card.className = "phrase-card";
        card.textContent = phrase;
        card.onclick = () => selectPhrase(phrase);
        phraseContainer.appendChild(card);
    });

    appendPhraseHistory(phrases);

    if (dismissTimer) clearTimeout(dismissTimer);
    dismissTimer = setTimeout(() => {
        phraseContainer.innerHTML = "";
    }, AUTO_DISMISS_MS);
}

function selectPhrase(phrase) {
    if (dismissTimer) clearTimeout(dismissTimer);
    phraseContainer.innerHTML = "";
    selectedPhraseEl.textContent = phrase;
    selectedPhraseEl.style.display = "block";
    sendMessage({ type: "phrase_selected", phrase });
    selectedPhrases.add(phrase);
    markPhraseUsedInHistory(phrase);

    setTimeout(() => {
        selectedPhraseEl.style.display = "none";
    }, 4000);
}

// --- Phrase history ---
function appendPhraseHistory(phrases) {
    const empty = phraseHistoryEl.querySelector(".phrase-history-empty");
    if (empty) empty.remove();

    const now = new Date();
    const time = now.toTimeString().slice(0, 8);

    const group = document.createElement("div");
    group.className = "phrase-history-group";

    const timeEl = document.createElement("div");
    timeEl.className = "phrase-history-time";
    timeEl.textContent = time;
    group.appendChild(timeEl);

    phrases.forEach((phrase) => {
        const item = document.createElement("div");
        item.className = "phrase-history-item";
        if (selectedPhrases.has(phrase)) item.classList.add("used");
        item.textContent = phrase;
        item.dataset.phrase = phrase;
        group.appendChild(item);
    });

    phraseHistoryEl.appendChild(group);
    phraseHistoryEl.scrollTop = phraseHistoryEl.scrollHeight;
}

function markPhraseUsedInHistory(phrase) {
    phraseHistoryEl.querySelectorAll(".phrase-history-item").forEach((el) => {
        if (el.dataset.phrase === phrase) el.classList.add("used");
    });
}

// --- Recap ---
function showRecap(recap, phrasesUsed) {
    isSessionActive = false;

    statusIndicator.textContent = "Ended";
    statusIndicator.className = "";
    phraseContainer.innerHTML = "";
    if (dismissTimer) clearTimeout(dismissTimer);

    let html = `<p>${recap}</p>`;
    if (phrasesUsed && phrasesUsed.length > 0) {
        html += "<h4>Phrases you used:</h4><ul>";
        phrasesUsed.forEach((p) => { html += `<li>${p}</li>`; });
        html += "</ul>";
    }
    html += '<button id="restart-btn">New Session</button>';
    inlineRecapEl.innerHTML = html;
    inlineRecapEl.style.display = "block";
    document.getElementById("restart-btn").onclick = () => location.reload();

    const endBtn = document.getElementById("end-btn");
    if (endBtn) endBtn.disabled = true;

    cleanup();
}

// --- End session ---
document.getElementById("end-btn").onclick = () => {
    sendMessage({ type: "end_session" });
};

// --- Transcript display (speaker-labeled lines from Watson STT via server log) ---
function appendTranscriptLine(text) {
    const line = document.createElement("div");
    line.textContent = text;
    transcriptEl.appendChild(line);
    const parent = document.getElementById("live-transcript");
    parent.scrollTop = parent.scrollHeight;
}

// --- Pipeline log ---
function appendLog(msg) {
    const entry = document.createElement("div");
    entry.className = "log-entry";

    const now = new Date();
    const time = now.toTimeString().slice(0, 8);
    const stage = msg.stage || "event";
    const status = msg.status || "";
    const detail = msg.detail || "";

    const header = document.createElement("div");
    header.className = "log-header";
    header.innerHTML =
        `<span class="log-time">${time}</span>` +
        `<span class="log-stage ${stage}">[${stage}]</span>` +
        `<span class="log-status">${status}</span>` +
        `<span class="log-detail"></span>`;
    header.querySelector(".log-detail").textContent = detail;
    entry.appendChild(header);

    const addBlock = (label, value) => {
        if (value === undefined || value === null || value === "") return;
        const block = document.createElement("div");
        block.className = "log-block";
        const lab = document.createElement("span");
        lab.className = "log-label";
        lab.textContent = label + ": ";
        const body = document.createElement("span");
        body.className = "log-body";
        body.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
        block.appendChild(lab);
        block.appendChild(body);
        entry.appendChild(block);
    };

    addBlock("prompt", msg.prompt);
    addBlock("output", msg.output);
    addBlock("parsed", msg.phrases);

    logPanelEl.appendChild(entry);
    logPanelEl.scrollTop = logPanelEl.scrollHeight;
}

// --- Error ---
function showError(msg) {
    errorBar.textContent = msg;
    errorBar.style.display = "block";
}
function hideError() {
    errorBar.style.display = "none";
}

// --- Cleanup ---
function cleanup() {
    if (scriptProcessor) {
        scriptProcessor.disconnect();
        scriptProcessor = null;
    }
    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }
    if (mediaStream) {
        mediaStream.getTracks().forEach((t) => t.stop());
        mediaStream = null;
    }
    if (ws) {
        ws.close();
        ws = null;
    }
}

// --- Init ---
startBtn.onclick = () => startSession();
