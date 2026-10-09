const chatBox = document.getElementById("chat-box");
const userInput = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");
const clearButton = document.getElementById("clear-button");
const statusText = document.getElementById("status-text");
const providerText = document.getElementById("provider-text");

const sourceBadge = document.getElementById("source-badge");
const docsBadge = document.getElementById("docs-badge");

const topicButtons = document.querySelectorAll(".topic-btn");
const activeBanner = document.getElementById("active-topic-banner");
const activeTopicLabel = document.getElementById("active-topic-label");
const sampleChipsArea = document.getElementById("sample-chips-area");
const sampleChipsLabel = document.getElementById("sample-chips-label");
const sampleChipsContainer = document.getElementById("sample-chips");
const emptyState = document.getElementById("empty-state");
const chatTabs = document.getElementById("chat-tabs");

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

  voiceListenButton.title = voiceListenLabel.textContent;
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
  // The GitHub Pages copy has none of the voice elements. Stop before
  // touching them. When the elements exist, a deployment that turns voice
  // off still disables the composer microphone.
  if (!voiceTools) return;
  if (!voiceConfig.enabled || voiceConfig.mode === "disabled") {
    voiceTools.classList.add("hidden");
    voiceListenButton.disabled = true;
    voiceListenButton.title = "Voice input is disabled for this deployment";
    setVoiceStatus("Type your question to get started. Voice input is disabled.");
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
      setServiceStatus("ready");
    } else {
      voiceConfig = normalizeVoiceConfig(DEFAULT_VOICE_CONFIG);
      setServiceStatus("error");
    }
  } catch (error) {
    // Older deployments do not have the optional route. Browser defaults are
    // safe and preserve voice functionality while the app is upgraded.
    voiceConfig = normalizeVoiceConfig(DEFAULT_VOICE_CONFIG);
    setServiceStatus("unavailable");
  }
  initializeVoiceControls();
}

// ── TOPIC SELECTOR ──
topicButtons.forEach((btn) => {
  btn.setAttribute("aria-pressed", "false");

  btn.addEventListener("click", () => {
    topicButtons.forEach((b) => {
      b.classList.remove("active");
      b.setAttribute("aria-pressed", "false");
    });

    btn.classList.add("active");
    btn.setAttribute("aria-pressed", "true");

    activeTopic = btn.dataset.topic;
    activeTopicLabel.textContent = `Topic: ${activeTopic}`;
    activeBanner.classList.remove("hidden");

    const samples = JSON.parse(btn.dataset.samples || "[]");
    showSampleChips(activeTopic, samples);

    // Selecting a topic only focuses the chips. The hint is not a question the
    // student asked, so it must not spend a completion. Sample chips below
    // call sendMessage and show the answer.
    addMessage(
      `Got it! I'm now focused on ${activeTopic}. Choose a sample question below or type your own!`,
      "bot-message",
      "ISDA Bot"
    );
  });
});

function showSampleChips(topic, samples) {
  sampleChipsLabel.textContent = `Try asking about ${topic}:`;
  sampleChipsContainer.innerHTML = "";

  samples.forEach((q) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.classList.add("sample-chip");
    chip.textContent = q;
    chip.addEventListener("click", () => {
      sendMessage(q);
    });
    sampleChipsContainer.appendChild(chip);
  });

  sampleChipsArea.classList.remove("hidden");
}

// Welcome suggestions sit outside the transcript. Hide them once a message
// exists, and bring them back when New chat clears the conversation.
function hideEmptyState() {
  if (!emptyState || !chatBox) return;
  const active = document.activeElement;
  const focusedInWelcome = Boolean(
    active &&
    typeof emptyState.contains === "function" &&
    emptyState.contains(active)
  );
  emptyState.classList.add("hidden");
  chatBox.classList.remove("is-empty");
  if (focusedInWelcome && userInput && typeof userInput.focus === "function") {
    userInput.focus();
  }
}

function showEmptyState() {
  if (!emptyState || !chatBox) return;
  if (typeof chatBox.replaceChildren === "function") {
    chatBox.replaceChildren();
  } else {
    chatBox.innerHTML = "";
  }
  chatBox.classList.add("is-empty");
  emptyState.classList.remove("hidden");
}

function hideSampleChips() {
  sampleChipsArea?.classList.add("hidden");
}

// ── MESSAGES ──
// Transcript used when this page has no conversation tabs (the static docs
// fallback). Tabbed chats keep the same text in localStorage.
let plainTranscript = [];

function renderMessageText(container, value, linkify = false) {
  const text = String(value ?? "");
  // User messages stay plain text. Only bot replies opt into Markdown.
  if (!linkify) {
    container.textContent = text;
    return;
  }

  // markdown.js escapes the string first, then adds a fixed set of tags.
  // If that file did not load, show the reply as text rather than HTML.
  if (typeof renderBotMarkdown !== "function") {
    container.textContent = text;
    return;
  }

  try {
    container.innerHTML = renderBotMarkdown(text);
  } catch (_error) {
    container.textContent = text;
  }
}

