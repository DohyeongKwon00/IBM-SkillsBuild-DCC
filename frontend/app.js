/**
 * CommCopilot Frontend (listener mode)
 *
 * In listener mode the browser no longer decides when to trigger the pipeline.
 * It just transcribes speech via the Web Speech API and streams each final
 * transcript chunk to the server. The server forwards chunks to ContextAgent,
 * which decides on its own whether to stay silent or return phrase suggestions.
 */

// Defaults — overridden by session_ready message from server
let AUTO_DISMISS_MS = 5000;
let MIN_SPEECH_CONFIDENCE = 0.6;

const WS_RECONNECT_DELAYS = [1000, 2000, 4000];

let ws = null;
let mediaStream = null;
let recognition = null;
let reconnectAttempt = 0;
let dismissTimer = null;
let isSessionActive = false;

// --- Screens ---
const startScreen = document.getElementById('start-screen');
const sessionScreen = document.getElementById('session-screen');
const startBtn = document.getElementById('start-btn');
const statusIndicator = document.getElementById('status-indicator');
const phraseContainer = document.getElementById('phrase-container');
const selectedPhraseEl = document.getElementById('selected-phrase');
const recapContent = document.getElementById('recap-content');
const errorBar = document.getElementById('error-bar');
const inlineRecapEl = document.getElementById('inline-recap');
const transcriptFinalEl = document.getElementById('transcript-final');
const transcriptInterimEl = document.getElementById('transcript-interim');
const logPanelEl = document.getElementById('log-panel');

// --- Session ---
async function startSession() {
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        showError('Mic permission denied. Please allow microphone access.');
        return;
    }

    startScreen.style.display = 'none';
    sessionScreen.style.display = 'block';
    isSessionActive = true;

    connectWebSocket();
}

// --- WebSocket ---
function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        reconnectAttempt = 0;
        hideError();
        ws.send(JSON.stringify({ type: 'start' }));
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.type === 'session_ready') {
            if (msg.phrase_auto_dismiss_s) AUTO_DISMISS_MS = msg.phrase_auto_dismiss_s * 1000;
            if (msg.min_speech_confidence) MIN_SPEECH_CONFIDENCE = msg.min_speech_confidence;
            statusIndicator.textContent = 'Listening...';
            startSpeechRecognition();

        } else if (msg.type === 'thinking') {
            statusIndicator.textContent = 'Listener thinking...';
            statusIndicator.className = 'processing';

        } else if (msg.type === 'idle') {
            statusIndicator.textContent = 'Listening...';
            statusIndicator.className = '';

        } else if (msg.type === 'phrases') {
            showPhrases(msg.phrases);

        } else if (msg.type === 'log') {
            appendLog(msg);

        } else if (msg.type === 'recap') {
            showRecap(msg.recap, msg.phrases_used);
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
            showError('Connection lost. Please refresh the page.');
        }
    };

    ws.onerror = () => {};
}

function sendMessage(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
    }
}

// --- Web Speech API (STT only — no triggers) ---
function startSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        showError('Speech recognition not supported. Use Chrome.');
        return;
    }

    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onresult = (event) => {
        let interim = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
            const result = event.results[i];
            const transcript = result[0].transcript;
            const confidence = result[0].confidence;

            if (result.isFinal) {
                if (confidence < MIN_SPEECH_CONFIDENCE && confidence > 0) continue;
                sendMessage({ type: 'transcript', text: transcript });
                appendFinalTranscript(transcript);
            } else {
                interim += transcript;
            }
        }
        transcriptInterimEl.textContent = interim;
    };

    recognition.onend = () => {
        if (isSessionActive) {
            try { recognition.start(); } catch (e) {}
        }
    };

    recognition.onerror = (e) => {
        if (e.error === 'not-allowed') {
            showError('Microphone access denied.');
        }
    };

    try {
        recognition.start();
    } catch (e) {}
}

