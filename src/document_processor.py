"""Document processing for RAG (Retrieval Augmented Generation)."""
try:
    __import__("pysqlite3")
    import sys
    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except ImportError:
    pass

import os
from typing import List, Optional
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, TextLoader, PyPDFLoader
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
try:
    from .config import Config
except ImportError:  # pragma: no cover
    from config import Config


def _create_embeddings():
    """Create the embeddings instance based on available config."""
    if (Config.AZURE_OPENAI_API_KEY
            and Config.AZURE_OPENAI_ENDPOINT
            and Config.AZURE_OPENAI_EMBEDDING_DEPLOYMENT):
        from langchain_openai import AzureOpenAIEmbeddings
        return AzureOpenAIEmbeddings(
            azure_deployment=Config.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_API_KEY,
            api_version=Config.AZURE_OPENAI_API_VERSION,
        )
    if Config.OPENAI_API_KEY:
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(api_key=Config.OPENAI_API_KEY)
    from langchain_community.embeddings import SentenceTransformerEmbeddings
    return SentenceTransformerEmbeddings(model_name="all-MiniLM-L6-v2")


def _first_existing_dir(candidates: list[str], fallback: str) -> str:
    for d in candidates:
        try:
            if d and os.path.isdir(d):
                return d
        except Exception:
            pass
    return fallback


DOCS_DIRECTORIES = [
    "./data/advising_docs",
    "./docs",
    "/home/site/wwwroot/data/advising_docs",
    "/home/site/wwwroot/docs",
]


class DocumentProcessor:
    """Handles loading and processing advising documents."""

    def __init__(self, docs_path: Optional[str] = None):
        fallback = "./data/advising_docs"
        self.docs_path = _first_existing_dir(
            [
                docs_path,
                getattr(Config, "DOCS_PATH", None),
                "/home/site/wwwroot/data/advising_docs",
                os.path.join(os.getcwd(), "data", "advising_docs"),
                fallback,
            ],
            fallback,
        )
        self.embeddings = _create_embeddings()
        self.vector_store = None
        self.loaded_documents: List[Document] = []

    def scan_source_files(self) -> dict:
        """Count files on disk (pre-index), useful for debugging deployments."""
        roots = list(dict.fromkeys([self.docs_path] + DOCS_DIRECTORIES))
        txt = 0
        pdf = 0
        for root in roots:
            try:
                if not os.path.isdir(root):
                    continue
                txt += len(list(Path(root).rglob("*.txt")))
                pdf += len(list(Path(root).rglob("*.pdf")))
            except Exception:
                continue
        return {"roots": roots, "txt_files": txt, "pdf_files": pdf, "total_files": txt + pdf}

    def vector_store_count(self) -> Optional[int]:
        """Return number of indexed chunks in Chroma, if available."""
        try:
            if not self.vector_store:
                self.load_vector_store()
            if not self.vector_store:
                return None
            # Chroma's underlying collection supports count()
            return int(self.vector_store._collection.count())  # type: ignore[attr-defined]
        except Exception:
            return None

    def _load_from_directory(self, directory: str) -> List[Document]:
        """Load .txt and .pdf files from a single directory."""
        if not os.path.exists(directory):
            return []

        documents: List[Document] = []

        try:
            txt_loader = DirectoryLoader(
                directory, glob="**/*.txt",
                loader_cls=TextLoader, recursive=True,
            )
            documents.extend(txt_loader.load())
        except Exception as e:
            print(f"Error loading text files from {directory}: {e}")

        try:
            pdf_loader = DirectoryLoader(
                directory, glob="**/*.pdf",
                loader_cls=PyPDFLoader, recursive=True,
            )
            documents.extend(pdf_loader.load())
        except Exception as e:
            print(f"Error loading PDF files from {directory}: {e}")

        return documents

    def load_documents(self) -> List[Document]:
        """Load documents from all configured directories."""
        documents: List[Document] = []

        search_dirs = list(dict.fromkeys([self.docs_path] + DOCS_DIRECTORIES))

        for directory in search_dirs:
            loaded = self._load_from_directory(directory)
            if loaded:
                print(f"Loaded {len(loaded)} documents from {directory}")
                documents.extend(loaded)

        if not documents:
            print("No documents found in any directory.")

        self.loaded_documents = documents
        return documents
    
    def split_documents(self, documents: List[Document],
                       chunk_size: int = 600,
                       chunk_overlap: int = 150) -> List[Document]:
        """Split documents into chunks optimised for advising-style Q&A retrieval."""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        split_docs = splitter.split_documents(documents)
        print(f"Split into {len(split_docs)} chunks (size={chunk_size}, overlap={chunk_overlap})")
        return split_docs
    
    def create_vector_store(self, documents: List[Document]) -> Chroma:
        """
        Create a vector store from documents.
        
        Args:
            documents: Documents to index
            
        Returns:
            Chroma vector store instance
        """
        persist_directory = Config.VECTOR_STORE_PATH
        
        # Create vector store
        self.vector_store = Chroma.from_documents(
            documents=documents,
            embedding=self.embeddings,
            persist_directory=persist_directory
        )
        
        self.vector_store.persist()
        print(f"Vector store created at {persist_directory}")
        
        return self.vector_store
    
    def load_vector_store(self) -> Optional[Chroma]:
        """
        Load an existing vector store.
        
        Returns:
            Chroma vector store if it exists, None otherwise
        """
        persist_directory = Config.VECTOR_STORE_PATH
        
        if not os.path.exists(persist_directory):
            return None
        
        try:
            self.vector_store = Chroma(
                persist_directory=persist_directory,
                embedding_function=self.embeddings
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
        if not self.vector_store:
            self.load_vector_store()
        
        if not self.vector_store:
            return []
        
        try:
            results = self.vector_store.similarity_search(query, k=k)
            return results
        except Exception as e:
            print(f"Error retrieving documents: {e}")
            return []
    
    def process_and_index(self) -> Chroma:
        """
        Complete workflow: load, split, and index documents.
        
        Returns:
            Vector store instance
        """
        print(f"Processing documents from {self.docs_path}...")
        
        # Load documents
        documents = self.load_documents()
        
        if not documents:
            print("No documents found. Proceeding without document retrieval.")
            return None
        
        # Split documents
        split_docs = self.split_documents(documents)
        
        # Create vector store
        vector_store = self.create_vector_store(split_docs)
        
        return vector_store


def setup_sample_docs() -> None:
    """
    Create sample advising documents for testing.
    """
    docs_dir = "./data/advising_docs"
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
        with open(sample_file, 'w') as f:
            f.write(sample_content)
        print(f"Created sample document: {sample_file}")
