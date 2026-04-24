const chatBox = document.getElementById("chat-box");
const userInput = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");
const clearButton = document.getElementById("clear-button");
const statusText = document.getElementById("status-text");
const providerText = document.getElementById("provider-text");
const loadDocsButton = document.getElementById("load-docs-button");

const sourceBadge = document.getElementById("source-badge");
const confidenceBadge = document.getElementById("confidence-badge");
const docsBadge = document.getElementById("docs-badge");

const topicButtons = document.querySelectorAll(".topic-btn");
const activeBanner = document.getElementById("active-topic-banner");
const activeTopicLabel = document.getElementById("active-topic-label");
const sampleChipsArea = document.getElementById("sample-chips-area");
const sampleChipsLabel = document.getElementById("sample-chips-label");
const sampleChipsContainer = document.getElementById("sample-chips");

let activeTopic = null;

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
    const response = await fetch("/api/chat", {
      method: "POST",
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
      addMessage(
        data.response || "I couldn't find an answer for that question.",
        "bot-message",
        "ISDA Bot"
      );
      updateMeta({
        source: data.source,
        confidence: data.confidence,
        retrieved_docs: data.retrieved_docs
      });
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
    const response = await fetch("/api/status");
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
    await fetch("/api/clear-history", { method: "POST" });
  } catch (error) {
    console.error("Clear history error:", error);
  }
}

// ── LOAD DOCS ──
async function loadDocuments() {
  loadDocsButton.disabled = true;
  loadDocsButton.textContent = "Loading...";

  try {
    const response = await fetch("/api/load-docs", { method: "POST" });
    const data = await response.json();

    if (response.ok) {
      addMessage(data.message || "Documents loaded successfully.", "bot-message", "System");
    } else {
      addMessage(data.error || "Failed to load documents.", "bot-message", "System");
    }
  } catch (error) {
    addMessage("Error loading documents.", "bot-message", "System");
  } finally {
    loadDocsButton.disabled = false;
    loadDocsButton.textContent = "Load Documents";
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

loadDocsButton.addEventListener("click", loadDocuments);

loadStatus();