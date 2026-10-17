# Dynamic suggestion ranking

The chat still starts from the curated list in `data/question_suggestions.json`.
With the dynamic flag off, which is the default, nothing about that list
changes: the empty chat shows the same four starter questions, they hide
once a conversation starts, and the dropdown is a prefix and token match.
Selecting a suggestion fills the input. The student sends it themselves.

## Flag

`DYNAMIC_SUGGESTIONS_ENABLED` defaults off. `true`, `1`, `yes`, or `on`
turns the ranking on. Any other value, including unset, leaves the curated
behavior in place and does not write pick events.

`QUESTION_SUGGESTIONS_ENABLED` still controls whether the curated list is
loaded at all. `POPULAR_QUESTIONS_PUBLIC` is a different switch for
anonymized chat questions. This feature does not read those questions.

## What is stored

A pick is recorded only when a student clicks a starter chip or chooses a
dropdown row. The row stores:

- the curated suggestion id
- `chip` or `dropdown`
- the program selected in the composer (`isda`, `mlis`, or `both`)
- a full UTC timestamp

It does not store the characters the student typed, the question text, an
IP address, a user id, a conversation id, or a session id. One browser
counts a suggestion at most once per UTC day. The dedupe key is an HMAC of
the session, the suggestion id, and that day. The HMAC is not in the
reviewer export.

Rows are kept. There is no retention window, so the all-time ranking can
be compared later, including after several months.

`POST /api/question-suggestion-events` uses the same rate limiter as the
other API routes. The limit is `SUGGESTION_EVENT_RATE_LIMIT` calls per
`SUGGESTION_EVENT_RATE_WINDOW_SECONDS` for each client address (defaults
30 and 60).

## Ranking

`SUGGESTION_RANKING_MODE` defaults to `all_time`. That mode counts every
stored pick. No other mode is implemented. If the variable is set to
anything else, popularity stays off and the four curated starters remain.

`SUGGESTION_POPULARITY_MIN_COUNT` (default 3) is the number of counted
picks a suggestion needs before it can replace a starter or add a
popularity boost in the dropdown. The boost is capped so a strong prefix
match still wins over a weak match that happens to be popular.

Starter slots, up to four:

1. Entries marked `"pinned": true`, in catalog order. A pinned suggestion
   is forced into the starters even with no picks. Hidden entries are skipped.
2. Other suggestions at or above the minimum count, highest count first.
3. `fallback_starter_ids`, which is the original four starter questions,
   in that order.
4. The rest of the catalog, if a slot is still empty.

Hidden suggestions never occupy a starter slot and never receive a boost.
They can still appear in the dropdown as an ordinary curated match.

## Tuning the list

Edit `data/question_suggestions.json`.

- `id` is the stable key. Do not reuse an id for a different question.
  Use lowercase letters, numbers, and single hyphens, 2 to 64 characters.
- `text` is the question the student sees.
- `topic` is the small label.
- `programs` is `isda`, `mlis`, or `both`.
- `pinned: true` forces a starter.
- `fallback_starter_ids` is the ordered backup when there are not enough
  picks. A starter chip's question should stay in this file and in a topic
  button's `data-samples`.

## Reviewer moderation

The evaluation page, with the same sign-in as the rest of that workspace
(`EVAL_ACCESS_TOKEN`, `EVAL_ALLOWED_REVIEWERS`, or Easy Auth), shows the
all-time counts. A reviewer can hide a suggestion, restore it, or reset
its counts. Each action requires a short reason and is stored with the
reviewer's name and a timestamp.

`GET /api/evaluation/suggestion-events.csv` downloads the pick events:
timestamp, suggestion id, event type, and program.
