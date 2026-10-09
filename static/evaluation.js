const DIMENSIONS = [
  { key: "accuracy", low: "Inaccurate", high: "Accurate" },
  { key: "tone", low: "Cold or dismissive", high: "Warm and supportive" },
  { key: "succinctness", low: "Too long or too brief", high: "Appropriately succinct" },
  { key: "edge_cases", low: "Mishandled", high: "Handled with care" }
];

const DIMENSION_COLORS = {
  accuracy: "#0055a2",
  tone: "#0874d1",
  succinctness: "#e5a823",
  edge_cases: "#16704a"
};

function emptyScores() {
  return { accuracy: null, tone: null, succinctness: null, edge_cases: null };
}

const state = {
  csrf: null,
  reviewer: null,
  workspace: null,
  currentIndex: 0,
  selectedScores: { A: emptyScores(), B: emptyScores() }
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
  questionCategory: document.getElementById("question-category"),
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
  toast: document.getElementById("toast"),
  issueReports: document.getElementById("issue-reports"),
  issueReportsList: document.getElementById("issue-reports-list"),
  issueReportsStatus: document.getElementById("issue-reports-status"),
  savedChats: document.getElementById("saved-chats"),
  savedChatsList: document.getElementById("saved-chats-list"),
  savedChatsStatus: document.getElementById("saved-chats-status"),
  refreshSavedChatsButton: document.getElementById("refresh-saved-chats-button"),
  refreshReportsButton: document.getElementById("refresh-reports-button")
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
    DIMENSIONS.forEach((dimension) => {
      const container = document.getElementById(`${dimension.key}-${candidate}`);
      for (let score = 1; score <= 5; score += 1) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "rating-option";
        button.dataset.score = String(score);
        button.textContent = String(score);
        button.setAttribute("role", "radio");
        button.setAttribute("aria-checked", "false");
        button.title = score === 1 ? dimension.low : score === 5 ? dimension.high : `Score ${score}`;
        button.addEventListener("click", () => selectScore(candidate, dimension.key, score));
        container.appendChild(button);
      }
    });
  });
}

function selectScore(candidate, dimension, score) {
  state.selectedScores[candidate][dimension] = score;
  document.querySelectorAll(`#${dimension}-${candidate} .rating-option`).forEach((button) => {
    const selected = score != null && Number(button.dataset.score) === Number(score);
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
  if (elements.savedChats) {
    elements.savedChats.classList.remove("hidden");
    loadSavedChats();
  }
  if (elements.authDialog.open) elements.authDialog.close();
  if (elements.issueReports) {
    elements.issueReports.classList.remove("hidden");
    loadIssueReports();
  }
}

function appendLabeledText(parent, label, value) {
  const block = document.createElement("p");
  const caption = document.createElement("span");
  caption.className = "issue-report__label";
  caption.textContent = label;
  const body = document.createElement("span");
  body.className = "issue-report__answer";
  body.textContent = value || "—";
  block.append(caption, body);
  parent.appendChild(block);
}

function renderIssueReports(reports) {
  const list = elements.issueReportsList;
  if (!list) return;
  list.replaceChildren();
  if (!reports.length) {
    const empty = document.createElement("p");
    empty.textContent = "No student reports yet.";
    list.appendChild(empty);
    return;
  }
  reports.forEach((report) => {
    const article = document.createElement("article");
    article.className = "issue-report";
    const heading = document.createElement("h3");
    heading.textContent = report.reason_label || report.reason || "Report";
    article.appendChild(heading);

    const meta = document.createElement("div");
    meta.className = "issue-report__meta";
    const when = document.createElement("span");
    when.textContent = formatDate(report.created_at);
    const scope = document.createElement("span");
    scope.textContent = report.include_conversation ? "Conversation included" : "Answer only";
    meta.append(when, scope);
    if (report.include_conversation && (report.provider || report.model)) {
      const model = document.createElement("span");
      model.textContent = [report.provider, report.model].filter(Boolean).join(" · ");
      meta.appendChild(model);
    }
    if (report.conversation_id) {
      const conversation = document.createElement("span");
      conversation.textContent = `Conversation ${report.conversation_id}`;
      meta.appendChild(conversation);
    }
    article.appendChild(meta);

    appendLabeledText(article, "Question", report.question);
    appendLabeledText(article, "Answer", report.answer);
    if (report.comment) appendLabeledText(article, "Comment", report.comment);
    if (report.include_conversation && Array.isArray(report.citations) && report.citations.length) {
      appendLabeledText(article, "Links in the answer", report.citations.join("\n"));
    }
    if (report.include_conversation && Array.isArray(report.turns) && report.turns.length) {
      const transcript = document.createElement("div");
      const caption = document.createElement("span");
      caption.className = "issue-report__label";
      caption.textContent = "Conversation";
      transcript.appendChild(caption);
      report.turns.forEach((turn) => {
        const line = document.createElement("p");
        line.className = "issue-report__turn";
        const who = turn.role === "user" ? "Student" : "Assistant";
        line.textContent = `${who}: ${turn.text || ""}`;
        transcript.appendChild(line);
      });
      article.appendChild(transcript);
    }
    list.appendChild(article);
  });
}

async function loadIssueReports() {
  if (!elements.issueReports || !state.reviewer) return;
  if (elements.issueReportsStatus) elements.issueReportsStatus.textContent = "Loading reports…";
  try {
    const data = await api("/api/evaluation/issue-reports");
    renderIssueReports(Array.isArray(data.reports) ? data.reports : []);
    if (elements.issueReportsStatus) elements.issueReportsStatus.textContent = "";
  } catch (error) {
    if (elements.issueReportsStatus) {
      elements.issueReportsStatus.textContent = error.message || "Unable to load reports.";
    }
  }
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
    button.className = `question-nav-item${question.complete ? " complete" : ""}${question.needs_rescore ? " legacy" : ""}${index === state.currentIndex ? " active" : ""}`;
    button.setAttribute("aria-current", index === state.currentIndex ? "true" : "false");

    const number = document.createElement("span");
    number.className = "question-nav-item__number";
    number.textContent = String(index + 1).padStart(2, "0");
    const copy = document.createElement("span");
    copy.className = "question-nav-item__copy";
    const title = document.createElement("span");
    title.className = "question-nav-item__title";
    title.textContent = question.question;
    const category = document.createElement("span");
    category.className = "question-nav-item__category";
    category.textContent = question.category || "";
    if (!question.category) category.classList.add("hidden");
    copy.append(title, category);
    const status = document.createElement("span");
    status.className = "question-nav-item__state";
    const navLabel = !question.complete
      ? "Incomplete"
      : question.needs_rescore
        ? "Saved on the previous 1 to 7 rubric"
        : "Complete";
    status.setAttribute("aria-label", navLabel);
    button.append(number, copy, status);
    button.addEventListener("click", () => navigateTo(index));
    elements.questionList.appendChild(button);
  });
}

