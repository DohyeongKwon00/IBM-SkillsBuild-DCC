/**
 * CommCopilot Frontend
 *
 * The browser captures two microphones via AudioContext (PCM16, 16 kHz)
 * and streams source-prefixed binary frames to the server over WebSocket.
 * The server forwards each source to a separate AssemblyAI STT session and
 * labels transcripts from the source stream.
 *
 * No STT or hesitation detection happens in the browser — it only captures and streams.
 */

const WS_RECONNECT_DELAYS = [1000, 2000, 4000];
const SAMPLE_RATE = 16000;
const BUFFER_SIZE = 4096;  // ~256ms at 16 kHz
const AUDIO_SOURCES = {
    speakerA: { code: 1, label: "Speaker A" },
    speakerB: { code: 2, label: "Speaker B" },
};

let ws = null;
const mediaStreams = {};
const audioContexts = {};
const scriptProcessors = {};
let reconnectAttempt = 0;
let isSessionActive = false;

// --- DOM refs ---
const startScreen = document.getElementById("start-screen");
const sessionScreen = document.getElementById("session-screen");
const startBtn = document.getElementById("start-btn");
const refreshMicsBtn = document.getElementById("refresh-mics-btn");
const speakerAMicSelect = document.getElementById("speaker-a-mic-select");
const speakerBMicSelect = document.getElementById("speaker-b-mic-select");
const statusIndicator = document.getElementById("status-indicator");
const phraseContainer = document.getElementById("phrase-container");
const selectedPhraseEl = document.getElementById("selected-phrase");
const errorBar = document.getElementById("error-bar");
const inlineRecapEl = document.getElementById("inline-recap");
const transcriptEl = document.getElementById("transcript-final");
const logPanelEl = document.getElementById("log-panel");
const logToggleBtn = document.getElementById("log-toggle-btn");
const logToggleIcon = document.getElementById("log-toggle-icon");
const phraseHistoryEl = document.getElementById("phrase-history");
const phraseHistoryToggleBtn = document.getElementById("phrase-history-toggle-btn");
const phraseHistoryToggleIcon = document.getElementById("phrase-history-toggle-icon");

// tracks which phrases were selected, for history highlighting
const selectedPhrases = new Set();

// --- Microphone selection ---
async function loadMicrophoneOptions() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
        showError("Microphone device selection is not supported in this browser.");
        return;
    }

    if (!window.isSecureContext) {
        showError("Microphone access requires localhost or HTTPS.");
        return;
    }

    let permissionStream = null;
    try {
        permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (e) {
        showError("Mic permission denied. Please allow microphone access, then refresh mics.");
        return;
    } finally {
        if (permissionStream) {
            permissionStream.getTracks().forEach((track) => track.stop());
        }
    }

    let audioInputs = [];
    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        audioInputs = devices.filter((device) => device.kind === "audioinput");
    } catch (e) {
        showError("Could not list microphone inputs.");
        return;
    }

    populateMicSelect(speakerAMicSelect, audioInputs, 0);
    populateMicSelect(speakerBMicSelect, audioInputs, Math.min(1, audioInputs.length - 1));

    if (audioInputs.length < 2) {
        showError("Two microphone inputs are required for dual-mic mode.");
    } else {
        hideError();
    }
}

function populateMicSelect(select, devices, selectedIndex) {
    select.innerHTML = "";
    devices.forEach((device, index) => {
        const option = document.createElement("option");
        option.value = device.deviceId;
        option.textContent = device.label || `Microphone ${index + 1}`;
        if (index === selectedIndex) option.selected = true;
        select.appendChild(option);
    });
}

// --- Session start ---
async function startSession() {
    if (!window.AudioContext && !window.webkitAudioContext) {
        showError("AudioContext not supported. Please use Chrome or Edge.");
        return;
    }

    const speakerADeviceId = speakerAMicSelect.value;
    const speakerBDeviceId = speakerBMicSelect.value;

    if (!speakerADeviceId || !speakerBDeviceId) {
        showError("Please select microphones for both Speaker A and Speaker B.");
        return;
    }
    if (speakerADeviceId === speakerBDeviceId) {
        showError("Please select two different microphone inputs.");
        return;
    }

    try {
        mediaStreams.speakerA = await getMicStream(speakerADeviceId);
        mediaStreams.speakerB = await getMicStream(speakerBDeviceId);
    } catch (e) {
        showError("Mic permission denied or unavailable. Please allow both microphones.");
        return;
    }

    startScreen.style.display = "none";
    sessionScreen.style.display = "block";
    isSessionActive = true;

    connectWebSocket();
}