function addMessage(text, className, label, options) {
  const skipRecord = Boolean(options && options.skipRecord);
  hideEmptyState();
  if (!skipRecord && tabsEnabled()) {
    const current = getActiveConversation();
    if (current) {
      rememberMessage(current.id, {
        role: className === "user-message" ? "user" : "bot",
        text: text,
        provider: options && options.report ? options.report.provider : null,
        model: options && options.report ? options.report.model : null
      });
    }
  }
  if (!tabsEnabled() && !skipRecord) {
    plainTranscript.push({
      role: className === "user-message" ? "user" : "bot",
      text: String(text ?? ""),
      provider: options && options.report ? options.report.provider : null,
      model: options && options.report ? options.report.model : null
    });
    if (plainTranscript.length > MAX_STORED_MESSAGES) {
      plainTranscript.splice(0, plainTranscript.length - MAX_STORED_MESSAGES);
    }
  }
  const wrapper = document.createElement("div");
  wrapper.classList.add("message", className);

  const labelDiv = document.createElement("div");
  labelDiv.classList.add("message-label");
  labelDiv.textContent = label;

  const textDiv = document.createElement("div");
  textDiv.classList.add("message-body");
  renderMessageText(textDiv, text, className === "bot-message");

  wrapper.appendChild(labelDiv);
  wrapper.appendChild(textDiv);
  if (options && options.reportable && options.report) {
    attachReportControl(wrapper, options.report);
  }

  chatBox.appendChild(wrapper);
  chatBox.scrollTop = chatBox.scrollHeight;
  return wrapper;
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

  if (info.retrieved_docs !== undefined && info.retrieved_docs !== null) {
    docsBadge.textContent = `Retrieved Docs: ${info.retrieved_docs}`;
    docsBadge.classList.remove("hidden");
  } else {
    docsBadge.classList.add("hidden");
  }
}

// Gradually reveal completed answers.
// Every request and animation belongs to its original conversation.
const replyOperations = new Map();
const conversationDrafts = new Map();

const replyStatus = document.getElementById("reply-status");
const replyAction = document.getElementById("reply-action");

function activeReplyKey() {
  return tabsEnabled() ? getActiveConversation()?.id : "default";
}

function updateReplyControls() {
  const operation = replyOperations.get(activeReplyKey());

  sendButton.disabled = Boolean(operation);

  if (replyStatus) {
    replyStatus.textContent = operation
      ? (operation.text ? "Writing your answer…" : "Thinking...")
      : "";
  }

  if (replyAction) {
    replyAction.hidden = !operation;
    replyAction.textContent = operation?.text
      ? "Show full answer"
      : "Cancel request";
  }
}

function cancelReply(key) {
  const operation = replyOperations.get(key);
  if (!operation) return;

  operation.controller.abort();
  clearTimeout(operation.timer);
  replyOperations.delete(key);

  if (key === activeReplyKey()) {
    removeTypingIndicator();
    updateReplyControls();

    if (replyStatus) {
      replyStatus.textContent = "Request canceled.";
    }
  }

  renderTabs();
}

function finishReply(key, speak = true) {
  const operation = replyOperations.get(key);
  if (!operation?.text) return;

  clearTimeout(operation.timer);
  replyOperations.delete(key);

  const visible = key === activeReplyKey();
  const scrollTop = chatBox.scrollTop;
  const follow =
    chatBox.scrollHeight - scrollTop - chatBox.clientHeight < 80;

  if (operation.wrapper?.isConnected && visible) {
    operation.wrapper.remove();
  }

  deliverBotReply(
    key === "default" ? null : key,
    operation.text,
    operation.meta
  );

  if (visible) {
    removeTypingIndicator();
  
    if (!follow) {
      chatBox.scrollTop = scrollTop;
    }

    updateMeta({
      source: operation.meta?.source,
      retrieved_docs: operation.meta?.retrievedDocs
    });

    updateReplyControls();

    if (replyStatus) {
      replyStatus.textContent = "Answer ready.";
    }

    if (speak) {
      speakBotReply(operation.text);
    }
  }

  renderTabs();
}

function leaveConversation() {
  const key = activeReplyKey();

  conversationDrafts.set(key, userInput.value);

  // Save a completed answer in its original chat before switching.
  finishReply(key, false);

  stopVoiceInput();
  stopVoiceOutput();
}

function characterRevealPlan(body, text) {
  // Format the complete answer once, then reveal its text.
  renderMessageText(body, text, true);

  const walker = document.createTreeWalker(
    body,
    NodeFilter.SHOW_TEXT
  );

  const plan = [];

  const segmenter = typeof Intl.Segmenter === "function"
    ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
    : null;

  let node;

  while ((node = walker.nextNode())) {
    const characters = segmenter
      ? Array.from(
          segmenter.segment(node.data),
          (part) => part.segment
        )
      : Array.from(node.data);

    if (characters.length) {
      plan.push({ node, characters });
    }
  }

  // Hide formatted elements until their text begins appearing.
  body.querySelectorAll("*").forEach((element) => {
    element.hidden = true;
  });

  plan.forEach((part) => {
    part.node.data = "";
  });

  return plan;
}

