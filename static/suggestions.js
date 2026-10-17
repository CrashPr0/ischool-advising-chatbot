// Client-side matcher for curated advising questions.
// Keep the scoring in sync with src/question_suggestions.py.
// The typed text stays in the browser. Nothing here sends or logs it.

(function (root) {
  var TRUE_PROGRAMS = { isda: true, mlis: true, both: true };
  var DEFAULT_LIMIT = 6;
  var MAX_LIMIT = 8;

  function normalizeSuggestionText(value) {
    if (typeof value !== "string") return "";
    var text = value.replace(/[’‘`]/g, "'").toLowerCase();
    text = text.replace(/[^a-z0-9]+/g, " ");
    text = text.replace(/([a-z])(\d)/g, "$1 $2");
    text = text.replace(/(\d)([a-z])/g, "$1 $2");
    return text.replace(/\s+/g, " ").trim();
  }

  function stemToken(token) {
    if (
      token.length >= 5 &&
      token.charAt(token.length - 1) === "s" &&
      !token.endsWith("ss") &&
      !/\d/.test(token.charAt(token.length - 2))
    ) {
      return token.slice(0, -1);
    }
    return token;
  }

  function tokensMatch(queryToken, candidateToken) {
    queryToken = stemToken(queryToken);
    candidateToken = stemToken(candidateToken);
    if (queryToken === candidateToken) return true;
    if (queryToken.length >= 3 && candidateToken.indexOf(queryToken) === 0) return true;
    if (candidateToken.length >= 4 && queryToken.indexOf(candidateToken) === 0) return true;
    var shared = 0;
    var length = Math.min(queryToken.length, candidateToken.length);
    for (var index = 0; index < length; index += 1) {
      if (queryToken.charAt(index) !== candidateToken.charAt(index)) break;
      shared += 1;
    }
    return shared >= 5 && length >= 5;
  }

  function matchScore(query, question) {
    var normalizedQuery = normalizeSuggestionText(query);
    var normalizedQuestion = normalizeSuggestionText(question);
    if (normalizedQuery.length < 2 || !normalizedQuestion) return 0;
    if (normalizedQuery === normalizedQuestion) return 1000;

    var queryTokens = normalizedQuery.split(" ");
    var questionTokens = normalizedQuestion.split(" ");
    var matched = 0;
    var longMatch = false;
    queryTokens.forEach(function (token) {
      var found = questionTokens.some(function (candidate) {
        return tokensMatch(token, candidate);
      });
      if (found) {
        matched += 1;
        if (stemToken(token).length >= 4) longMatch = true;
      }
    });
    var coverageComplete = matched === queryTokens.length;
    var prefix = normalizedQuestion.indexOf(normalizedQuery) === 0;
    var substring = normalizedQuestion.indexOf(normalizedQuery) !== -1;
    if (!prefix && !substring && (!coverageComplete || !longMatch)) return 0;

    var score = Math.floor((matched * 100) / queryTokens.length);
    if (coverageComplete) score += 40;
    if (prefix) {
      score += 240;
    } else if (substring) {
      var position = normalizedQuestion.indexOf(normalizedQuery);
      score += 140 + Math.max(0, 15 - position);
    }
    return score;
  }

  function programAdjustment(programs, selected) {
    if (
      selected === "both" ||
      programs.indexOf("both") !== -1 ||
      programs.indexOf(selected) !== -1
    ) {
      return 8;
    }
    return -12;
  }

  function rankQuestionSuggestions(query, questions, options) {
    var settings = options || {};
    var selected = TRUE_PROGRAMS[settings.program] ? settings.program : "both";
    var capped = Number(settings.limit);
    if (!isFinite(capped)) capped = DEFAULT_LIMIT;
    capped = Math.min(MAX_LIMIT, Math.max(1, Math.floor(capped)));
    var ranked = [];
    var seen = {};
    (questions || []).forEach(function (item) {
      if (!item || typeof item.text !== "string" || !item.text.trim()) return;
      var key = normalizeSuggestionText(item.text);
      if (!key || seen[key]) return;
      var score = matchScore(query, item.text);
      if (score <= 0) return;
      var programs = Array.isArray(item.programs) ? item.programs.map(String) : ["both"];
      score += programAdjustment(programs, selected);
      if (score <= 0) return;
      seen[key] = true;
      ranked.push({ score: score, text: item.text, item: item });
    });
    ranked.sort(function (left, right) {
      if (right.score !== left.score) return right.score - left.score;
      var leftText = left.text.toLowerCase();
      var rightText = right.text.toLowerCase();
      if (leftText < rightText) return -1;
      if (leftText > rightText) return 1;
      return 0;
    });
    return ranked.slice(0, capped).map(function (row) { return row.item; });
  }

  // Keyboard actions for the combobox. "none" lets the input keep its default.
  function suggestionKeyAction(key, state) {
    var current = state || {};
    if (current.isComposing) return { type: "none" };
    var count = current.count || 0;
    var open = Boolean(current.open);
    var activeIndex = typeof current.activeIndex === "number" ? current.activeIndex : -1;
    if (key === "Escape") return open ? { type: "close" } : { type: "none" };
    if (key === "ArrowDown") {
      if (!count) return { type: "none" };
      var next = open ? Math.min(count - 1, activeIndex + 1) : 0;
      return { type: "move", index: next };
    }
    if (key === "ArrowUp") {
      if (!open || !count) return { type: "none" };
      return { type: "move", index: Math.max(0, activeIndex - 1) };
    }
    if ((key === "Enter" || key === "Tab") && open && activeIndex >= 0 && activeIndex < count) {
      return { type: "select", index: activeIndex };
    }
    return { type: "none" };
  }

  var targets = [root];
  if (typeof window !== "undefined" && targets.indexOf(window) === -1) targets.push(window);
  targets.forEach(function (target) {
    target.normalizeSuggestionText = normalizeSuggestionText;
    target.rankQuestionSuggestions = rankQuestionSuggestions;
    target.suggestionKeyAction = suggestionKeyAction;
    target.SUGGESTION_DEBOUNCE_MS = 120;
  });
})(typeof globalThis !== "undefined" ? globalThis : this);