function getMicStream(deviceId) {
    return navigator.mediaDevices.getUserMedia({
        audio: {
            deviceId: { exact: deviceId },
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
        video: false,
    });
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
            statusIndicator.textContent = "Listening...";
            startAudioStreaming("speakerA");
            startAudioStreaming("speakerB");

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
            stopSessionAfterFatalError(msg.message || "An error occurred.");
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

// --- Audio streaming (PCM16 via AudioContext -> backend -> AssemblyAI) ---
function startAudioStreaming(sourceId) {
    stopAudioPipeline(sourceId);

    const AC = window.AudioContext || window.webkitAudioContext;
    const audioContext = new AC({ sampleRate: SAMPLE_RATE });
    audioContexts[sourceId] = audioContext;

    const source = audioContext.createMediaStreamSource(mediaStreams[sourceId]);
    const scriptProcessor = audioContext.createScriptProcessor(BUFFER_SIZE, 1, 1);
    scriptProcessors[sourceId] = scriptProcessor;

    scriptProcessor.onaudioprocess = (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        const float32 = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(float32.length);
        for (let i = 0; i < float32.length; i++) {
            const s = Math.max(-1, Math.min(1, float32[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        sendAudioFrame(sourceId, int16.buffer);
    };

    source.connect(scriptProcessor);
    scriptProcessor.connect(audioContext.destination);
}

function sendAudioFrame(sourceId, pcmBuffer) {
    const source = AUDIO_SOURCES[sourceId];
    if (!source) return;

    const payload = new Uint8Array(1 + pcmBuffer.byteLength);
    payload[0] = source.code;
    payload.set(new Uint8Array(pcmBuffer), 1);
    ws.send(payload.buffer);
}

function stopAudioPipeline(sourceId) {
    if (scriptProcessors[sourceId]) {
        scriptProcessors[sourceId].disconnect();
        delete scriptProcessors[sourceId];
    }
    if (audioContexts[sourceId]) {
        audioContexts[sourceId].close();
        delete audioContexts[sourceId];
    }
}

// --- Phrase display ---
function showPhraseEmptyState() {
    phraseContainer.innerHTML = '<span class="phrase-empty">No active suggestions.</span>';
}

function showPhrases(phrases) {
    phraseContainer.innerHTML = "";
    statusIndicator.textContent = "Listening...";
    statusIndicator.className = "";

    phrases.forEach((phrase) => {
        const card = document.createElement("div");
        card.className = "phrase-card";
        card.textContent = phrase;
        card.dataset.phrase = phrase;
        card.onclick = () => selectPhrase(phrase);
        phraseContainer.appendChild(card);
    });

    appendPhraseHistory(phrases);
}

function selectPhrase(phrase) {
    selectedPhraseEl.textContent = phrase;
    selectedPhraseEl.style.display = "block";
    sendMessage({ type: "phrase_selected", phrase });
    selectedPhrases.add(phrase);
    markPhraseUsedInHistory(phrase);
    markActivePhraseSelected(phrase);

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
    updatePhraseHistoryVisibility();
    phraseHistoryEl.scrollTop = phraseHistoryEl.scrollHeight;
}

function updatePhraseHistoryVisibility() {
    const isExpanded = phraseHistoryToggleBtn.getAttribute("aria-expanded") === "true";
    phraseHistoryEl.classList.toggle("expanded", isExpanded);
    const groups = phraseHistoryEl.querySelectorAll(".phrase-history-group");
    groups.forEach((group, index) => {
        group.hidden = !isExpanded && index < groups.length - 1;
    });
}

function togglePhraseHistory() {
    const isExpanded = phraseHistoryToggleBtn.getAttribute("aria-expanded") === "true";
    phraseHistoryToggleBtn.setAttribute("aria-expanded", String(!isExpanded));
    phraseHistoryToggleIcon.textContent = isExpanded ? "Show All" : "Show Latest";
    updatePhraseHistoryVisibility();
    phraseHistoryEl.scrollTop = phraseHistoryEl.scrollHeight;
}

function markPhraseUsedInHistory(phrase) {
    phraseHistoryEl.querySelectorAll(".phrase-history-item").forEach((el) => {
        if (el.dataset.phrase === phrase) el.classList.add("used");
    });
}

function markActivePhraseSelected(phrase) {
    phraseContainer.querySelectorAll(".phrase-card").forEach((el) => {
        el.classList.toggle("selected", el.dataset.phrase === phrase);
    });
}

// --- Recap ---
function showRecap(recap, phrasesUsed) {
    isSessionActive = false;

    statusIndicator.textContent = "Ended";
    statusIndicator.className = "";
    showPhraseEmptyState();

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

// --- Transcript display (source-labeled lines from AssemblyAI STT) ---
function appendTranscriptLine(text) {
    const line = document.createElement("div");
    line.textContent = text;
    transcriptEl.appendChild(line);
    const parent = document.getElementById("live-transcript");
    parent.scrollTop = parent.scrollHeight;
}

// --- Pipeline log ---
function togglePipelineLog() {
    const isExpanded = logToggleBtn.getAttribute("aria-expanded") === "true";
    logToggleBtn.setAttribute("aria-expanded", String(!isExpanded));
    logPanelEl.hidden = isExpanded;
    logToggleIcon.textContent = isExpanded ? "Show" : "Hide";
    if (!isExpanded) {
        logPanelEl.scrollTop = logPanelEl.scrollHeight;
    }
}

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

function stopSessionAfterFatalError(message) {
    isSessionActive = false;
    statusIndicator.textContent = "Error";
    statusIndicator.className = "";
    showError(message);
    cleanup();
}

// --- Cleanup ---
function cleanup() {
    Object.keys(AUDIO_SOURCES).forEach((sourceId) => stopAudioPipeline(sourceId));

    Object.values(mediaStreams).forEach((stream) => {
        stream.getTracks().forEach((track) => track.stop());
    });
    Object.keys(mediaStreams).forEach((key) => delete mediaStreams[key]);

    if (ws) {
        ws.close();
        ws = null;
    }
}

// --- Init ---
startBtn.onclick = () => startSession();
refreshMicsBtn.onclick = () => loadMicrophoneOptions();
phraseHistoryToggleBtn.onclick = () => togglePhraseHistory();
logToggleBtn.onclick = () => togglePipelineLog();
loadMicrophoneOptions();