function revealReply(key, text, meta) {
  const operation = replyOperations.get(key);
  if (!operation) return;

  // Keep "Thinking..." visible for at least 450 milliseconds.
  const wait = Math.max(
    0,
    450 - (Date.now() - operation.startedAt)
  );

  if (wait && key === activeReplyKey()) {
    operation.timer = setTimeout(
      () => revealReply(key, text, meta),
      wait
    );
    return;
  }

  operation.text = text;
  operation.meta = meta;

  // Keep background replies in their original conversation.
  if (key !== activeReplyKey()) {
    finishReply(key, false);
    return;
  }

  removeTypingIndicator();

  operation.wrapper = addMessage(
    "",
    "bot-message",
    "ISDA Bot",
    { skipRecord: true }
  );

  operation.wrapper.classList.add("is-revealing");

  // Screen readers receive the finished answer once.
  operation.wrapper.setAttribute("aria-hidden", "true");

  const body = operation.wrapper.querySelector(".message-body");
  const plan = characterRevealPlan(body, text);

  let partIndex = 0;
  let characterIndex = 0;

  updateReplyControls();

  function tick() {
    if (replyOperations.get(key) !== operation) return;

    if (key !== activeReplyKey()) {
      finishReply(key, false);
      return;
    }

    const part = plan[partIndex];

    if (!part) {
      finishReply(key);
      return;
    }

    const nearBottom =
      chatBox.scrollHeight -
      chatBox.scrollTop -
      chatBox.clientHeight < 80;

    // Reveal the formatting around the current character.
    let parent = part.node.parentElement;

    while (parent && parent !== body) {
      parent.hidden = false;
      parent = parent.parentElement;
    }

    // Add exactly one visible character.
    part.node.appendData(part.characters[characterIndex]);
    characterIndex++;

    if (characterIndex === part.characters.length) {
      partIndex++;
      characterIndex = 0;
    }

    if (nearBottom) {
      chatBox.scrollTop = chatBox.scrollHeight;
    }

    if (partIndex === plan.length) {
      finishReply(key);
      return;
    }

    operation.timer = setTimeout(tick, 12);
  }

  operation.timer = setTimeout(tick, 12);
}

if (replyAction) {
  replyAction.addEventListener("click", () => {
    const key = activeReplyKey();

    if (replyOperations.get(key)?.text) {
      finishReply(key);
    } else {
      cancelReply(key);
    }
  });
}

// ── SEND MESSAGE ──
async function sendMessage(prefilledMessage = null) {
  const message = (prefilledMessage || userInput.value).trim();
  const key = activeReplyKey();

  if (!message || replyOperations.has(key)) return;

  const conversationId = key === "default" ? null : key;

  const operation = {
    controller: new AbortController(),
    startedAt: Date.now(),
    timer: null
  };

  replyOperations.set(key, operation);

  hideSampleChips();
  userInput.value = "";
  conversationDrafts.delete(key);
  updateMeta({});

  addMessage(message, "user-message", "You");
  showTypingIndicator();
  updateReplyControls();
  renderTabs();

  const payload = { message };

  if (conversationId) {
    payload.conversation_id = conversationId;
  }

  try {
    const response = await fetch(apiUrl("/api/chat"), {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload),
      signal: operation.controller.signal
    });

    const data = await response.json();

    if (replyOperations.get(key) !== operation) return;

    if (!response.ok) {
      throw new Error(
        data.error || "An error occurred while contacting the chatbot."
      );
    }

    revealReply(
      key,
      data.response || "I couldn't find an answer for that question.",
      {
        source: data.source,
        retrievedDocs: data.retrieved_docs,
        provider: data.provider || null,
        model: data.model || null
      }
    );
  } catch (error) {
    if (
      replyOperations.get(key) !== operation ||
      error.name === "AbortError"
    ) {
      return;
    }

    replyOperations.delete(key);

    if (key === activeReplyKey()) {
      removeTypingIndicator();
    }

    deliverBotReply(
      conversationId,
      "Sorry, I couldn't get an answer right now. Please try again."
    );

    updateReplyControls();
    renderTabs();
  }
}

function reportContext(messages, index, conversationId) {
  const message = messages && messages[index];
  if (!message || message.role === "user") return null;
  const answer = String(message.text || "").trim();
  let question = "";
  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    const earlier = messages[cursor];
    if (earlier && earlier.role === "user" && String(earlier.text || "").trim()) {
      question = String(earlier.text);
      break;
    }
  }
  if (!question.trim() || !answer) return null;
  return {
    question,
    answer: String(message.text),
    conversationId: conversationId || null,
    provider: message.provider || null,
    model: message.model || null,
    turns: messages.slice(0, index + 1).map((item) => ({
      role: item.role === "user" ? "user" : "assistant",
      text: String(item.text || "")
    }))
  };
}