function renderCurrentQuestion() {
  const questions = state.workspace.questions;
  const question = questions[state.currentIndex];
  if (!question) return;
  elements.questionNumber.textContent = `Question ${state.currentIndex + 1} · ${question.id}`;
  elements.questionCategory.textContent = question.category || "";
  elements.questionCategory.classList.toggle("hidden", !question.category);
  elements.questionText.textContent = question.question;
  const statusLabel = !question.complete
    ? "Review in progress"
    : question.needs_rescore
      ? "Previous 1–7 score"
      : "Review complete";
  elements.questionStatus.textContent = statusLabel;
  elements.questionStatus.classList.toggle("complete", question.complete && !question.needs_rescore);
  elements.questionStatus.classList.toggle("legacy", Boolean(question.needs_rescore));
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
  const dimensional = review && review.rubric !== "legacy";
  DIMENSIONS.forEach((dimension) => {
    const value = dimensional ? review[dimension.key] : null;
    selectScore(candidate, dimension.key, value);
  });
  const legacy = document.getElementById(`legacy-${candidate}`);
  if (review?.rubric === "legacy") {
    legacy.textContent = `Saved under the previous overall score of ${review.rating}/7. Choose the four dimension scores to replace it. The short note can stay or be rewritten.`;
    legacy.classList.remove("hidden");
  } else {
    legacy.textContent = "";
    legacy.classList.add("hidden");
  }
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
  const scores = state.selectedScores[candidate];
  const comment = document.getElementById(`comment-${candidate}`).value.trim();
  const errorElement = document.getElementById(`error-${candidate}`);
  const sentences = countSentences(comment);
  errorElement.textContent = "";
  if (DIMENSIONS.some((dimension) => !scores[dimension.key])) {
    errorElement.textContent = "Score every dimension from 1 to 5.";
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
      body: JSON.stringify({ ...scores, comment })
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
      const count = document.createElement("p");
      count.className = "model-result__count";
      count.textContent = `${model.dimension_review_count} dimension review${model.dimension_review_count === 1 ? "" : "s"} · averages out of 5`;
      const list = document.createElement("ul");
      list.className = "dimension-list";
      (summary.dimension_definitions || []).forEach((dimension) => {
        const item = document.createElement("li");
        const name = document.createElement("span");
        name.textContent = dimension.label;
        const value = document.createElement("strong");
        const average = model.dimensions?.[dimension.key]?.average;
        value.textContent = average == null ? "—" : String(average);
        item.append(name, value);
        list.appendChild(item);
      });
      card.append(provider, heading, count, list);
      if (model.legacy_review_count) {
        const note = document.createElement("p");
        note.className = "legacy-note";
        const average = model.legacy?.average_rating;
        note.textContent = `${model.legacy_review_count} review${model.legacy_review_count === 1 ? "" : "s"} used the previous 1–7 overall score${average == null ? "" : ` (average ${average})`}.`;
        card.appendChild(note);
      }
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
    if (elements.savedChats) elements.savedChats.classList.add("hidden");
    if (elements.savedChatsList) elements.savedChatsList.replaceChildren();
    if (elements.savedChatsStatus) elements.savedChatsStatus.textContent = "";
    elements.app.classList.add("hidden");
    elements.summaryPanel.classList.add("hidden");
    if (elements.issueReports) elements.issueReports.classList.add("hidden");
    if (elements.issueReportsList) elements.issueReportsList.replaceChildren();
    if (elements.issueReportsStatus) elements.issueReportsStatus.textContent = "";
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
if (elements.refreshReportsButton) {
  elements.refreshReportsButton.addEventListener("click", () => loadIssueReports());
}

initializeSession();

const showResultsButton = document.getElementById("show-results-button");
const resultsChartContainer = document.getElementById("results-chart-container");
const resultsCanvas = document.getElementById("results-chart");

let resultsChart = null;

const leftDistributionCanvas = document.getElementById("left-distribution-chart");
const rightDistributionCanvas = document.getElementById("right-distribution-chart");
const leftDistributionTitle = document.getElementById("left-distribution-title");
const rightDistributionTitle = document.getElementById("right-distribution-title");

let leftDistributionChart = null;
let rightDistributionChart = null;

const resultsChartTitle = document.getElementById("results-chart-title");
const legacyResultsNote = document.getElementById("legacy-results-note");

showResultsButton.addEventListener("click", async () => {
  const isOpening = resultsChartContainer.classList.contains("hidden");
  resultsChartContainer.classList.toggle("hidden", !isOpening);
  showResultsButton.setAttribute("aria-expanded", String(isOpening));
  if (isOpening) {
    await loadResultsChart();
  }
});

function destroyChart(chart) {
  if (chart) chart.destroy();
  return null;
}

async function loadResultsChart() {
  try {
    const response = await fetch("/api/evaluation/summary", {
      credentials: "same-origin"
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Unable to load evaluation summary");
    }
    if (!data.available) {
      throw new Error(data.message || "Complete all reviews before viewing results.");
    }

    const leftModel = data.models.left;
    const rightModel = data.models.right;
    const legacyCount = Object.values(data.models).reduce(
      (sum, model) => sum + (model.legacy_review_count || 0),
      0
    );
    const hasDimensionReviews = Object.values(data.models).some(
      (model) => model.dimension_review_count > 0
    );

    resultsChart = destroyChart(resultsChart);
    leftDistributionChart = destroyChart(leftDistributionChart);
    rightDistributionChart = destroyChart(rightDistributionChart);

    if (!hasDimensionReviews) {
      resultsChartTitle.textContent = "Previous overall score";
      legacyResultsNote.textContent = "These reviews used the previous 1–7 overall score. Dimension charts appear after responses are re-scored on the four-part rubric.";
      legacyResultsNote.classList.remove("hidden");
      resultsChart = createAverageChart(
        [leftModel.label, rightModel.label],
        [
          {
            label: "Average overall score",
            data: [
              leftModel.legacy?.average_rating,
              rightModel.legacy?.average_rating
            ],
            backgroundColor: ["#0055a2", "#e57200"],
            borderRadius: 8
          }
        ],
        "Previous overall score (1–7)",
        7
      );
      createLegacyDistributionChart(leftModel, leftDistributionCanvas, leftDistributionTitle, "left");
      createLegacyDistributionChart(rightModel, rightDistributionCanvas, rightDistributionTitle, "right");
      return;
    }

    resultsChartTitle.textContent = "Model results by dimension";
    if (legacyCount) {
      legacyResultsNote.textContent = `${legacyCount} review${legacyCount === 1 ? "" : "s"} used the previous 1–7 overall score and ${legacyCount === 1 ? "is" : "are"} excluded from the dimension charts.`;
      legacyResultsNote.classList.remove("hidden");
    } else {
      legacyResultsNote.textContent = "";
      legacyResultsNote.classList.add("hidden");
    }

    const definitions = data.dimension_definitions || [];
    resultsChart = createAverageChart(
      definitions.map((dimension) => dimension.label),
      [
        {
          label: leftModel.label,
          data: definitions.map((dimension) => leftModel.dimensions[dimension.key].average),
          backgroundColor: "#0055a2",
          borderRadius: 8
        },
        {
          label: rightModel.label,
          data: definitions.map((dimension) => rightModel.dimensions[dimension.key].average),
          backgroundColor: "#e57200",
          borderRadius: 8
        }
      ],
      "Average score by dimension",
      data.scale?.max || 5
    );
    createDimensionDistributionChart(
      leftModel,
      definitions,
      leftDistributionCanvas,
      leftDistributionTitle,
      "left"
    );
    createDimensionDistributionChart(
      rightModel,
      definitions,
      rightDistributionCanvas,
      rightDistributionTitle,
      "right"
    );
  } catch (error) {
    resultsChartContainer.classList.add("hidden");
    showResultsButton.setAttribute("aria-expanded", "false");
    showToast(error.message, true);
  }
}

function createAverageChart(labels, datasets, title, maximum) {
  return new Chart(resultsCanvas, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: {
        legend: { display: datasets.length > 1 },
        title: { display: true, text: title }
      },
      scales: {
        y: {
          min: 0,
          max: maximum,
          ticks: { stepSize: 1 },
          title: { display: true, text: "Average score" }
        }
      }
    }
  });
}

function storeDistributionChart(side, chart) {
  if (side === "left") leftDistributionChart = chart;
  else rightDistributionChart = chart;
}

function createLegacyDistributionChart(model, canvas, titleElement, side) {
  titleElement.textContent = `${model.label} previous overall-score distribution`;
  const labels = ["1", "2", "3", "4", "5", "6", "7"];
  storeDistributionChart(side, new Chart(canvas, {
    type: "bar",
    data: {
      labels: labels.map((rating) => `Rating ${rating}`),
      datasets: [
        {
          label: "Number of reviews",
          data: labels.map((rating) => model.legacy?.distribution?.[rating] || 0),
          backgroundColor: side === "left" ? "#0055a2" : "#e57200",
          borderRadius: 6
        }
      ]
    },
    options: {
      responsive: true,
      scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
    }
  }));
}

function createDimensionDistributionChart(model, definitions, canvas, titleElement, side) {
  titleElement.textContent = `${model.label} dimension distribution`;
  const labels = ["1", "2", "3", "4", "5"];
  storeDistributionChart(side, new Chart(canvas, {
    type: "bar",
    data: {
      labels: labels.map((score) => `Score ${score}`),
      datasets: definitions.map((dimension) => ({
        label: dimension.label,
        data: labels.map((score) => model.dimensions[dimension.key].distribution[score] || 0),
        backgroundColor: DIMENSION_COLORS[dimension.key] || "#0055a2",
        borderRadius: 6
      }))
    },
    options: {
      responsive: true,
      scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
    }
  }));
}

async function loadSavedChats() {
  if (!elements.savedChats || !state.reviewer) return;
  elements.savedChatsStatus.textContent = "Loading saved conversations…";
  try {
    const data = await api("/api/evaluation/saved-chats");
    elements.savedChatsList.replaceChildren();
    const chats = Array.isArray(data.chats) ? data.chats : [];
    elements.savedChatsStatus.textContent = chats.length ? "" : "No saved conversations yet.";
    chats.forEach((chat) => {
      const article = document.createElement("article");
      article.className = "issue-report saved-chat";
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      const turns = Array.isArray(chat.transcript) ? chat.transcript : [];
      summary.textContent = `${formatDate(chat.updated_at)} · ${turns.length} messages · View transcript`;
      details.appendChild(summary);
      turns.forEach((turn) => {
        const who = turn.role === "user" ? "Student" : "Assistant";
        appendLabeledText(details, `${who} · ${formatDate(turn.time)}`, turn.content);
      });
      article.appendChild(details);
      elements.savedChatsList.appendChild(article);
    });
  } catch (error) {
    elements.savedChatsStatus.textContent = error.message || "Unable to load saved conversations.";
  }
}

if (elements.refreshSavedChatsButton) {
  elements.refreshSavedChatsButton.addEventListener("click", () => loadSavedChats());
}
