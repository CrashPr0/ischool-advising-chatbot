"""Document processing for RAG (Retrieval Augmented Generation)."""

try:
    __import__("pysqlite3")
    import sys

    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except ImportError:
    pass

import os
import hashlib
import json
import math
import re
import threading
from typing import List, Optional
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_core.documents import Document

try:
    from langchain_chroma import Chroma
except ImportError:  # pragma: no cover - compatibility with older installs
    from langchain_community.vectorstores import Chroma
import yaml
from langchain_core.embeddings import Embeddings

try:
    from .config import Config
except ImportError:  # pragma: no cover
    from config import Config


class HashingEmbeddings(Embeddings):
    """Small deterministic offline embedder for quota-free indexing.

    Feature hashing is lexical rather than generative, but it is a strong fit
    for a focused advising corpus: identical academic terms, acronyms, course
    codes, and URL text map consistently without downloading a model or making
    an external API call.
    """

    _STOPWORDS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "do", "does",
        "for", "from", "how", "i", "in", "is", "it", "many", "of", "on",
        "or", "that", "the", "there", "to", "what", "when", "where", "which",
        "who", "why", "with",
    }

    def __init__(self, dimensions: int = 2048):
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", text.casefold())
            if token not in self._STOPWORDS
        ]
        if "bsisda" in tokens:
            tokens.extend(["bs", "isda", "information", "science", "data", "analytics"])
        elif "isda" in tokens:
            tokens.extend(["information", "science", "data", "analytics"])
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimensions
            vector[index] += 1.0
        magnitude = math.sqrt(sum(component * component for component in vector))
        if magnitude:
            vector = [component / magnitude for component in vector]
        return vector

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text)


def _create_embeddings():
    """Create the embeddings instance based on available config."""
    provider = getattr(Config, "EMBEDDING_PROVIDER", "hashing").strip().lower()
    supported = {"auto", "hashing", "local", "azure_openai", "openai"}
    if provider not in supported:
        raise ValueError(
            f"Unsupported EMBEDDING_PROVIDER '{provider}'. "
            f"Choose one of: {', '.join(sorted(supported))}"
        )

    if provider == "hashing":
        return HashingEmbeddings()

    if provider == "local":
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

    azure_configured = bool(
        Config._configured_secret(Config.AZURE_OPENAI_API_KEY)
        and Config.AZURE_OPENAI_ENDPOINT
        and Config.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    )
    if provider in {"auto", "azure_openai"} and azure_configured:
        from langchain_openai import AzureOpenAIEmbeddings

        return AzureOpenAIEmbeddings(
            azure_deployment=Config.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_API_KEY,
            api_version=Config.AZURE_OPENAI_API_VERSION,
        )
    if provider == "azure_openai":
        raise ValueError(
            "EMBEDDING_PROVIDER=azure_openai requires AZURE_OPENAI_API_KEY, "
            "AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
        )

    openai_configured = Config._configured_secret(Config.OPENAI_API_KEY)
    if provider in {"auto", "openai"} and openai_configured:
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(api_key=Config.OPENAI_API_KEY)
    if provider == "openai":
        raise ValueError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")

    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")


def _first_existing_dir(candidates: list[str], fallback: str) -> str:
    for d in candidates:
        try:
            if d and os.path.isdir(d):
                return d
        except Exception:
            pass
    return fallback


DEFAULT_VECTOR_COLLECTION = "advising_documents"
NON_DOCUMENT_DIRECTORIES = {"meta", "scrape", "__pycache__"}


