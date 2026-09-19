const state = {
  csrf: null,
  reviewer: null,
  workspace: null,
  currentIndex: 0,
  selectedRatings: { A: null, B: null }
};

const elements = {
  authDialog: document.getElementById("auth-dialog"),
  authForm: document.getElementById("auth-form"),
  authError: document.getElementById("auth-error"),
  reviewerName: document.getElementById("reviewer-name"),
  accessToken: document.getElementById("access-token"),
  accessTokenGroup: document.getElementById("access-token-group"),
  reviewerChip: document.getElementById("reviewer-chip"),
  loading: document.getElementById("loading-state"),
  empty: document.getElementById("empty-state"),
  emptyMessage: document.getElementById("empty-message"),
  app: document.getElementById("review-app"),
  questionList: document.getElementById("question-list"),
  queueCount: document.getElementById("queue-count"),
  questionsProgress: document.getElementById("questions-progress"),
  responsesProgress: document.getElementById("responses-progress"),
  runCreated: document.getElementById("run-created"),
  questionNumber: document.getElementById("question-number"),
  questionStatus: document.getElementById("question-status"),
  questionText: document.getElementById("question-text"),
  reviewFocus: document.getElementById("review-focus"),
  sourceList: document.getElementById("source-list"),
  revealPanel: document.getElementById("reveal-panel"),
  previousButton: document.getElementById("previous-button"),
  nextButton: document.getElementById("next-button"),
  nextIncompleteButton: document.getElementById("next-incomplete-button"),
  workspacePosition: document.getElementById("workspace-position"),
  summaryPanel: document.getElementById("summary-panel"),
  modelSummary: document.getElementById("model-summary"),
  reviewerCount: document.getElementById("reviewer-count"),
  exportButton: document.getElementById("export-button"),
  logoutButton: document.getElementById("logout-button"),
  toast: document.getElementById("toast")
};

