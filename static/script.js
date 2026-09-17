const chatBox = document.getElementById("chat-box");
const userInput = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");
const clearButton = document.getElementById("clear-button");
const statusText = document.getElementById("status-text");
const providerText = document.getElementById("provider-text");

const sourceBadge = document.getElementById("source-badge");
const confidenceBadge = document.getElementById("confidence-badge");
const docsBadge = document.getElementById("docs-badge");

const topicButtons = document.querySelectorAll(".topic-btn");
const activeBanner = document.getElementById("active-topic-banner");
const activeTopicLabel = document.getElementById("active-topic-label");
const sampleChipsArea = document.getElementById("sample-chips-area");
const sampleChipsLabel = document.getElementById("sample-chips-label");
const sampleChipsContainer = document.getElementById("sample-chips");

const voiceTools = document.getElementById("voice-tools");
const voiceListenButton = document.getElementById("voice-listen-button");
const voiceListenLabel = document.getElementById("voice-listen-label");
const voiceLanguageSelect = document.getElementById("voice-language-select");
const voiceReplyToggle = document.getElementById("voice-reply-toggle");
const voiceStopButton = document.getElementById("voice-stop-button");
const voiceStatus = document.getElementById("voice-status");
const voiceSupportBadge = document.getElementById("voice-support-badge");

let activeTopic = null;

// A static fallback page can point this bundle at the separately deployed
// Flask API. Normal Flask rendering leaves API_BASE empty and uses same-origin
// requests.
const API_BASE = String(window.API_BASE || "").replace(/\/+$/, "");
function apiUrl(path) {
  return `${API_BASE}${path}`;
}

// Browser-native speech keeps the default setup credential-free. The
// optional /api/voice-config endpoint lets a deployment disable voice or
// choose locales without coupling the frontend to a speech vendor.
const DEFAULT_VOICE_CONFIG = {
  enabled: true,
  mode: "browser",
  stt_enabled: true,
  tts_enabled: true,
  default_language: "en-US",
  languages: [
    { code: "en-US", label: "English (US)" },
    { code: "zh-CN", label: "普通话（简体中文）" }
  ],
  auto_send: false,
  max_utterance_chars: 1000
};

let voiceConfig = DEFAULT_VOICE_CONFIG;
let speechRecognition = null;
let isListening = false;
let lastFinalTranscript = "";
let recognitionError = false;

// ── BROWSER VOICE ──
function getSpeechRecognitionConstructor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function normalizeVoiceConfig(rawConfig) {
  const raw = rawConfig && typeof rawConfig === "object" ? rawConfig : {};
  const languageValues = Array.isArray(raw.languages)
    ? raw.languages
        .map((language) => {
          if (typeof language === "string") {
            return { code: language, label: language };
          }
          if (language && typeof language.code === "string") {
            return {
              code: language.code,
              label: language.label || language.code
            };
          }
          return null;
        })
        .filter(Boolean)
    : DEFAULT_VOICE_CONFIG.languages;

  const languages = languageValues.length
    ? languageValues
    : DEFAULT_VOICE_CONFIG.languages;
  const requestedDefault = String(
    raw.default_language || DEFAULT_VOICE_CONFIG.default_language
  );
  const selectedDefault =
    languages.find(
      (language) => language.code.toLowerCase() === requestedDefault.toLowerCase()
    ) || languages[0];
  const maxUtterance = Number(raw.max_utterance_chars);

  return {
    ...DEFAULT_VOICE_CONFIG,
    ...raw,
    languages,
    default_language: selectedDefault.code,
    max_utterance_chars:
      Number.isFinite(maxUtterance) && maxUtterance >= 100
        ? Math.min(maxUtterance, 10000)
        : DEFAULT_VOICE_CONFIG.max_utterance_chars
  };
}

function setVoiceStatus(message, isError = false) {
  if (!voiceStatus) return;
  voiceStatus.textContent = message;
  voiceStatus.classList.toggle("error", isError);
}

function setVoiceSupportBadge(message, state = "") {
  if (!voiceSupportBadge) return;
  voiceSupportBadge.textContent = message;
  voiceSupportBadge.classList.remove("ready", "warning", "disabled");
  if (state) voiceSupportBadge.classList.add(state);
}

function updateVoiceListenButton(listening) {
  if (!voiceListenButton || !voiceListenLabel) return;
  voiceListenButton.classList.toggle("listening", listening);
  voiceListenButton.setAttribute("aria-pressed", String(listening));
  voiceListenLabel.textContent = listening
    ? "Stop listening"
    : "Start voice input";
}

function populateVoiceLanguages() {
  if (!voiceLanguageSelect) return;
  voiceLanguageSelect.innerHTML = "";
  voiceConfig.languages.forEach((language) => {
    const option = document.createElement("option");
    option.value = language.code;
    option.textContent = language.label || language.code;
    voiceLanguageSelect.appendChild(option);
  });
  voiceLanguageSelect.value = voiceConfig.default_language;
}

