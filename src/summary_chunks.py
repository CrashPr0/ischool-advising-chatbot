"""Generated summary chunks for broad questions (a light RAPTOR).

``SUMMARY_CHUNKS`` defaults to off. When on, an index build adds two kinds
of extra chunk next to the ordinary 600-character pieces:

* level 1, one per long source page: a short summary of that page.
* level 2, one per topic cluster (``topic_clusters.page_topic``) and
  program: an overview built from the member pages' summaries.

A broad question ("what should I know before applying?") then has a few
chunks that each speak for many pages, instead of eight fragments of one.

Summaries are written by the configured chat provider
(``DEFAULT_LLM_PROVIDER`` through ``LLMFactory``). They are generated text,
not published policy: every summary carries the real ``source_url`` values it
covers so the answer cites the official pages, and the prompt context labels
it as a summary. Pinned pages never pick up a summary.

Results are cached in ``SUMMARY_CACHE_PATH`` keyed by a hash of the prompt
(page text, title, URL, prompt version). A rebuild calls the model only for
pages whose text changed. Any failure (no API key, 429, timeout) skips that
summary; the build still publishes the ordinary chunks. After three failures
in a row the build stops calling the model for the rest of the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from langchain_core.documents import Document

try:
    from .program_scope import metadata_programs
    from .topic_clusters import page_topic
except ImportError:  # pragma: no cover - src/ on sys.path
    from program_scope import metadata_programs
    from topic_clusters import page_topic

_TRUE_VALUES = {"1", "true", "yes", "on"}
# Bump when a prompt changes so cached summaries are regenerated.
SUMMARY_PROMPT_VERSION = "1"
SUMMARY_CACHE_FILENAME = "summary_cache.json"
# A page this short is already one or two chunks; its own text is the summary.
MIN_PAGE_CHARS_FOR_SUMMARY = 1500
# Bounds on prompt size. The largest saved page is about 22,000 characters.
MAX_PAGE_INPUT_CHARS = 12_000
MAX_TOPIC_INPUT_CHARS = 12_000
TOPIC_MEMBER_EXCERPT_CHARS = 700
MAX_SUMMARY_CHARS = 1_200
MAX_CONSECUTIVE_FAILURES = 3

PAGE_LEVEL = 1
TOPIC_LEVEL = 2

PAGE_PROMPT = """You are indexing an official San Jose State University advising page \
so a search system can find it for broad student questions.

Summarize the page in at most 5 sentences (under 700 characters). Name the \
program or office, the topics it covers, and any deadlines, requirements, \
forms, or contacts it states. Use only facts stated on the page. Do not give \
advice and do not add links. Plain text only.

Title: {title}
URL: {url}

Page text:
{text}

Summary:"""

TOPIC_PROMPT = """You are indexing official San Jose State University advising pages \
so a search system can find them for broad student questions.

Below are descriptions of {count} official pages about "{topic}" for \
{program}. Write an overview in at most 6 sentences (under 900 characters) of \
what a student should know on this topic and which page (by title) covers \
what. Use only facts stated below. Do not give advice and do not add links. \
Plain text only.

{pages}

