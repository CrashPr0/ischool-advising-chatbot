const chatBox = document.getElementById("chat-box");
const userInput = document.getElementById("user-input");
const sendButton = document.getElementById("send-button");
const clearButton = document.getElementById("clear-button");
const promptChips = document.querySelectorAll(".prompt-chip");

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

async function sendMessage(prefilledMessage = null) {
  const message = (prefilledMessage || userInput.value).trim();
  if (!message) return;

  addMessage(message, "user-message", "You");
  userInput.value = "";
  showTypingIndicator();

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ message: message })
    });

    const data = await response.json();
    removeTypingIndicator();

    const answer = data.answer || "I couldn't find an answer for that question.";
    addMessage(answer, "bot-message", "ISDA Bot");
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

sendButton.addEventListener("click", () => sendMessage());

userInput.addEventListener("keypress", (event) => {
  if (event.key === "Enter") {
    sendMessage();
  }
});

clearButton.addEventListener("click", () => {
  chatBox.innerHTML = `
    <div class="message bot-message">
      <div class="message-label">ISDA Bot</div>
      <div>Hi! I’m the ISDA Advising Support Chatbot. I can help with general questions about advising, degree requirements, course planning, and registration.</div>
    </div>
  `;
});

promptChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    sendMessage(chip.textContent);
  });
});