function attachReportControl(wrapper, report) {
  if (!report || !String(report.question || "").trim() || !String(report.answer || "").trim()) {
    return;
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "report-issue-button";
  button.textContent = "Report issue";
  button.setAttribute("aria-label", "Report an issue with this answer");
  button.addEventListener("click", () => openReportDialog(button, report));
  wrapper.appendChild(button);
}

const REPORT_LIMITS = {
  question: 4000,
  answer: 12000,
  comment: 1000,
  turn: 4000,
  turns: 40
};

function clipReportText(value, limit) {
  const text = String(value ?? "");
  return text.length > limit ? text.slice(0, limit) : text;
}

let reportDialog = null;
let reportReturnFocus = null;
let pendingReport = null;

function ensureReportDialog() {
  if (reportDialog) return reportDialog;
  const dialog = document.createElement("dialog");
  dialog.id = "report-issue-dialog";
  dialog.className = "report-dialog";
  dialog.setAttribute("aria-labelledby", "report-issue-title");

  const form = document.createElement("form");
  form.id = "report-issue-form";
  form.noValidate = true;

  const title = document.createElement("h2");
  title.id = "report-issue-title";
  title.tabIndex = -1;
  title.textContent = "Report an issue";

  const privacy = document.createElement("p");
  privacy.id = "report-issue-privacy";
  privacy.className = "report-privacy";
  privacy.textContent =
    "Only the reason, your comment, and this answer are saved unless you include the conversation. Reports, including any conversation you share, are automatically deleted after 90 days.";

  const fieldset = document.createElement("fieldset");
  fieldset.className = "report-reasons";
  const legend = document.createElement("legend");
  legend.textContent = "Reason";
  fieldset.appendChild(legend);
  [
    ["incorrect_info", "Incorrect information"],
    ["made_up", "Made-up link or course"],
    ["outdated", "Outdated information"],
    ["other", "Other"]
  ].forEach(([value, labelText], index) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "report-reason";
    input.value = value;
    if (index === 0) input.id = "report-reason-incorrect";
    label.appendChild(input);
    label.appendChild(document.createTextNode(labelText));
    fieldset.appendChild(label);
  });

  const commentLabel = document.createElement("label");
  commentLabel.htmlFor = "report-issue-comment";
  commentLabel.textContent = "Comment (optional)";

  const comment = document.createElement("textarea");
  comment.id = "report-issue-comment";
  comment.maxLength = REPORT_LIMITS.comment;
  comment.rows = 3;

  const toggleLabel = document.createElement("label");
  toggleLabel.className = "report-toggle";
  toggleLabel.htmlFor = "report-include-conversation";
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.id = "report-include-conversation";
  const toggleText = document.createElement("span");
  toggleText.textContent = "Include this conversation so the team can review it";
  toggleLabel.append(toggle, toggleText);

  const error = document.createElement("p");
  error.id = "report-issue-error";
  error.className = "report-error";
  error.setAttribute("role", "alert");

  const actions = document.createElement("div");
  actions.className = "report-actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.id = "report-issue-cancel";
  cancel.className = "report-secondary";
  cancel.textContent = "Cancel";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.id = "report-issue-submit";
  submit.className = "report-primary";
  submit.textContent = "Submit report";
  actions.append(cancel, submit);

  form.append(title, privacy, fieldset, commentLabel, comment, toggleLabel, error, actions);

  const thanks = document.createElement("div");
  thanks.id = "report-issue-thanks";
  thanks.hidden = true;
  const thanksTitle = document.createElement("h2");
  thanksTitle.id = "report-issue-thanks-title";
  thanksTitle.tabIndex = -1;
  thanksTitle.textContent = "Thanks, your report was sent.";
  const thanksCopy = document.createElement("p");
  thanksCopy.textContent = "The advising team can review it from the evaluation workspace.";
  const done = document.createElement("button");
  done.type = "button";
  done.id = "report-issue-done";
  done.className = "report-primary";
  done.textContent = "Done";
  thanks.append(thanksTitle, thanksCopy, done);

  dialog.append(form, thanks);
  document.body.appendChild(dialog);

  cancel.addEventListener("click", () => dialog.close());
  done.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    const target = reportReturnFocus;
    reportReturnFocus = null;
    pendingReport = null;
    if (target && typeof target.focus === "function") target.focus();
  });
  dialog.addEventListener("cancel", () => {
    // Native Escape closes the dialog and then the close handler restores focus.
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitIssueReport(form);
  });

  reportDialog = dialog;
  return dialog;
}

function resetReportDialog(dialog) {
  const form = dialog.querySelector("#report-issue-form");
  const thanks = dialog.querySelector("#report-issue-thanks");
  form.hidden = false;
  thanks.hidden = true;
  dialog.setAttribute("aria-labelledby", "report-issue-title");
  form.reset();
  const toggle = dialog.querySelector("#report-include-conversation");
  if (toggle) toggle.checked = false;
  const error = dialog.querySelector("#report-issue-error");
  if (error) error.textContent = "";
  const submit = dialog.querySelector("#report-issue-submit");
  if (submit) submit.disabled = false;
}