Overview:"""

Summarizer = Callable[[str], str]


def summary_chunks_enabled() -> bool:
    """Read on each call. Unset or any value other than true/yes/on/1 is off."""
    return os.getenv("SUMMARY_CHUNKS", "false").strip().lower() in _TRUE_VALUES


def is_summary_metadata(metadata: Optional[dict]) -> bool:
    """True for a generated summary chunk (bool, int, or string after Chroma)."""
    if not isinstance(metadata, dict):
        return False
    value = metadata.get("is_summary")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_VALUES
    return False


def summary_source_urls(metadata: Optional[dict]) -> list[str]:
    """Every official page a summary covers, primary ``source_url`` first."""
    if not isinstance(metadata, dict):
        return []
    urls: list[str] = []
    primary = metadata.get("source_url")
    if isinstance(primary, str) and primary.strip():
        urls.append(primary.strip())
    raw = metadata.get("summary_source_urls")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = raw.split()
    if isinstance(raw, (list, tuple)):
        for value in raw:
            if isinstance(value, str) and value.strip() and value.strip() not in urls:
                urls.append(value.strip())
    return urls


def summary_cache_path() -> Path:
    """``SUMMARY_CACHE_PATH``, else beside (not inside) the vector store.

    ``build_index --clean`` deletes the vector-store directory; the cache
    sits next to it so a clean rebuild does not pay for summaries again.
    """
    configured = os.getenv("SUMMARY_CACHE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    try:
        from .config import Config
    except ImportError:  # pragma: no cover - src/ on sys.path
        from config import Config
    store = Path(Config.VECTOR_STORE_PATH).expanduser().resolve()
    return store.parent / SUMMARY_CACHE_FILENAME


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SummaryCache:
    """JSON file of summaries keyed by prompt hash.

    ``save`` keeps only entries read or written during this build, so text
    for deleted or changed pages does not accumulate.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else None
        self._entries: dict[str, dict] = {}
        self._used: set[str] = set()
        self.hits = 0
        self.misses = 0
        if self.path is not None:
            self._entries = self._read(self.path)

    @staticmethod
    def _read(path: Path) -> dict[str, dict]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, ValueError) as error:
            print(f"Summary cache unreadable, starting empty: {error}")
            return {}
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            return {}
        return {
            key: value
            for key, value in entries.items()
            if isinstance(value, dict) and isinstance(value.get("summary"), str)
        }

    def get(self, key: str) -> Optional[str]:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        self._used.add(key)
        return entry["summary"]

    def put(self, key: str, summary: str, kind: str) -> None:
        self._entries[key] = {
            "summary": summary,
            "kind": kind,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._used.add(key)

    def save(self) -> None:
        if self.path is None:
            return
        kept = {key: self._entries[key] for key in sorted(self._used)}
        payload = {"version": 1, "prompt_version": SUMMARY_PROMPT_VERSION, "entries": kept}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".summary_cache.", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=1)
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


def _message_text(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "").strip()


def default_summarizer() -> Summarizer:
    """Summarize with the configured chat provider.

    The client is created on first use, so a build without credentials
    fails on the first summary (and is skipped), not at import. A reasoning
    deployment that rejects temperature is retried once without it, as chat
    does.
    """
    try:
        from .llm_providers import LLMFactory, REASONING_RETRY
    except ImportError:  # pragma: no cover - src/ on sys.path
        from llm_providers import LLMFactory, REASONING_RETRY

    state: dict = {}

    def summarize(prompt: str) -> str:
        if "llm" not in state:
            state["llm"] = LLMFactory.create_llm()
        try:
            return _message_text(state["llm"].invoke(prompt))
        except Exception as error:
            text = str(error).casefold()
            if state.get("reasoning") or not (
                "temperature" in text or "max_completion_tokens" in text
            ):
                raise
            state["llm"] = LLMFactory.create_llm(temperature=REASONING_RETRY)
            state["reasoning"] = True
            return _message_text(state["llm"].invoke(prompt))

    return summarize


class _Generator:
    """Cache lookup, model call, and the consecutive-failure breaker."""

    def __init__(self, summarize: Summarizer, cache: SummaryCache):
        self.summarize = summarize
        self.cache = cache
        self.failures = 0
        self.calls = 0
        self.stopped = False

    def __call__(self, kind: str, prompt: str) -> tuple[str, str]:
        key = _sha256(f"{SUMMARY_PROMPT_VERSION}\0{kind}\0{prompt}")
        cached = self.cache.get(key)
        if cached is not None:
            return cached, key
        if self.stopped:
            return "", key
        self.calls += 1
        try:
            summary = " ".join(str(self.summarize(prompt) or "").split())
        except Exception as error:
            self.failures += 1
            print(f"Summary generation failed ({kind}): {type(error).__name__}: {error}")
            if self.failures >= MAX_CONSECUTIVE_FAILURES:
                print("Summary generation stopped after repeated failures.")
                self.stopped = True
            return "", key
        self.failures = 0
        summary = summary[:MAX_SUMMARY_CHARS].strip()
        if summary:
            self.cache.put(key, summary, kind)
        return summary, key


def _group_pages(documents: Iterable[Document]) -> "OrderedDict[str, dict]":
    """Join loaded documents (PDF pages) into one entry per source page."""
    pages: "OrderedDict[str, dict]" = OrderedDict()
    for document in documents:
        metadata = dict(document.metadata or {})
        url = metadata.get("source_url")
        if not isinstance(url, str) or not url.strip():
            continue
        key = url.strip()
        entry = pages.setdefault(key, {"metadata": metadata, "parts": []})
        entry["parts"].append(document.page_content or "")
    for entry in pages.values():
        entry["text"] = "\n\n".join(part.strip() for part in entry["parts"] if part.strip())
    return pages