function apiUrl(path) {
  return String(path);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (state.csrf && !["GET", "HEAD"].includes(String(options.method || "GET").toUpperCase())) {
    headers.set("X-Evaluation-CSRF", state.csrf);
  }
  const response = await fetch(apiUrl(path), {
    credentials: "same-origin",
    ...options,
    headers
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(data?.error || `Request failed (${response.status})`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function showToast(message, isError = false) {
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", isError);
  elements.toast.classList.add("visible");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => elements.toast.classList.remove("visible"), 2600);
}

function countSentences(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return 0;
  const matches = text.match(/[^.!?。！？]+(?:[.!?。！？]+|$)/g) || [];
  return matches.filter((segment) => segment.replace(/[.!?。！？]/g, "").trim()).length;
}

function updateSentenceCount(candidate) {
  const textarea = document.getElementById(`comment-${candidate}`);
  const counter = document.getElementById(`sentence-count-${candidate}`);
  const count = countSentences(textarea.value);
  counter.textContent = `${count} ${count === 1 ? "sentence" : "sentences"}`;
  counter.classList.toggle("valid", count >= 1 && count <= 3 && textarea.value.trim().length >= 10);
}

function buildRatingScales() {
  ["A", "B"].forEach((candidate) => {
    const container = document.getElementById(`rating-${candidate}`);
    for (let score = 1; score <= 7; score += 1) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "rating-option";
      button.dataset.rating = String(score);
      button.textContent = String(score);
      button.setAttribute("role", "radio");
      button.setAttribute("aria-checked", "false");
      button.title = score === 1 ? "Unusable" : score === 4 ? "Mixed" : score === 7 ? "Excellent" : `Rating ${score}`;
      button.addEventListener("click", () => selectRating(candidate, score));
      container.appendChild(button);
    }
  });
}

function selectRating(candidate, score) {
  state.selectedRatings[candidate] = score;
  document.querySelectorAll(`#rating-${candidate} .rating-option`).forEach((button) => {
    const selected = Number(button.dataset.rating) === Number(score);
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-checked", String(selected));
  });
  document.getElementById(`error-${candidate}`).textContent = "";
}

function formatDate(value) {
  if (!value) return "Unknown run date";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function cleanModelText(value) {
  return String(value || "")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, "$1 ($2)")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .trim();
}

function showAuthenticatedSession(sessionData) {
  state.csrf = sessionData.csrf;
  state.reviewer = sessionData.reviewer;
  elements.reviewerChip.textContent = sessionData.reviewer;
  if (elements.authDialog.open) elements.authDialog.close();
}

async function initializeSession() {
  try {
    const sessionData = await api("/api/evaluation/session");
    elements.accessTokenGroup.classList.toggle("hidden", !sessionData.token_required);
    if (sessionData.authenticated) {
      showAuthenticatedSession(sessionData);
      await loadWorkspace();
      return;
    }
    elements.loading.classList.add("hidden");
    elements.authDialog.showModal();
  } catch (error) {
    elements.loading.classList.add("hidden");
    showToast(error.message, true);
    elements.authDialog.showModal();
  }
}

async function signIn(event) {
  event.preventDefault();
  elements.authError.textContent = "";
  const reviewer = elements.reviewerName.value.trim();
  if (reviewer.length < 2) {
    elements.authError.textContent = "Enter your name or work email.";
    return;
  }
  const submit = elements.authForm.querySelector("button[type='submit']");
  submit.disabled = true;
  try {
    const sessionData = await api("/api/evaluation/session", {
      method: "POST",
      body: JSON.stringify({ reviewer, access_token: elements.accessToken.value })
    });
    elements.accessToken.value = "";
    showAuthenticatedSession(sessionData);
    elements.loading.classList.remove("hidden");
    await loadWorkspace();
  } catch (error) {
    elements.authError.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
}

async function loadWorkspace() {
  elements.empty.classList.add("hidden");
  try {
    state.workspace = await api("/api/evaluation/workspace");
    if (state.currentIndex >= state.workspace.questions.length) state.currentIndex = 0;
    renderWorkspace();
    elements.loading.classList.add("hidden");
    elements.app.classList.remove("hidden");
    if (state.workspace.progress.submitted_reviews === state.workspace.progress.total_reviews) {
      await loadSummary();
    } else {
      elements.summaryPanel.classList.add("hidden");
    }
  } catch (error) {
    elements.loading.classList.add("hidden");
    elements.app.classList.add("hidden");
    if (error.status === 404) {
      elements.emptyMessage.textContent = error.message;
      elements.empty.classList.remove("hidden");
    } else if (error.status === 401) {
      elements.authDialog.showModal();
    } else {
      showToast(error.message, true);
    }
  }
}

function renderWorkspace() {
  const workspace = state.workspace;
  const progress = workspace.progress;
  elements.questionsProgress.textContent = `${progress.completed_questions} / ${progress.total_questions}`;
  elements.responsesProgress.textContent = `${progress.submitted_reviews} / ${progress.total_reviews}`;
  elements.runCreated.textContent = formatDate(workspace.created_at);
  elements.queueCount.textContent = `${workspace.questions.length} questions`;
  elements.exportButton.disabled = progress.submitted_reviews !== progress.total_reviews;
  renderQuestionList();
  renderCurrentQuestion();
}

function renderQuestionList() {
  elements.questionList.replaceChildren();
  state.workspace.questions.forEach((question, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `question-nav-item${question.complete ? " complete" : ""}${index === state.currentIndex ? " active" : ""}`;
    button.setAttribute("aria-current", index === state.currentIndex ? "true" : "false");

    const number = document.createElement("span");
    number.className = "question-nav-item__number";
    number.textContent = String(index + 1).padStart(2, "0");
    const title = document.createElement("span");
    title.className = "question-nav-item__title";
    title.textContent = question.question;
    const status = document.createElement("span");
    status.className = "question-nav-item__state";
    status.setAttribute("aria-label", question.complete ? "Complete" : "Incomplete");
    button.append(number, title, status);
    button.addEventListener("click", () => navigateTo(index));
    elements.questionList.appendChild(button);
  });
}

function renderCurrentQuestion() {
  const questions = state.workspace.questions;
  const question = questions[state.currentIndex];
  if (!question) return;
  elements.questionNumber.textContent = `Question ${state.currentIndex + 1} · ${question.id}`;
  elements.questionText.textContent = question.question;
  elements.questionStatus.textContent = question.complete ? "Review complete" : "Review in progress";
  elements.questionStatus.classList.toggle("complete", question.complete);
  elements.reviewFocus.textContent = question.review_focus || "Evaluate accuracy, evidence use, clarity, and appropriate uncertainty.";
  elements.sourceList.replaceChildren();
  (question.sources || []).forEach((source) => {
    const chip = document.createElement("span");
    chip.textContent = source;
    chip.title = source;
    elements.sourceList.appendChild(chip);
  });
  if (!question.sources?.length) {
    const chip = document.createElement("span");
    chip.textContent = "No retrieved sources";
    elements.sourceList.appendChild(chip);
  }

  ["A", "B"].forEach((candidate) => renderCandidate(candidate, question.candidates[candidate]));
  renderReveal(question);
  elements.previousButton.disabled = state.currentIndex === 0;
  elements.nextButton.disabled = state.currentIndex === questions.length - 1;
  elements.workspacePosition.textContent = `${state.currentIndex + 1} of ${questions.length}`;
}

function renderCandidate(candidate, data) {
  const answer = document.getElementById(`answer-${candidate}`);
  answer.textContent = data.error
    ? `Model request failed: ${data.error}`
    : cleanModelText(data.answer) || "No response returned.";
  answer.classList.remove("expanded");
  document.querySelector(`.expand-answer[data-candidate='${candidate}']`).textContent = "Expand answer";

  const review = data.review;
  state.selectedRatings[candidate] = review?.rating || null;
  selectRating(candidate, review?.rating || null);
  const textarea = document.getElementById(`comment-${candidate}`);
  textarea.value = review?.comment || "";
  updateSentenceCount(candidate);
  const saved = document.getElementById(`saved-${candidate}`);
  saved.textContent = review ? `Saved · ${formatDate(review.updated_at)}` : "Not saved";
  saved.classList.toggle("saved", Boolean(review));
  document.getElementById(`error-${candidate}`).textContent = "";
}

function renderReveal(question) {
  if (!question.reveal) {
    elements.revealPanel.classList.add("hidden");
    return;
  }
  ["A", "B"].forEach((candidate) => {
    document.getElementById(`reveal-${candidate}`).textContent = question.reveal[candidate].label;
  });
  elements.revealPanel.classList.remove("hidden");
}

function navigateTo(index) {
  state.currentIndex = Math.max(0, Math.min(index, state.workspace.questions.length - 1));
  renderQuestionList();
  renderCurrentQuestion();
  document.querySelector(".question-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function saveReview(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const candidate = form.dataset.candidate;
  const rating = state.selectedRatings[candidate];
  const comment = document.getElementById(`comment-${candidate}`).value.trim();
  const errorElement = document.getElementById(`error-${candidate}`);
  const sentences = countSentences(comment);
  errorElement.textContent = "";
  if (!rating) {
    errorElement.textContent = "Choose a rating from 1 to 7.";
    return;
  }
  if (comment.length < 10 || sentences < 1 || sentences > 3) {
    errorElement.textContent = "Write 1–3 sentences with at least 10 characters.";
    return;
  }

  const button = form.querySelector(".save-review");
  button.disabled = true;
  button.textContent = "Saving…";
  const question = state.workspace.questions[state.currentIndex];
  try {
    await api(`/api/evaluation/reviews/${encodeURIComponent(question.id)}/${candidate}`, {
      method: "PUT",
      body: JSON.stringify({ rating, comment })
    });
    showToast(`Candidate ${candidate} review saved.`);
    await loadWorkspace();
  } catch (error) {
    errorElement.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = `Save Candidate ${candidate}`;
  }
}

async function loadSummary() {
  try {
    const summary = await api("/api/evaluation/summary");
    if (!summary.available) return;
    elements.modelSummary.replaceChildren();
    Object.values(summary.models).forEach((model) => {
      const card = document.createElement("article");
      card.className = "model-result";
      const provider = document.createElement("span");
      provider.textContent = model.provider || "Model";
      const heading = document.createElement("h3");
      heading.textContent = model.label;
      const score = document.createElement("div");
      score.className = "model-score";
      const strong = document.createElement("strong");
      strong.textContent = model.average_rating ?? "—";
      const scale = document.createElement("span");
      scale.textContent = `/ 7 · ${model.review_count} reviews`;
      score.append(strong, scale);
      card.append(provider, heading, score);
      elements.modelSummary.appendChild(card);
    });
    elements.reviewerCount.textContent = `${summary.total_reviewers} reviewer${summary.total_reviewers === 1 ? "" : "s"}`;
    elements.summaryPanel.classList.remove("hidden");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function exportCsv() {
  elements.exportButton.disabled = true;
  try {
    const response = await fetch("/api/evaluation/export.csv", { credentials: "same-origin" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "Unable to export reviews");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "advising-model-reviews.csv";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    showToast("Review export downloaded.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.exportButton.disabled =
      state.workspace?.progress.submitted_reviews !== state.workspace?.progress.total_reviews;
  }
}

async function signOut() {
  try {
    await api("/api/evaluation/session", { method: "DELETE" });
  } finally {
    state.csrf = null;
    state.reviewer = null;
    state.workspace = null;
    elements.app.classList.add("hidden");
    elements.summaryPanel.classList.add("hidden");
    elements.reviewerChip.textContent = "Not signed in";
    elements.reviewerName.value = "";
    elements.authDialog.showModal();
  }
}

function nextIncomplete() {
  const questions = state.workspace.questions;
  const offset = questions.findIndex((question, index) => index > state.currentIndex && !question.complete);
  if (offset >= 0) return navigateTo(offset);
  const first = questions.findIndex((question) => !question.complete);
  if (first >= 0) return navigateTo(first);
  showToast("All questions are complete.");
}

buildRatingScales();
elements.authDialog.addEventListener("cancel", (event) => event.preventDefault());
elements.authForm.addEventListener("submit", signIn);
document.querySelectorAll(".review-form").forEach((form) => form.addEventListener("submit", saveReview));
["A", "B"].forEach((candidate) => {
  document.getElementById(`comment-${candidate}`).addEventListener("input", () => updateSentenceCount(candidate));
});
document.querySelectorAll(".expand-answer").forEach((button) => {
  button.addEventListener("click", () => {
    const answer = document.getElementById(`answer-${button.dataset.candidate}`);
    const expanded = answer.classList.toggle("expanded");
    button.textContent = expanded ? "Collapse answer" : "Expand answer";
  });
});
elements.previousButton.addEventListener("click", () => navigateTo(state.currentIndex - 1));
elements.nextButton.addEventListener("click", () => navigateTo(state.currentIndex + 1));
elements.nextIncompleteButton.addEventListener("click", nextIncomplete);
elements.exportButton.addEventListener("click", exportCsv);
elements.logoutButton.addEventListener("click", signOut);

initializeSession();