function getBrowserVoice(languageCode) {
  if (!window.speechSynthesis || typeof window.speechSynthesis.getVoices !== "function") {
    return null;
  }
  const voices = window.speechSynthesis.getVoices();
  const requested = languageCode.toLowerCase();
  const languagePrefix = requested.split("-", 1)[0];
  return (
    voices.find((voice) => voice.lang.toLowerCase() === requested) ||
    voices.find((voice) => voice.lang.toLowerCase().startsWith(`${languagePrefix}-`)) ||
    null
  );
}

function stopVoiceOutput() {
  if (window.speechSynthesis && typeof window.speechSynthesis.cancel === "function") {
    window.speechSynthesis.cancel();
    if (voiceReplyToggle?.checked) setVoiceStatus("Voice reply stopped.");
  }
}

function speakResponse(responseText) {
  if (
    !voiceReplyToggle?.checked ||
    !voiceConfig.enabled ||
    !voiceConfig.tts_enabled ||
    !window.speechSynthesis ||
    typeof window.SpeechSynthesisUtterance !== "function"
  ) {
    return;
  }

  const text = String(responseText || "").trim();
  if (!text) return;

  // Avoid filling a browser speech queue when a response contains a very
  // large retrieved document or an accidental model dump.
  const maxChars = Math.max(100, Number(voiceConfig.max_utterance_chars) * 4);
  const utterance = new SpeechSynthesisUtterance(text.slice(0, maxChars));
  utterance.lang = voiceLanguageSelect?.value || voiceConfig.default_language;
  const browserVoice = getBrowserVoice(utterance.lang);
  if (browserVoice) utterance.voice = browserVoice;
  utterance.rate = 0.95;
  utterance.onstart = () => setVoiceStatus("Reading the reply aloud…");
  utterance.onend = () => setVoiceStatus("Reply finished.");
  utterance.onerror = () => setVoiceStatus("The browser could not read this reply.", true);

  stopVoiceOutput();
  window.speechSynthesis.speak(utterance);
}

function createSpeechRecognition() {
  const Recognition = getSpeechRecognitionConstructor();
  if (!Recognition) return null;

  const recognition = new Recognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    isListening = true;
    updateVoiceListenButton(true);
    setVoiceStatus("Listening… speak your question, then pause.");
  };

  recognition.onresult = (event) => {
    let interimTranscript = "";
    let newlyFinalized = "";

    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const transcript = event.results[index][0]?.transcript || "";
      if (event.results[index].isFinal) {
        newlyFinalized += transcript;
      } else {
        interimTranscript += transcript;
      }
    }

    if (newlyFinalized) {
      lastFinalTranscript = `${lastFinalTranscript} ${newlyFinalized}`.trim();
    }

    const combined = `${lastFinalTranscript} ${interimTranscript}`
      .trim()
      .slice(0, voiceConfig.max_utterance_chars);
    userInput.value = combined;
    if (combined) {
      setVoiceStatus(interimTranscript ? "Listening…" : "Transcript ready to send.");
    }
  };

  recognition.onerror = (event) => {
    recognitionError = true;
    isListening = false;
    updateVoiceListenButton(false);
    const errorMessages = {
      "not-allowed": "Microphone permission was denied. You can still type your question.",
      "service-not-allowed": "This browser does not allow speech recognition here.",
      "no-speech": "I did not hear speech. Press the microphone button to try again.",
      network: "The browser speech service is unavailable. You can still type your question."
    };
    setVoiceStatus(
      errorMessages[event.error] || "Voice input failed. You can still type your question.",
      true
    );
  };

  recognition.onend = () => {
    const transcript = lastFinalTranscript.trim();
    isListening = false;
    updateVoiceListenButton(false);

    if (recognitionError) {
      recognitionError = false;
      return;
    }

    if (!transcript) {
      setVoiceStatus("Voice input is off until you press the microphone button.");
      return;
    }

    userInput.value = transcript.slice(0, voiceConfig.max_utterance_chars);
    if (voiceConfig.auto_send) {
      setVoiceStatus("Sending your voice question…");
      sendMessage(userInput.value);
    } else {
      setVoiceStatus("Transcript ready. Review it, then press Send.");
      userInput.focus();
    }
  };

  return recognition;
}