// --- Phrase Display ---
function showPhrases(phrases) {
    phraseContainer.innerHTML = '';
    statusIndicator.textContent = 'Listening...';
    statusIndicator.className = '';

    phrases.forEach(phrase => {
        const card = document.createElement('div');
        card.className = 'phrase-card';
        card.textContent = phrase;
        card.onclick = () => selectPhrase(phrase);
        phraseContainer.appendChild(card);
    });

    if (dismissTimer) clearTimeout(dismissTimer);
    dismissTimer = setTimeout(() => {
        phraseContainer.innerHTML = '';
    }, AUTO_DISMISS_MS);
}

function selectPhrase(phrase) {
    if (dismissTimer) clearTimeout(dismissTimer);
    phraseContainer.innerHTML = '';
    selectedPhraseEl.textContent = phrase;
    selectedPhraseEl.style.display = 'block';
    sendMessage({ type: 'phrase_selected', phrase });

    setTimeout(() => {
        selectedPhraseEl.style.display = 'none';
    }, 4000);
}

// --- Recap (inline) ---
function showRecap(recap, phrasesUsed) {
    isSessionActive = false;

    statusIndicator.textContent = 'Ended';
    statusIndicator.className = '';
    phraseContainer.innerHTML = '';
    if (dismissTimer) clearTimeout(dismissTimer);

    let html = `<p>${recap}</p>`;
    if (phrasesUsed && phrasesUsed.length > 0) {
        html += '<h4>Phrases you used:</h4><ul>';
        phrasesUsed.forEach(p => { html += `<li>${p}</li>`; });
        html += '</ul>';
    }
    html += '<button id="restart-btn">New Session</button>';
    inlineRecapEl.innerHTML = html;
    inlineRecapEl.style.display = 'block';
    document.getElementById('restart-btn').onclick = () => location.reload();

    const endBtn = document.getElementById('end-btn');
    if (endBtn) endBtn.disabled = true;

    cleanup();
}

// --- End Session ---
document.getElementById('end-btn').onclick = () => {
    sendMessage({ type: 'end_session' });
};

// --- Live Transcript ---
function appendFinalTranscript(text) {
    const span = document.createElement('span');
    span.textContent = text.trim() + ' ';
    transcriptFinalEl.appendChild(span);
    const parent = document.getElementById('live-transcript');
    parent.scrollTop = parent.scrollHeight;
}

// --- Pipeline Log ---
function appendLog(msg) {
    const entry = document.createElement('div');
    entry.className = 'log-entry';

    const now = new Date();
    const time = now.toTimeString().slice(0, 8);
    const stage = msg.stage || 'event';
    const status = msg.status || '';
    const detail = msg.detail || '';

    const header = document.createElement('div');
    header.className = 'log-header';
    header.innerHTML =
        `<span class="log-time">${time}</span>` +
        `<span class="log-stage ${stage}">[${stage}]</span>` +
        `<span class="log-status">${status}</span>` +
        `<span class="log-detail"></span>`;
    header.querySelector('.log-detail').textContent = detail;
    entry.appendChild(header);

    const addBlock = (label, value) => {
        if (value === undefined || value === null || value === '') return;
        const block = document.createElement('div');
        block.className = 'log-block';
        const lab = document.createElement('span');
        lab.className = 'log-label';
        lab.textContent = label + ': ';
        const body = document.createElement('span');
        body.className = 'log-body';
        body.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
        block.appendChild(lab);
        block.appendChild(body);
        entry.appendChild(block);
    };

    addBlock('prompt', msg.prompt);
    addBlock('output', msg.output);
    addBlock('parsed', msg.phrases);

    logPanelEl.appendChild(entry);
    logPanelEl.scrollTop = logPanelEl.scrollHeight;
}

// --- Error ---
function showError(msg) {
    errorBar.textContent = msg;
    errorBar.style.display = 'block';
}
function hideError() {
    errorBar.style.display = 'none';
}

// --- Cleanup ---
function cleanup() {
    if (recognition) {
        recognition.stop();
        recognition = null;
    }
    if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
    }
    if (ws) {
        ws.close();
        ws = null;
    }
}

// --- Start ---
startBtn.onclick = () => startSession();