function openReportDialog(button, report) {
  const dialog = ensureReportDialog();
  if (typeof dialog.showModal !== "function") return;
  pendingReport = report;
  reportReturnFocus = button;
  resetReportDialog(dialog);
  if (!dialog.open) dialog.showModal();
  const title = dialog.querySelector("#report-issue-title");
  if (title && typeof title.focus === "function") title.focus();
}

function reportPayload(report, form) {
  const selected = form.querySelector("input[name='report-reason']:checked");
  const comment = form.querySelector("#report-issue-comment");
  const toggle = form.querySelector("#report-include-conversation");
  const include = Boolean(toggle && toggle.checked);
  const payload = {
    reason: selected ? selected.value : "",
    comment: clipReportText(comment ? comment.value : "", REPORT_LIMITS.comment).trim(),
    question: clipReportText(report.question, REPORT_LIMITS.question),
    answer: clipReportText(report.answer, REPORT_LIMITS.answer),
    include_conversation: include
  };
  if (!include) return payload;
  const turns = Array.isArray(report.turns) ? report.turns.slice(-REPORT_LIMITS.turns) : [];
  payload.turns = turns.map((turn) => ({
    role: turn.role === "user" ? "user" : "assistant",
    text: clipReportText(turn.text, REPORT_LIMITS.turn)
  })).filter((turn) => turn.text.trim());
  if (report.conversationId) payload.conversation_id = report.conversationId;
  if (report.provider) payload.provider = String(report.provider).slice(0, 120);
  if (report.model) payload.model = String(report.model).slice(0, 120);
  return payload;
}

async function submitIssueReport(form) {
  const dialog = reportDialog;
  const error = dialog.querySelector("#report-issue-error");
  const submit = dialog.querySelector("#report-issue-submit");
  const selected = form.querySelector("input[name='report-reason']:checked");
  if (!pendingReport) return;
  if (!selected) {
    error.textContent = "Choose a reason.";
    const first = form.querySelector("input[name='report-reason']");
    if (first && typeof first.focus === "function") first.focus();
    return;
  }
  error.textContent = "";
  submit.disabled = true;
  try {
    const csrfResponse = await fetch(apiUrl("/api/issue-reports/csrf"), {
      method: "GET",
      credentials: "include"
    });
    const csrfBody = await csrfResponse.json();
    if (!csrfResponse.ok || !csrfBody.csrf) {
      throw new Error(csrfBody.error || "The report could not be sent. Please try again.");
    }
    const response = await fetch(apiUrl("/api/issue-reports"), {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-Issue-Report-CSRF": csrfBody.csrf
      },
      body: JSON.stringify(reportPayload(pendingReport, form))
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || "The report could not be sent. Please try again.");
    }
    form.hidden = true;
    const thanks = dialog.querySelector("#report-issue-thanks");
    thanks.hidden = false;
    dialog.setAttribute("aria-labelledby", "report-issue-thanks-title");
    const thanksTitle = dialog.querySelector("#report-issue-thanks-title");
    if (thanksTitle && typeof thanksTitle.focus === "function") thanksTitle.focus();
  } catch (sendError) {
    error.textContent = sendError.message || "The report could not be sent. Please try again.";
    submit.disabled = false;
  }
}

function deliverBotReply(conversationId, text, meta) {
  const stored = {
    role: "bot",
    text: text,
    source: meta && meta.source,
    retrievedDocs: meta ? meta.retrievedDocs : null,
    provider: meta && meta.provider ? meta.provider : null,
    model: meta && meta.model ? meta.model : null
  };
  let messages = plainTranscript;
  if (conversationId) {
    rememberMessage(conversationId, stored);
    const convo = conversations.find((item) => item.id === conversationId);
    messages = convo ? convo.messages : [];
    if (!isActiveConversation(conversationId)) return;
  }
  const index = Math.max(0, messages.length - (conversationId ? 1 : 0));
  const reportMessages = conversationId
    ? messages.slice(0, index + 1)
    : messages.concat([stored]);
  addMessage(text, "bot-message", "ISDA Bot", {
    skipRecord: Boolean(conversationId),
    reportable: true,
    report: reportContext(reportMessages, reportMessages.length - 1, conversationId)
  });
}

function speakBotReply(responseText) {
  try {
    // Visible words only. Markdown markers and the bubble's HTML are not spoken.
    const spoken =
      typeof botMarkdownPlainText === "function"
        ? botMarkdownPlainText(responseText)
        : responseText;
    speakResponse(spoken);
  } catch (error) {
    console.error("Voice reply error:", error);
  }
}

// ── STATUS ──
// /api/status builds the full RAG stack (Chroma plus the chat model) inside
// the request. Page load must not do that. /api/voice-config is already
// fetched below, is under /api so the static docs page can call it cross-origin,
// and does not open the index. The index loads on the first real question.
function setServiceStatus(state, detail) {
  if (!statusText || !providerText) return;
  statusText.classList.remove("is-ready", "is-error", "is-unavailable");
  if (state === "ready") {
    statusText.textContent = "Ready to help";
    statusText.classList.add("is-ready");
    providerText.textContent = "Advising support is available.";
    return;
  }
  if (state === "error") {
    statusText.textContent = "Status: error";
    statusText.classList.add("is-error");
    providerText.textContent = detail || "Could not load status.";
    return;
  }
  statusText.textContent = "Status: unavailable";
  statusText.classList.add("is-unavailable");
  providerText.textContent =
    detail || "Could not connect to backend status endpoint.";
}