class DocumentProcessor:
    """Handles loading and processing advising documents."""

    def __init__(self, docs_path: Optional[str] = None):
        fallback = "./data/advising_docs"
        configured_docs_path = docs_path or getattr(Config, "DOCS_PATH", None)
        self._explicit_docs_path = bool(configured_docs_path)
        if self._explicit_docs_path:
            # An explicit path is authoritative, even when it does not exist
            # yet. Silently falling back to a different corpus makes a typo
            # dangerous during a production index rebuild.
            self.docs_path = str(configured_docs_path)
        else:
            release_docs = str(
                Path(__file__).resolve().parent.parent / "data" / "advising_docs"
            )
            self.docs_path = _first_existing_dir(
                [
                    release_docs,
                    os.path.join(os.getcwd(), "data", "advising_docs"),
                    "/home/site/wwwroot/data/advising_docs",
                    fallback,
                ],
                fallback,
            )
        self.docs_path = self._canonical_path(self.docs_path)
        self.embeddings = _create_embeddings()
        self.vector_store = None
        self.loaded_documents: List[Document] = []
        self.collection_name = os.getenv(
            "VECTOR_COLLECTION_NAME",
            getattr(Config, "VECTOR_COLLECTION_NAME", DEFAULT_VECTOR_COLLECTION),
        )
        self._index_lock = threading.RLock()

    @staticmethod
    def _canonical_path(path: str) -> str:
        """Return a stable absolute path for file and directory comparisons."""
        try:
            return str(Path(path).expanduser().resolve())
        except (OSError, RuntimeError, TypeError):
            return os.path.abspath(os.path.expanduser(str(path)))

    def _search_directories(self) -> List[str]:
        """Return existing, canonical document roots in deterministic order."""
        candidates = [self.docs_path]

        roots: List[str] = []
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            canonical = self._canonical_path(candidate)
            if canonical in seen or not os.path.isdir(canonical):
                continue
            seen.add(canonical)
            roots.append(canonical)
        return roots

    def scan_source_files(self) -> dict:
        """Count files on disk (pre-index), useful for debugging deployments."""
        roots = self._search_directories()
        txt = 0
        pdf = 0
        for root in roots:
            try:
                if not os.path.isdir(root):
                    continue
                txt += sum(
                    1
                    for path in Path(root).rglob("*.txt")
                    if self._is_document_path(path, root)
                )
                pdf += sum(
                    1
                    for path in Path(root).rglob("*.pdf")
                    if self._is_document_path(path, root)
                )
            except Exception:
                continue
        return {
            "roots": roots,
            "txt_files": txt,
            "pdf_files": pdf,
            "total_files": txt + pdf,
        }

    @staticmethod
    def _is_document_path(path: Path, root: str) -> bool:
        """Exclude crawler controls, metadata, caches, and other non-evidence."""
        try:
            relative = path.relative_to(root)
        except ValueError:
            return False
        return not any(
            part.casefold() in NON_DOCUMENT_DIRECTORIES for part in relative.parts[:-1]
        )

    def vector_store_count(self) -> Optional[int]:
        """Return number of indexed chunks in Chroma, if available."""
        with self._index_lock:
            try:
                if not self.vector_store:
                    self.load_vector_store()
                if not self.vector_store:
                    return None
                # Chroma's underlying collection supports count()
                collection = self.vector_store._collection  # type: ignore[attr-defined]
                return int(collection.count())
            except Exception:
                return None

    def _load_from_directory(self, directory: str) -> List[Document]:
        """Load .txt and .pdf files from a single directory."""
        if not os.path.isdir(directory):
            return []

        documents: List[Document] = []

        files = sorted(
            (
                path
                for path in Path(directory).rglob("*")
                if path.is_file()
                and path.suffix.casefold() in {".txt", ".pdf"}
                and self._is_document_path(path, directory)
            ),
            key=lambda path: path.relative_to(directory).as_posix().casefold(),
        )
        for path in files:
            # This legacy fixture describes the MLIS program, not the ISDA
            # program served by this application. Keep the fixture available
            # for explicitly requested local demos, but never index it as
            # advising evidence.
            if path.name == "degree_requirements.txt":
                try:
                    preview = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    preview = ""
                if "Master's of Library and Information Science (MLIS)" in preview:
                    print(f"Skipping legacy non-ISDA sample document: {path}")
                    continue

            source = self._canonical_path(str(path))
            try:
                if path.suffix.casefold() == ".txt":
                    try:
                        loader = TextLoader(
                            source, encoding="utf-8", autodetect_encoding=True
                        )
                    except TypeError:  # compatibility with older langchain versions
                        loader = TextLoader(source, encoding="utf-8")
                else:
                    loader = PyPDFLoader(source)

                for document in loader.load():
                    document.metadata = dict(document.metadata or {})
                    companion = self._load_companion_metadata(path, directory)
                    if companion:
                        document.metadata.update(companion)
                    document.metadata["source"] = source
                    documents.append(document)
            except Exception as e:
                print(f"Error loading {source}: {e}")

        return documents

    @staticmethod
    def _load_companion_metadata(path: Path, root: str) -> dict:
        """Load optional YAML metadata beside a document or under ``meta/``."""
        candidates = [
            path.with_suffix(".yml"),
            path.with_suffix(".yaml"),
            Path(root) / "meta" / f"{path.stem}.yml",
            Path(root) / "meta" / f"{path.stem}.yaml",
        ]
        for metadata_path in candidates:
            if not metadata_path.is_file():
                continue
            try:
                loaded = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError) as error:
                print(f"Error loading metadata {metadata_path}: {error}")
                return {}
            return loaded if isinstance(loaded, dict) else {}
        return {}

    def load_documents(self) -> List[Document]:
        """Load documents from all configured directories."""
        documents: List[Document] = []
        seen_documents = set()

        for directory in self._search_directories():
            loaded = self._load_from_directory(directory)
            if loaded:
                print(f"Loaded {len(loaded)} documents from {directory}")
                for document in loaded:
                    metadata = document.metadata or {}
                    identity = (
                        self._canonical_path(str(metadata.get("source", ""))),
                        str(metadata.get("page", "")),
                        document.page_content,
                    )
                    if identity in seen_documents:
                        continue
                    seen_documents.add(identity)
                    documents.append(document)

        if not documents:
            print("No documents found in any directory.")

        self.loaded_documents = documents
        return documents

    def split_documents(
        self, documents: List[Document], chunk_size: int = 600, chunk_overlap: int = 150
    ) -> List[Document]:
        """Split documents into chunks optimised for advising-style Q&A retrieval."""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        split_docs = splitter.split_documents(documents)
        print(
            f"Split into {len(split_docs)} chunks "
            f"(size={chunk_size}, overlap={chunk_overlap})"
        )
        return split_docs

    def _document_id(self, document: Document) -> str:
        """Generate a stable ID so a rebuild has deterministic chunk keys."""
        metadata = {
            str(key): str(value)
            for key, value in sorted(
                (document.metadata or {}).items(), key=lambda item: str(item[0])
            )
        }
        payload = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        payload = f"{payload}\0{document.page_content}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def create_vector_store(self, documents: List[Document]) -> Chroma:
        """Replace the advising collection with a deterministic document set."""
        if not documents:
            raise ValueError("Cannot create a vector store without documents")

        persist_directory = Config.VECTOR_STORE_PATH
        os.makedirs(persist_directory, exist_ok=True)

        try:
            previous = Chroma(
                collection_name=self.collection_name,
                persist_directory=persist_directory,
                embedding_function=self.embeddings,
            )
            previous.delete_collection()
        except Exception as e:
            print(f"No existing vector collection to replace: {e}")

        self.vector_store = Chroma(
            collection_name=self.collection_name,
            persist_directory=persist_directory,
            embedding_function=self.embeddings,
        )
        unique_documents = []
        unique_ids = []
        seen_ids = set()
        for document in documents:
            document_id = self._document_id(document)
            if document_id in seen_ids:
                continue
            seen_ids.add(document_id)
            unique_documents.append(document)
            unique_ids.append(document_id)
        self.vector_store.add_documents(unique_documents, ids=unique_ids)
        persist = getattr(self.vector_store, "persist", None)
        if callable(persist):
            persist()
        print(f"Vector store created at {persist_directory}")
        return self.vector_store

    def load_vector_store(self) -> Optional[Chroma]:
        """
        Load an existing vector store.

        Returns:
            Chroma vector store if it exists, None otherwise
        """
        with self._index_lock:
            persist_directory = Config.VECTOR_STORE_PATH

            if not os.path.exists(persist_directory):
                return None

            try:
                self.vector_store = Chroma(
                    collection_name=self.collection_name,
                    persist_directory=persist_directory,
                    embedding_function=self.embeddings,
                )
                print(f"Loaded vector store from {persist_directory}")
                return self.vector_store
            except Exception as e:
                print(f"Error loading vector store: {e}")
                return None

    def retrieve_relevant_docs(self, query: str, k: int = 3) -> List[Document]:
        """
        Retrieve relevant documents for a query.

        Args:
            query: Search query
            k: Number of documents to retrieve

        Returns:
            List of relevant documents
        """
        if not isinstance(query, str) or not query.strip():
            return []
        with self._index_lock:
            if not self.vector_store:
                self.load_vector_store()

            if not self.vector_store:
                return []

            try:
                k = max(1, min(int(k), 20))
                results = self.vector_store.similarity_search(query, k=k)
                return results
            except Exception as e:
                print(f"Error retrieving documents: {e}")
                return []

    def process_and_index(self) -> Optional[Chroma]:
        """Load, split, and atomically replace the advising index."""
        with self._index_lock:
            print(f"Processing documents from {self.docs_path}...")
            documents = self.load_documents()
            if not documents:
                print("No documents found. Proceeding without document retrieval.")
                return None
            split_docs = self.split_documents(documents)
            return self.create_vector_store(split_docs)