function startVoiceInput() {
  if (!voiceListenButton || voiceListenButton.disabled) return;
  if (!speechRecognition) speechRecognition = createSpeechRecognition();
  if (!speechRecognition) {
    setVoiceStatus("Voice input is not supported in this browser.", true);
    return;
  }

  lastFinalTranscript = userInput.value.trim();
  recognitionError = false;
  speechRecognition.lang = voiceLanguageSelect?.value || voiceConfig.default_language;
  try {
    speechRecognition.start();
  } catch (error) {
    // Calling start twice raises InvalidStateError in Chromium. The UI state
    // remains authoritative and the next click will stop/retry cleanly.
    if (error?.name !== "InvalidStateError") {
      setVoiceStatus("Could not start voice input. Please try again.", true);
    }
  }
}

function stopVoiceInput() {
  if (speechRecognition && isListening) speechRecognition.stop();
  isListening = false;
  updateVoiceListenButton(false);
}

function initializeVoiceControls() {
  if (!voiceTools) return;
  if (!voiceConfig.enabled || voiceConfig.mode === "disabled") {
    voiceTools.classList.add("hidden");
    return;
  }

  populateVoiceLanguages();
  const sttSupported = Boolean(getSpeechRecognitionConstructor());
  const ttsSupported = Boolean(
    window.speechSynthesis && typeof window.SpeechSynthesisUtterance === "function"
  );
  const sttReady = voiceConfig.stt_enabled && sttSupported;
  const ttsReady = voiceConfig.tts_enabled && ttsSupported;

  voiceListenButton.disabled = !sttReady;
  voiceReplyToggle.disabled = !ttsReady;
  voiceStopButton.disabled = !ttsReady;

  if (sttReady && ttsReady) {
    setVoiceSupportBadge("Browser voice ready", "ready");
  } else if (sttReady || ttsReady) {
    setVoiceSupportBadge("Partial browser support", "warning");
  } else {
    setVoiceSupportBadge("Text-only browser", "warning");
    setVoiceStatus(
      "This browser does not expose speech controls. You can still use text chat."
    );
  }

  if (!sttReady) {
    voiceListenButton.title = "Speech recognition is unavailable in this browser";
  }
  if (!ttsReady) {
    voiceReplyToggle.title = "Speech synthesis is unavailable in this browser";
  }
}

async function loadVoiceConfig() {
  try {
    const response = await fetch(apiUrl("/api/voice-config"), {
      headers: { Accept: "application/json" }
    });
    if (response.ok) {
      voiceConfig = normalizeVoiceConfig(await response.json());
    } else {
      voiceConfig = normalizeVoiceConfig(DEFAULT_VOICE_CONFIG);
    }
  } catch (error) {
    // Older deployments do not have the optional route. Browser defaults are
    // safe and preserve voice functionality while the app is upgraded.
    voiceConfig = normalizeVoiceConfig(DEFAULT_VOICE_CONFIG);
  }
  initializeVoiceControls();
}

// ── TOPIC SELECTOR ──
topicButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    topicButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");

    activeTopic = btn.dataset.topic;
    activeTopicLabel.textContent = `Topic: ${activeTopic}`;
    activeBanner.classList.remove("hidden");

    // Show sample chips for this topic
    const samples = JSON.parse(btn.dataset.samples || "[]");
    showSampleChips(activeTopic, samples);

    // Send silent priming message to backend
    const hint = btn.dataset.hint;
    if (hint) {
      sendMessage(hint, true);
    }
  });
});

function showSampleChips(topic, samples) {
  sampleChipsLabel.textContent = `Try asking about ${topic}:`;
  sampleChipsContainer.innerHTML = "";

  samples.forEach((q) => {
    const chip = document.createElement("button");
    chip.classList.add("sample-chip");
    chip.textContent = q;
    chip.addEventListener("click", () => {
      sendMessage(q);
    });
    sampleChipsContainer.appendChild(chip);
  });

  sampleChipsArea.classList.remove("hidden");
}