// ── CLEAR ──
async function clearHistory(conversationId) {
  try {
    const options = { method: "POST", credentials: "include" };
    if (conversationId) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify({ conversation_id: conversationId });
    }
    await fetch(apiUrl("/api/clear-history"), options);
  } catch (error) {
    console.error("Clear history error:", error);
  }
}

// ── EVENT LISTENERS ──
sendButton.addEventListener("click", () => sendMessage());

userInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) {
    event.preventDefault();
    sendMessage();
  }
});

function resetTopicSelection() {
  topicButtons.forEach((b) => {
    b.classList.remove("active");
    b.setAttribute("aria-pressed", "false");
  });

  activeBanner?.classList.add("hidden");
  hideSampleChips();
  if (sampleChipsContainer) sampleChipsContainer.innerHTML = "";
  activeTopic = null;
  updateMeta({});
}

clearButton.addEventListener("click", async (event) => {
  plainTranscript = [];
  if (!tabsEnabled()) cancelReply("default");
  if (tabsEnabled()) {
    startNewConversation();
  } else if (emptyState) {
    showEmptyState();
  } else {
    chatBox.innerHTML = `
    <div class="message bot-message">
      <div class="message-label">ISDA Bot</div>
      <div>Hi! I’m your ISDA advising assistant. I can help you explore degree requirements, plan your courses, and navigate registration. Choose a topic or ask me a question to get started.</div>
    </div>
  `;
  }

  resetTopicSelection();

  // A mouse click leaves the button focused, which looks stuck in a pressed state.
  if (event && event.detail > 0 && typeof clearButton.blur === "function") {
    clearButton.blur();
  } else if (tabsEnabled()) {
    const selected = chatTabs.querySelector('[aria-selected="true"]');
    if (selected && typeof selected.focus === "function") selected.focus();
  }

  if (!tabsEnabled()) await clearHistory();
});

document.querySelectorAll(".starter-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const question = String(chip.getAttribute("data-question") || "").trim();
    if (question) sendMessage(question);
  });
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

// ── CONVERSATION TABS ──
// Transcripts stay in this browser. The server only keeps model context,
// keyed by conversation_id under the existing session cookie.
const CONVERSATION_STORAGE_KEY = "isda-chat-conversations-v1";
const MAX_CONVERSATIONS = 10;
const MAX_STORED_MESSAGES = 50;
const CONVERSATION_ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;

let conversations = [];
let activeConversationId = null;

function tabsEnabled() {
  return Boolean(chatTabs && emptyState);
}

function browserStorage() {
  try {
    if (typeof localStorage === "undefined" || !localStorage) return null;
    return localStorage;
  } catch (_error) {
    return null;
  }
}

function newConversationId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  let suffix = "";
  for (let index = 0; index < 16; index += 1) {
    suffix += Math.floor(Math.random() * 36).toString(36);
  }
  return `conv-${suffix}`;
}

function createConversation() {
  const now = Date.now();
  return { id: newConversationId(), createdAt: now, updatedAt: now, messages: [] };
}

function conversationTitle(convo) {
  const firstUser = (convo.messages || []).find((message) => message.role === "user");
  if (!firstUser || !firstUser.text) return "New chat";
  return String(firstUser.text).replace(/\s+/g, " ").trim() || "New chat";
}

function normalizeMessage(message) {
  if (!message || (message.role !== "user" && message.role !== "bot")) return null;
  if (typeof message.text !== "string") return null;
  const normalized = {
    role: message.role,
    text: message.text,
    source: typeof message.source === "string" ? message.source : null,
    retrievedDocs: Number.isFinite(message.retrievedDocs) ? message.retrievedDocs : null
  };
  if (typeof message.provider === "string" && message.provider) {
    normalized.provider = message.provider.slice(0, 120);
  }
  if (typeof message.model === "string" && message.model) {
    normalized.model = message.model.slice(0, 120);
  }
  return normalized;
}

function normalizeConversation(raw) {
  if (!raw || typeof raw.id !== "string" || !CONVERSATION_ID_PATTERN.test(raw.id)) return null;
  const messages = Array.isArray(raw.messages)
    ? raw.messages.map(normalizeMessage).filter(Boolean).slice(-MAX_STORED_MESSAGES)
    : [];
  const createdAt = Number.isFinite(raw.createdAt) ? raw.createdAt : Date.now();
  const updatedAt = Number.isFinite(raw.updatedAt) ? raw.updatedAt : createdAt;
  return { id: raw.id, createdAt, updatedAt, messages };
}

function getActiveConversation() {
  return conversations.find((item) => item.id === activeConversationId) || conversations[0] || null;
}

function isActiveConversation(conversationId) {
  if (!conversationId || !tabsEnabled()) return false;
  const current = getActiveConversation();
  return Boolean(current && current.id === conversationId);
}

