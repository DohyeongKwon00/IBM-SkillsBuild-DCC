/*
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
let activeSuggestionGroup = null;

// Session metrics for the recap screen
let sessionMetrics = {
    startedAt: null,
    turns: 0,
    suggestionsUsed: 0,
    hesitations: 0,
};

// --- DOM refs ---
const homeScreen = document.getElementById("home-screen");
const sessionScreen = document.getElementById("session-screen");
const recapScreen = document.getElementById("recap-screen");

const startBtn = document.getElementById("start-btn");
const configureMicsBtn = document.getElementById("configure-mics-btn");
const modalStartBtn = document.getElementById("modal-start-btn");
const refreshMicsBtn = document.getElementById("refresh-mics-btn");
const speakerAMicSelect = document.getElementById("speaker-a-mic-select");
const speakerBMicSelect = document.getElementById("speaker-b-mic-select");

const micModal = document.getElementById("mic-modal");

const statusIndicator = document.getElementById("status-indicator");
const statusText = statusIndicator.querySelector(".status-text");
const errorBar = document.getElementById("error-bar");
const transcriptEl = document.getElementById("transcript-final");

const recapSummaryEl = document.getElementById("recap-summary");
const recapPhrasesWrap = document.getElementById("recap-phrases");
const recapPhrasesList = document.getElementById("recap-phrases-list");
const restartBtn = document.getElementById("restart-btn");
const homeBtn = document.getElementById("home-btn");

// --- Modal control ---
function openMicModal() {
    micModal.style.display = "flex";
    micModal.setAttribute("aria-hidden", "false");
}

function closeMicModal() {
    micModal.style.display = "none";
    micModal.setAttribute("aria-hidden", "true");
}

micModal.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", closeMicModal);
});

document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && micModal.style.display !== "none") {
        closeMicModal();
    }
});

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
        showError("Mic permission denied. Please allow microphone access, then refresh devices.");
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

    closeMicModal();
    homeScreen.style.display = "none";
    recapScreen.style.display = "none";
    sessionScreen.style.display = "flex";
    isSessionActive = true;

    sessionMetrics = {
        startedAt: Date.now(),
        turns: 0,
        suggestionsUsed: 0,
        hesitations: 0,
    };
    transcriptEl.innerHTML = "";

    setStatus("listening", "Listening");
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
            setStatus("listening", "Listening");
            startAudioStreaming("speakerA");
            startAudioStreaming("speakerB");

        } else if (msg.type === "thinking") {
            setStatus("thinking", "Thinking");

        } else if (msg.type === "idle") {
            setStatus("listening", "Listening");

        } else if (msg.type === "phrases") {
            sessionMetrics.hesitations += 1;
            appendHesitationBlock(msg.phrases);
            setStatus("listening", "Listening");

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

// --- Status pill ---
function setStatus(kind, text) {
    statusIndicator.classList.remove("status-listening", "status-thinking", "status-ended");
    statusIndicator.classList.add(`status-${kind}`);
    statusText.textContent = text;
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

// --- Phrase matching ---
function selectPhrase(phrase) {
    sendMessage({ type: "phrase_selected", phrase });
}

function normalizePhrase(text) {
    return text
        .toLowerCase()
        .replace(/[^\w\s]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}

function findMatchingActivePhrase(text) {
    if (!activeSuggestionGroup || activeSuggestionGroup.selected) return null;

    const normalizedText = normalizePhrase(text);
    if (!normalizedText) return null;

    return activeSuggestionGroup.phrases.find((phrase) => {
        const normalizedPhrase = normalizePhrase(phrase);
        if (!normalizedPhrase) return false;
        const phraseWords = normalizedPhrase.split(" ");
        const textWords = normalizedText.split(" ");
        const enoughPhraseSpoken = textWords.length >= Math.ceil(phraseWords.length * 0.7);

        return normalizedText === normalizedPhrase ||
            normalizedText.includes(normalizedPhrase) ||
            (enoughPhraseSpoken && normalizedPhrase.includes(normalizedText));
    });
}

function markSuggestionGroupSelected(phrase, group = activeSuggestionGroup) {
    if (!group || group.selected) return false;

    group.selected = true;
    sessionMetrics.suggestionsUsed += 1;
    selectPhrase(phrase);
    group.cards.querySelectorAll(".chat-phrase-card").forEach((card) => {
        card.classList.add("dismissed");
    });

    const block = group.block;
    if (block) {
        const head = block.querySelector(".hesitation-head");
        if (head) {
            const titleSub = head.querySelector(".hesitation-title-sub");
            if (titleSub) titleSub.textContent = "Selected";
        }
    }
    return true;
}

// --- Recap ---
function showRecap(recapText, phrasesUsed) {
    isSessionActive = false;
    setStatus("ended", "Ended");

    const elapsedSec = sessionMetrics.startedAt
        ? Math.floor((Date.now() - sessionMetrics.startedAt) / 1000)
        : 0;

    document.getElementById("stat-turns").textContent = String(sessionMetrics.turns);
    document.getElementById("stat-suggestions").textContent = String(sessionMetrics.suggestionsUsed);
    document.getElementById("stat-hesitations").textContent = String(sessionMetrics.hesitations);
    setRecapDuration(elapsedSec);

    if (recapText) {
        recapSummaryEl.textContent = recapText;
    } else {
        recapSummaryEl.textContent = "";
    }

    if (phrasesUsed && phrasesUsed.length > 0) {
        recapPhrasesList.innerHTML = "";
        phrasesUsed.forEach((p) => {
            const li = document.createElement("li");
            li.textContent = p;
            recapPhrasesList.appendChild(li);
        });
        recapPhrasesWrap.style.display = "block";
    } else {
        recapPhrasesWrap.style.display = "none";
    }

    sessionScreen.style.display = "none";
    homeScreen.style.display = "none";
    recapScreen.style.display = "flex";

    cleanup();
}

function formatDuration(totalSeconds) {
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
}

function setRecapDuration(totalSeconds) {
    const m = Math.floor(totalSeconds / 60);
    const s = String(totalSeconds % 60).padStart(2, "0");
    const el = document.getElementById("recap-duration");
    if (el) {
        el.innerHTML = `${m}<span class="colon"> : </span>${s}`;
    }
}

// --- End session ---
document.getElementById("end-btn").onclick = () => {
    sendMessage({ type: "end_session" });
};

// --- Transcript display (chat bubble layout) ---
function appendTranscriptLine(text) {
    const match = text.match(/^\[(.+?)\]:\s*(.+)$/);
    const wrapper = document.createElement("div");

    if (match) {
        const speakerLabel = match[1];
        const messageText = match[2];
        const isUserSpeaker = speakerLabel === "Speaker A";

        wrapper.className = `chat-message ${isUserSpeaker ? "speaker-a" : "speaker-b"}`;

        const labelEl = document.createElement("div");
        labelEl.className = "chat-speaker-label";
        labelEl.textContent = speakerLabel;

        const row = document.createElement("div");
        row.className = "chat-message-row";

        const avatar = document.createElement("div");
        avatar.className = `chat-avatar ${isUserSpeaker ? "avatar-a-chat" : "avatar-b-chat"}`;
        avatar.textContent = isUserSpeaker ? "A" : "B";

        const bubble = document.createElement("div");
        bubble.className = "chat-bubble";
        bubble.textContent = messageText;

        row.appendChild(avatar);
        row.appendChild(bubble);

        wrapper.appendChild(labelEl);
        wrapper.appendChild(row);

        sessionMetrics.turns += 1;

        if (isUserSpeaker) {
            const matchedPhrase = findMatchingActivePhrase(messageText);
            if (matchedPhrase) {
                markSuggestionGroupSelected(matchedPhrase);
                bubble.classList.add("phrase-selected");
                const tag = document.createElement("div");
                tag.className = "copilot-tag";
                tag.textContent = "Copilot";
                bubble.parentElement.parentElement.insertBefore(tag, bubble.parentElement);
            }
        }
    } else {
        wrapper.className = "chat-message";
        wrapper.textContent = text;
    }

    transcriptEl.appendChild(wrapper);
    scrollTranscriptToEnd();
}

function appendHesitationBlock(phrases) {
    const block = document.createElement("div");
    block.className = "hesitation-block";

    const head = document.createElement("div");
    head.className = "hesitation-head";

    const title = document.createElement("div");
    title.className = "hesitation-title";
    title.innerHTML = `Hesitation detected <span class="hesitation-title-sub">· Suggested responses</span>`;

    const closeBtn = document.createElement("button");
    closeBtn.className = "hesitation-close";
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Dismiss suggestions");
    closeBtn.textContent = "×";
    closeBtn.onclick = () => {
        block.style.display = "none";
        if (activeSuggestionGroup && activeSuggestionGroup.block === block) {
            activeSuggestionGroup.selected = true;
        }
    };

    head.appendChild(title);
    head.appendChild(closeBtn);

    const cards = document.createElement("div");
    cards.className = "chat-phrase-cards";

    const suggestionGroup = { phrases, cards, block, selected: false };

    phrases.forEach((phrase, index) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "chat-phrase-card";
        card.dataset.phrase = phrase;

        const num = document.createElement("span");
        num.className = "phrase-num";
        num.textContent = String(index + 1).padStart(2, "0");

        const textSpan = document.createElement("span");
        textSpan.className = "phrase-text";
        textSpan.textContent = phrase;

        const arrow = document.createElement("span");
        arrow.className = "phrase-arrow";
        arrow.textContent = "→";

        card.appendChild(num);
        card.appendChild(textSpan);
        card.appendChild(arrow);

        card.onclick = () => {
            if (markSuggestionGroupSelected(phrase, suggestionGroup)) {
                appendSelectedPhraseAsChatBubble(phrase);
            }
        };
        cards.appendChild(card);
    });

    block.appendChild(head);
    block.appendChild(cards);
    activeSuggestionGroup = suggestionGroup;

    transcriptEl.appendChild(block);
    scrollTranscriptToEnd();
}

function appendSelectedPhraseAsChatBubble(phrase) {
    const wrapper = document.createElement("div");
    wrapper.className = "chat-message speaker-a";

    const labelEl = document.createElement("div");
    labelEl.className = "chat-speaker-label";
    labelEl.textContent = "Speaker A";

    const row = document.createElement("div");
    row.className = "chat-message-row";

    const avatar = document.createElement("div");
    avatar.className = "chat-avatar avatar-a-chat";
    avatar.textContent = "A";

    const bubbleWrap = document.createElement("div");

    const tag = document.createElement("div");
    tag.className = "copilot-tag";
    tag.textContent = "Copilot";

    const bubble = document.createElement("div");
    bubble.className = "chat-bubble phrase-selected";
    bubble.textContent = phrase;

    bubbleWrap.appendChild(tag);
    bubbleWrap.appendChild(bubble);

    row.appendChild(avatar);
    row.appendChild(bubbleWrap);

    wrapper.appendChild(labelEl);
    wrapper.appendChild(row);
    transcriptEl.appendChild(wrapper);
    scrollTranscriptToEnd();
}

function scrollTranscriptToEnd() {
    const parent = document.getElementById("live-transcript");
    parent.scrollTop = parent.scrollHeight;
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
    setStatus("ended", "Error");
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
configureMicsBtn.onclick = () => openMicModal();
modalStartBtn.onclick = () => startSession();
refreshMicsBtn.onclick = () => loadMicrophoneOptions();
restartBtn.onclick = () => {
    recapScreen.style.display = "none";
    homeScreen.style.display = "flex";
    openMicModal();
};
homeBtn.onclick = () => {
    recapScreen.style.display = "none";
    homeScreen.style.display = "flex";
};

loadMicrophoneOptions();