// ── MESSAGES ──
function addMessage(text, className, label) {
  const wrapper = document.createElement("div");
  wrapper.classList.add("message", className);

  const labelDiv = document.createElement("div");
  labelDiv.classList.add("message-label");
  labelDiv.textContent = label;

  const textDiv = document.createElement("div");
  textDiv.textContent = text;

  wrapper.appendChild(labelDiv);
  wrapper.appendChild(textDiv);

  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function showTypingIndicator() {
  const wrapper = document.createElement("div");
  wrapper.classList.add("message", "bot-message");
  wrapper.id = "typing-indicator";

  const labelDiv = document.createElement("div");
  labelDiv.classList.add("message-label");
  labelDiv.textContent = "ISDA Bot";

  const textDiv = document.createElement("div");
  textDiv.textContent = "Thinking...";

  wrapper.appendChild(labelDiv);
  wrapper.appendChild(textDiv);

  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function removeTypingIndicator() {
  const typing = document.getElementById("typing-indicator");
  if (typing) typing.remove();
}

function updateMeta(info = {}) {
  if (info.source) {
    sourceBadge.textContent = `Source: ${info.source}`;
    sourceBadge.classList.remove("hidden");
  } else {
    sourceBadge.classList.add("hidden");
  }

  if (info.confidence !== undefined && info.confidence !== null) {
    confidenceBadge.textContent = `Confidence: ${Number(info.confidence).toFixed(2)}`;
    confidenceBadge.classList.remove("hidden");
  } else {
    confidenceBadge.classList.add("hidden");
  }

  if (info.retrieved_docs !== undefined && info.retrieved_docs !== null) {
    docsBadge.textContent = `Retrieved Docs: ${info.retrieved_docs}`;
    docsBadge.classList.remove("hidden");
  } else {
    docsBadge.classList.add("hidden");
  }
}

// ── SEND MESSAGE ──
async function sendMessage(prefilledMessage = null, silent = false) {
  const message = (prefilledMessage || userInput.value).trim();
  if (!message) return;

  if (!silent) {
    addMessage(message, "user-message", "You");
  }

  userInput.value = "";
  showTypingIndicator();
  updateMeta({});

  try {
    const response = await fetch(apiUrl("/api/chat"), {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message })
    });

    const data = await response.json();
    removeTypingIndicator();

    if (!response.ok) {
      addMessage(data.error || "An error occurred while contacting the chatbot.", "bot-message", "ISDA Bot");
      return;
    }

    if (!silent) {
      const responseText =
        data.response || "I couldn't find an answer for that question.";
      addMessage(responseText, "bot-message", "ISDA Bot");
      updateMeta({
        source: data.source,
        confidence: data.confidence,
        retrieved_docs: data.retrieved_docs
      });
      speakResponse(responseText);
    } else {
      addMessage(
        `Got it! I'm now focused on ${activeTopic}. Click a sample question above or type your own below!`,
        "bot-message",
        "ISDA Bot"
      );
    }

  } catch (error) {
    removeTypingIndicator();
    if (!silent) {
      addMessage(
        "Sorry, I couldn't connect to the chatbot backend right now.",
        "bot-message",
        "ISDA Bot"
      );
    }
    console.error("Chat error:", error);
  }
}

// ── STATUS ──
async function loadStatus() {
  try {
    const response = await fetch(apiUrl("/api/status"));
    const data = await response.json();

    if (data.status === "ready") {
      statusText.textContent = `Status: ${data.status}`;
      providerText.textContent = `Providers available: ${data.available_providers?.join(", ") || "None"} | Hardcoded questions: ${data.hardcoded_questions ?? 0}`;
    } else {
      statusText.textContent = "Status: error";
      providerText.textContent = data.error || "Could not load status.";
    }
  } catch (error) {
    statusText.textContent = "Status: unavailable";
    providerText.textContent = "Could not connect to backend status endpoint.";
  }
}

// ── CLEAR ──
async function clearHistory() {
  try {
    await fetch(apiUrl("/api/clear-history"), { method: "POST", credentials: "include" });
  } catch (error) {
    console.error("Clear history error:", error);
  }
}

// ── EVENT LISTENERS ──
sendButton.addEventListener("click", () => sendMessage());

userInput.addEventListener("keypress", (event) => {
  if (event.key === "Enter") sendMessage();
});

clearButton.addEventListener("click", async () => {
  chatBox.innerHTML = `
    <div class="message bot-message">
      <div class="message-label">ISDA Bot</div>
      <div>Hi! I'm the ISDA Advising Support Chatbot. I can help with general questions about advising, degree requirements, course planning, and registration.<br><br>Select a topic on the left to get focused answers and sample questions!</div>
    </div>
  `;
  topicButtons.forEach((b) => b.classList.remove("active"));
  activeBanner.classList.add("hidden");
  sampleChipsArea.classList.add("hidden");
  sampleChipsContainer.innerHTML = "";
  activeTopic = null;
  updateMeta({});
  await clearHistory();
});

if (voiceListenButton) {
  voiceListenButton.addEventListener("click", () => {
    if (isListening) {
      stopVoiceInput();
    } else {
      startVoiceInput();
    }
  });
}

if (voiceStopButton) {
  voiceStopButton.addEventListener("click", () => {
    stopVoiceOutput();
    stopVoiceInput();
    setVoiceStatus("Voice output stopped. Voice input is ready when you are.");
  });
}

if (voiceReplyToggle) {
  voiceReplyToggle.addEventListener("change", () => {
    if (!voiceReplyToggle.checked) stopVoiceOutput();
  });
}

if (voiceLanguageSelect) {
  voiceLanguageSelect.addEventListener("change", () => {
    if (isListening) stopVoiceInput();
    const language = voiceLanguageSelect.value;
    setVoiceStatus(`Voice language set to ${language}.`);
  });
}

loadStatus();
loadVoiceConfig();
