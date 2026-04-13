/**
 * CommCopilot Frontend
 * Handles: WebSocket connection, Web Speech API STT, silence detection,
 * filler word detection, phrase display.
 */

// Defaults — overridden by session_ready message from server
let PAUSE_THRESHOLD_MS = 1500;
let AUTO_DISMISS_MS = 5000;
let HESITATION_COOLDOWN_S = 5;
let MIN_SPEECH_CONFIDENCE = 0.6;

const WS_RECONNECT_DELAYS = [1000, 2000, 4000];

// Filler word detection (client-side — matches server FILLER_WORDS list)
const FILLER_WORDS = ['um', 'uh', 'er', 'ah', 'like', 'you know'];
const _fillerRe = new RegExp(
    '\\b(' + FILLER_WORDS.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')\\b',
    'i'
);

let ws = null;
let audioContext = null;
let analyser = null;
let mediaStream = null;
let recognition = null;
let silenceTimer = null;
let reconnectAttempt = 0;
let dismissTimer = null;
let isSessionActive = false;
let awaiting_phrases = false;  // client-side gate: suppress while server is thinking
let hesitationCooldownActive = false;

// --- Screens ---
const scenarioScreen = document.getElementById('scenario-screen');
const sessionScreen = document.getElementById('session-screen');
const recapScreen = document.getElementById('recap-screen');
const scenarioCards = document.getElementById('scenario-cards');
const scenarioLabel = document.getElementById('scenario-label');
const statusIndicator = document.getElementById('status-indicator');
const phraseContainer = document.getElementById('phrase-container');
const selectedPhraseEl = document.getElementById('selected-phrase');
const recapContent = document.getElementById('recap-content');
const errorBar = document.getElementById('error-bar');

// --- Init ---
async function init() {
    const resp = await fetch('/api/scenarios');
    const scenarios = await resp.json();
    for (const [key, val] of Object.entries(scenarios)) {
        const card = document.createElement('button');
        card.className = 'scenario-card';
        card.textContent = val.name;
        card.onclick = () => startSession(key, val.name);
        scenarioCards.appendChild(card);
    }
}

// --- Session ---
async function startSession(scenarioKey, scenarioName) {
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
        showError('Mic permission denied. Please allow microphone access.');
        return;
    }

    scenarioScreen.style.display = 'none';
    sessionScreen.style.display = 'block';
    scenarioLabel.textContent = scenarioName;
    isSessionActive = true;

    connectWebSocket(scenarioKey);
}

// --- WebSocket ---
function connectWebSocket(scenarioKey) {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        reconnectAttempt = 0;
        hideError();
        ws.send(JSON.stringify({ type: 'scenario', scenario: scenarioKey }));
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);

        if (msg.type === 'session_ready') {
            // Apply server-side config overrides
            if (msg.phrase_auto_dismiss_s) AUTO_DISMISS_MS = msg.phrase_auto_dismiss_s * 1000;
            if (msg.min_speech_confidence) MIN_SPEECH_CONFIDENCE = msg.min_speech_confidence;
            if (msg.hesitation_cooldown_s) HESITATION_COOLDOWN_S = msg.hesitation_cooldown_s;
            statusIndicator.textContent = 'Listening...';
            startSpeechRecognition();
            startSilenceDetection();

        } else if (msg.type === 'thinking') {
            statusIndicator.textContent = 'Thinking...';
            statusIndicator.className = 'processing';

        } else if (msg.type === 'phrases') {
            awaiting_phrases = false;
            showPhrases(msg.phrases);

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
                connectWebSocket(scenarioKey);
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

function triggerHesitation(trigger) {
    if (awaiting_phrases || hesitationCooldownActive) return;

    awaiting_phrases = true;
    hesitationCooldownActive = true;

    statusIndicator.textContent = 'Processing...';
    statusIndicator.className = 'processing';
    sendMessage({ type: 'hesitation', trigger });

    // Cooldown: block further hesitation triggers for HESITATION_COOLDOWN_S seconds
    setTimeout(() => {
        hesitationCooldownActive = false;
    }, HESITATION_COOLDOWN_S * 1000);
}

// --- Web Speech API ---
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
        for (let i = event.resultIndex; i < event.results.length; i++) {
            const result = event.results[i];
            const transcript = result[0].transcript;
            const confidence = result[0].confidence;

            if (result.isFinal) {
                // Filter low-confidence results
                if (confidence < MIN_SPEECH_CONFIDENCE && confidence > 0) return;

                sendMessage({ type: 'transcript', text: transcript });

                // Filler word detection
                if (_fillerRe.test(transcript)) {
                    triggerHesitation('filler');
                }
            }
        }
    };

    recognition.onend = () => {
        // Auto-restart if session still active (recognition stops after silence)
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

// --- Silence Detection (Web Audio API AnalyserNode) ---
function startSilenceDetection() {
    audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(mediaStream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);

    const dataArray = new Uint8Array(analyser.fftSize);
    let lastSoundTime = Date.now();

    function checkSilence() {
        if (!isSessionActive) return;

        analyser.getByteTimeDomainData(dataArray);

        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
            const val = (dataArray[i] - 128) / 128;
            sum += val * val;
        }
        const rms = Math.sqrt(sum / dataArray.length);

        if (rms > 0.01) {
            lastSoundTime = Date.now();
        }

        const silenceDuration = Date.now() - lastSoundTime;
        if (silenceDuration >= PAUSE_THRESHOLD_MS && !silenceTimer) {
            silenceTimer = true;
            triggerHesitation('pause');
            setTimeout(() => { silenceTimer = null; }, 1000);
        }

        requestAnimationFrame(checkSilence);
    }

    checkSilence();
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

// --- Recap ---
function showRecap(recap, phrasesUsed) {
    isSessionActive = false;
    sessionScreen.style.display = 'none';
    recapScreen.style.display = 'block';

    let html = `<p>${recap}</p>`;
    if (phrasesUsed && phrasesUsed.length > 0) {
        html += '<h3>Phrases you used:</h3><ul>';
        phrasesUsed.forEach(p => { html += `<li>${p}</li>`; });
        html += '</ul>';
    }
    recapContent.innerHTML = html;

    cleanup();
}

// --- End Session ---
document.getElementById('end-btn').onclick = () => {
    sendMessage({ type: 'end_session' });
};

document.getElementById('new-session-btn').onclick = () => {
    recapScreen.style.display = 'none';
    scenarioScreen.style.display = 'block';
};

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
    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }
    if (ws) {
        ws.close();
        ws = null;
    }
}

// --- Start ---
init();