function saveConversations() {
  const store = browserStorage();
  if (!store) return;
  try {
    store.setItem(CONVERSATION_STORAGE_KEY, JSON.stringify({
      activeId: activeConversationId,
      conversations: conversations
    }));
  } catch (_error) {
    // A full or blocked store should not stop the chat.
  }
}

function loadStoredConversations() {
  const store = browserStorage();
  if (!store) return;
  try {
    const raw = store.getItem(CONVERSATION_STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const stored = Array.isArray(parsed.conversations)
      ? parsed.conversations.map(normalizeConversation).filter(Boolean)
      : [];
    conversations = stored;
    activeConversationId = typeof parsed.activeId === "string" ? parsed.activeId : null;
  } catch (_error) {
    conversations = [];
    activeConversationId = null;
  }
}

function dropOldestConversation() {
  let chosen = -1;

  conversations.forEach((convo, index) => {
    if (convo.id === activeConversationId) return;

    if (
      chosen === -1 ||
      convo.updatedAt < conversations[chosen].updatedAt
    ) {
      chosen = index;
    }
  });

  if (chosen < 0) return;

  const removed = conversations.splice(chosen, 1)[0];

  cancelReply(removed.id);
  conversationDrafts.delete(removed.id);
  clearHistory(removed.id);
}

function trimConversations() {
  while (conversations.length > MAX_CONVERSATIONS) dropOldestConversation();
}

function rememberMessage(conversationId, message) {
  const convo = conversations.find((item) => item.id === conversationId);
  const normalized = normalizeMessage(message);
  if (!convo || !normalized) return;
  convo.messages.push(normalized);
  if (convo.messages.length > MAX_STORED_MESSAGES) {
    convo.messages.splice(0, convo.messages.length - MAX_STORED_MESSAGES);
  }
  convo.updatedAt = Date.now();
  trimConversations();
  saveConversations();
  renderTabs();
}

function paintConversation(convo) {
  if (!convo || !chatBox) return;

  userInput.value = conversationDrafts.get(convo.id) || "";
  updateReplyControls();
  hideSampleChips();

  if (typeof chatBox.replaceChildren === "function") {
    chatBox.replaceChildren();
  } else {
    chatBox.innerHTML = "";
  }

  if (!convo.messages.length) {
    showEmptyState();
    updateMeta({});
    return;
  }

  convo.messages.forEach((message, index) => {
    const isBot = message.role === "bot";
    addMessage(
      message.text,
      isBot ? "bot-message" : "user-message",
      isBot ? "ISDA Bot" : "You",
      {
        skipRecord: true,
        reportable: isBot,
        report: isBot ? reportContext(convo.messages, index, convo.id) : null
      }
    );
  });

  if (replyOperations.has(convo.id)) {
    showTypingIndicator();
  }

  const lastBot = [...convo.messages]
    .reverse()
    .find((message) => message.role === "bot");

  if (
    lastBot &&
    (lastBot.source || lastBot.retrievedDocs !== null)
  ) {
    updateMeta({
      source: lastBot.source,
      retrieved_docs: lastBot.retrievedDocs
    });
  } else {
    updateMeta({});
  }
}

function renderTabs(options) {
  if (!chatTabs) return;
  const activeElement = document.activeElement;
  const focusId = activeElement && activeElement.dataset
    ? activeElement.dataset.conversationId
    : null;
  const focusWasClose = Boolean(
    activeElement &&
    activeElement.classList &&
    activeElement.classList.contains("chat-tab-close")
  );
  if (typeof chatTabs.replaceChildren === "function") chatTabs.replaceChildren();
  else chatTabs.innerHTML = "";

  conversations.forEach((convo) => {
    const selected = convo.id === activeConversationId;
    const title = conversationTitle(convo);
    const tab = document.createElement("div");
    tab.className = "chat-tab";
    tab.classList.toggle("is-working", replyOperations.has(convo.id));
    tab.title = title;

  tab.setAttribute(
  "aria-label",
  title + (replyOperations.has(convo.id) ? ", answer in progress" : "")
);
    tab.id = `chat-tab-${convo.id}`;
    tab.dataset.conversationId = convo.id;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.setAttribute("aria-controls", "conversation-stage");
    tab.tabIndex = selected ? 0 : -1;

    const label = document.createElement("span");
    label.className = "chat-tab-label";
    label.textContent = title;
    if (title.length > 32) label.title = title;

    const close = document.createElement("button");
    close.type = "button";
    close.className = "chat-tab-close";
    close.dataset.conversationId = convo.id;
    close.setAttribute("aria-label", `Close ${title}`);
    close.textContent = "×";
    close.title = `Close ${title}`;
    close.tabIndex = selected ? 0 : -1;

    tab.appendChild(label);
    tab.appendChild(close);
    chatTabs.appendChild(tab);

    if (focusId === convo.id) {
      const target = focusWasClose ? close : tab;
      if (typeof target.focus === "function") target.focus();
    }
  });

  const stage = document.getElementById("conversation-stage");
  const active = getActiveConversation();
  if (stage && active) stage.setAttribute("aria-labelledby", `chat-tab-${active.id}`);

  if (options && options.scrollActive && typeof chatTabs.querySelector === "function") {
    const selectedTab = chatTabs.querySelector('[aria-selected="true"]');
    if (selectedTab && typeof selectedTab.scrollIntoView === "function") {
      selectedTab.scrollIntoView({ inline: "nearest", block: "nearest" });
    }
  }
}

function selectConversation(conversationId) {
  const convo = conversations.find(
    (item) => item.id === conversationId
  );

  if (!convo || convo.id === activeConversationId) {
    renderTabs({ scrollActive: true });
    return;
  }

  leaveConversation();

  activeConversationId = convo.id;
  convo.updatedAt = Date.now();

  saveConversations();
  resetTopicSelection();
  paintConversation(convo);
  renderTabs({ scrollActive: true });
}

function startNewConversation() {
  leaveConversation();

  const current = getActiveConversation();

  if (current && current.messages.length === 0) {
    paintConversation(current);
    renderTabs({ scrollActive: true });
    return;
  }

  const convo = createConversation();

  conversations.push(convo);
  activeConversationId = convo.id;

  trimConversations();
  saveConversations();
  paintConversation(convo);
  renderTabs({ scrollActive: true });
}

function closeConversation(conversationId) {
  const index = conversations.findIndex(
    (item) => item.id === conversationId
  );

  if (index < 0) return;

  const closingActive = conversationId === activeConversationId;

  if (closingActive) {
    stopVoiceInput();
    stopVoiceOutput();
  }

  cancelReply(conversationId);
  conversationDrafts.delete(conversationId);

  const removed = conversations.splice(index, 1)[0];
  clearHistory(removed.id);

  if (!conversations.length) {
    const fresh = createConversation();

    conversations.push(fresh);
    activeConversationId = fresh.id;
  } else if (activeConversationId === conversationId) {
    const neighbor = conversations[
      Math.min(index, conversations.length - 1)
    ];

    activeConversationId = neighbor.id;
  }

  saveConversations();

  if (closingActive) {
    resetTopicSelection();
    paintConversation(getActiveConversation());
  }

  renderTabs({ scrollActive: true });

  const selected = chatTabs.querySelector('[aria-selected="true"]');

  if (selected && typeof selected.focus === "function") {
    selected.focus();
  }
}

function tabFromTarget(target) {
  if (!target || typeof target.closest !== "function") return null;
  return target.closest('[role="tab"]');
}

function onTabClick(event) {
  const closeButton = event.target && typeof event.target.closest === "function"
    ? event.target.closest(".chat-tab-close")
    : null;
  if (closeButton) {
    event.preventDefault();
    closeConversation(closeButton.dataset.conversationId);
    return;
  }
  const tab = tabFromTarget(event.target);
  if (tab) selectConversation(tab.dataset.conversationId);
}

function moveTabFocus(current, direction) {
  const tabs = [...chatTabs.querySelectorAll('[role="tab"]')];
  const index = tabs.indexOf(current);
  if (index < 0 || !tabs.length) return;
  let next = current;
  if (direction === "next") next = tabs[(index + 1) % tabs.length];
  if (direction === "previous") next = tabs[(index - 1 + tabs.length) % tabs.length];
  if (direction === "first") next = tabs[0];
  if (direction === "last") next = tabs[tabs.length - 1];
  selectConversation(next.dataset.conversationId);
  const selected = [...chatTabs.querySelectorAll('[role="tab"]')].find(
    (tab) => tab.dataset.conversationId === next.dataset.conversationId
  );
  if (selected && typeof selected.focus === "function") selected.focus();
}

function onTabKeydown(event) {
  const tab = tabFromTarget(event.target);
  if (!tab) return;
  const onClose = Boolean(
    event.target.classList && event.target.classList.contains("chat-tab-close")
  );
  if (event.key === "ArrowRight") {
    event.preventDefault();
    moveTabFocus(tab, "next");
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    moveTabFocus(tab, "previous");
  } else if (event.key === "Home") {
    event.preventDefault();
    moveTabFocus(tab, "first");
  } else if (event.key === "End") {
    event.preventDefault();
    moveTabFocus(tab, "last");
  } else if (!onClose && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    selectConversation(tab.dataset.conversationId);
  } else if (!onClose && (event.key === "Delete" || event.key === "Backspace")) {
    event.preventDefault();
    closeConversation(tab.dataset.conversationId);
  }
}

function initConversations() {
  if (!tabsEnabled()) return;
  loadStoredConversations();
  if (!conversations.length) conversations = [createConversation()];
  if (!conversations.some((item) => item.id === activeConversationId)) {
    activeConversationId = conversations[conversations.length - 1].id;
  }
  trimConversations();
  saveConversations();
  paintConversation(getActiveConversation());
  renderTabs({ scrollActive: true });
  chatTabs.addEventListener("click", onTabClick);
  chatTabs.addEventListener("keydown", onTabKeydown);
}

initConversations();
loadVoiceConfig();