def setup_sample_docs() -> None:
    """Create the legacy demo fixture only when explicitly enabled.

    The fixture is intentionally not part of the production advising corpus;
    set ``ALLOW_SAMPLE_DOCS=true`` for a local demo only.
    """
    if os.getenv("ALLOW_SAMPLE_DOCS", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        print("Sample documents disabled; use official advising documents instead.")
        return

    docs_dir = os.getenv(
        "DOCS_PATH",
        str(Path(__file__).resolve().parent.parent / "data" / "advising_docs"),
    )
    os.makedirs(docs_dir, exist_ok=True)

    sample_content = """# iSchool Degree Requirements

## Master's of Library and Information Science (MLIS)

### Core Courses (36 units)
- INFO 200: Foundations of Information and Library Science
- INFO 203: Organization of Information
- INFO 210: Information Policy and Management
- INFO 211: Research Methods
- INFO 212: Knowledge Management
- INFO 213: Digital Information Services
- INFO 230: Advanced Information Architecture
- INFO 240: User Experience Research
- INFO 250: Capstone Project (6 units)

### Electives (18 units)
Students choose from:
- INFO 260: Digital Libraries
- INFO 265: Data Curation and Preservation
- INFO 270: Archives and Records Management
- INFO 275: Special Collections
- INFO 280: Information Organizations
- INFO 285: Information Law and Ethics

### Prerequisites
- Bachelor's degree from accredited institution
- Minimum 2.75 undergraduate GPA
- TOEFL (if international)

### Timeline
Most students complete MLIS in 2 years (full-time) or 3-4 years (part-time)

## Graduation Requirements

1. Complete all required core courses
2. Complete 18 units of electives
3. Maintain 3.0 GPA in graduate courses
4. Submit graduation petition in final semester
5. Complete capstone project with passing grade

## Course Scheduling Tips

- Take INFO 200 first (prerequisite for most courses)
- Spread out core courses across semesters
- Take 2-3 courses per semester for full-time students
- Part-time students can take 1-2 courses per semester
- Check prerequisites before registering

## Advising

Contact the iSchool Advising Office:
- Email: advising@sjsu.edu
- Phone: (408) 924-2470
- Office: Washington Square Hall, Room 108
- Hours: Monday-Friday, 9am-4pm
"""

    sample_file = os.path.join(docs_dir, "degree_requirements.txt")

    if not os.path.exists(sample_file):
        with open(sample_file, "w", encoding="utf-8") as f:
            f.write(sample_content)
        print(f"Created sample document: {sample_file}")