def _base_metadata(page_meta: dict) -> dict:
    programs = sorted(metadata_programs(page_meta))
    metadata = {
        "is_summary": True,
        "source_type": "summary",
        "summary_prompt_version": SUMMARY_PROMPT_VERSION,
    }
    if programs:
        metadata["programs"] = programs
    return metadata


def build_summary_documents(
    documents: Iterable[Document],
    summarize: Optional[Summarizer] = None,
    cache: Optional[SummaryCache] = None,
) -> list[Document]:
    """Return page and topic summary chunks for loaded (unsplit) documents."""
    pages = _group_pages(documents)
    if not pages:
        return []
    generator = _Generator(summarize or default_summarizer(), cache or SummaryCache())
    output: list[Document] = []
    descriptions: dict[str, str] = {}

    for url, entry in pages.items():
        meta = entry["metadata"]
        text = entry["text"]
        title = str(meta.get("title") or url).strip()
        topic = page_topic(meta)
        if len(text) < MIN_PAGE_CHARS_FOR_SUMMARY:
            descriptions[url] = text[:TOPIC_MEMBER_EXCERPT_CHARS]
            continue
        prompt = PAGE_PROMPT.format(title=title, url=url, text=text[:MAX_PAGE_INPUT_CHARS])
        summary, key = generator("page", prompt)
        if not summary:
            descriptions[url] = text[:TOPIC_MEMBER_EXCERPT_CHARS]
            continue
        descriptions[url] = summary
        metadata = _base_metadata(meta)
        metadata.update(
            {
                "title": f"Summary: {title}",
                "source": str(meta.get("source") or url),
                "source_url": url,
                "summary_source_urls": [url],
                "summary_level": PAGE_LEVEL,
                "summary_page_count": 1,
                "summary_key": key,
                "topic_cluster": topic,
            }
        )
        output.append(Document(page_content=summary, metadata=metadata))

    clusters: "OrderedDict[tuple[str, str], list[str]]" = OrderedDict()
    for url, entry in pages.items():
        meta = entry["metadata"]
        topic = page_topic(meta)
        for program in sorted(metadata_programs(meta)) or ["ALL"]:
            clusters.setdefault((topic, program), []).append(url)

    for (topic, program), urls in clusters.items():
        if len(urls) < 2:
            continue
        # Longest page first: it anchors the cluster's primary source_url.
        urls = sorted(urls, key=lambda url: (-len(pages[url]["text"]), url))
        blocks: list[str] = []
        used = 0
        for url in urls:
            title = str(pages[url]["metadata"].get("title") or url).strip()
            block = f"Page: {title}\nURL: {url}\n{descriptions.get(url, '')}"
            if used + len(block) > MAX_TOPIC_INPUT_CHARS:
                break
            blocks.append(block)
            used += len(block)
        program_label = "all programs" if program == "ALL" else program
        prompt = TOPIC_PROMPT.format(
            count=len(blocks),
            topic=topic,
            program=program_label,
            pages="\n\n".join(blocks),
        )
        summary, key = generator("topic", prompt)
        if not summary:
            continue
        metadata = {
            "is_summary": True,
            "source_type": "summary",
            "summary_prompt_version": SUMMARY_PROMPT_VERSION,
            "title": f"Topic overview: {topic} ({program_label})",
            "source": f"summary:{topic}:{program.casefold()}",
            "source_url": urls[0],
            "summary_source_urls": urls,
            "summary_level": TOPIC_LEVEL,
            "summary_page_count": len(urls),
            "summary_key": key,
            "topic_cluster": topic,
        }
        if program != "ALL":
            metadata["programs"] = [program]
        output.append(Document(page_content=summary, metadata=metadata))

    print(
        f"Summary chunks: {len(output)} "
        f"(cache hits {generator.cache.hits}, model calls {generator.calls})"
    )
    return output


def optional_summary_documents(
    documents: Iterable[Document],
    summarize: Optional[Summarizer] = None,
    cache_path: Optional[Path] = None,
) -> list[Document]:
    """Summary chunks when ``SUMMARY_CHUNKS`` is on; [] when off or on any error."""
    if not summary_chunks_enabled():
        return []
    try:
        cache = SummaryCache(cache_path if cache_path is not None else summary_cache_path())
        summaries = build_summary_documents(documents, summarize=summarize, cache=cache)
        try:
            cache.save()
        except Exception as error:
            print(f"Summary cache not saved: {error}")
        return summaries
    except Exception as error:
        print(f"Summary chunks skipped: {type(error).__name__}: {error}")
        return []
