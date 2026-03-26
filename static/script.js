const chatBox = document.getElementById("chat-box");
const userInput = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");
const clearButton = document.getElementById("clear-button");
const promptChips = document.querySelectorAll(".prompt-chip");
const statusText = document.getElementById("status-text");
const providerText = document.getElementById("provider-text");
const loadDocsButton = document.getElementById("load-docs-button");

const sourceBadge = document.getElementById("source-badge");
const confidenceBadge = document.getElementById("confidence-badge");
const docsBadge = document.getElementById("docs-badge");

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

async function sendMessage(prefilledMessage = null) {
  const message = (prefilledMessage || userInput.value).trim();
  if (!message) return;

  addMessage(message, "user-message", "You");
  userInput.value = "";
  showTypingIndicator();
  updateMeta({});

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ message: message })
    });

    const data = await response.json();
    removeTypingIndicator();

    if (!response.ok) {
      addMessage(data.error || "An error occurred while contacting the chatbot.", "bot-message", "ISDA Bot");
      return;
    }

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
  } catch (error) {
    removeTypingIndicator();
    addMessage(
      "Sorry, I couldn't connect to the chatbot backend right now.",
      "bot-message",
      "ISDA Bot"
    );
    console.error("Chat error:", error);
  }
}

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

async function clearHistory() {
  try {
    await fetch("/api/clear-history", {
      method: "POST"
    });
  } catch (error) {
    console.error("Clear history error:", error);
  }
}

async function loadDocuments() {
  loadDocsButton.disabled = true;
  loadDocsButton.textContent = "Loading...";

  try {
    const response = await fetch("/api/load-docs", {
      method: "POST"
    });

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

sendButton.addEventListener("click", () => sendMessage());

userInput.addEventListener("keypress", (event) => {
  if (event.key === "Enter") {
    sendMessage();
  }
});

clearButton.addEventListener("click", async () => {
  chatBox.innerHTML = `
    <div class="message bot-message">
      <div class="message-label">ISDA Bot</div>
      <div>Hi! I’m the ISDA Advising Support Chatbot. I can help with general questions about advising, degree requirements, course planning, and registration.</div>
    </div>
  `;
  updateMeta({});
  await clearHistory();
});

promptChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    sendMessage(chip.textContent);
  });
});

loadDocsButton.addEventListener("click", loadDocuments);

loadStatus();