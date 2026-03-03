"""Document processing for RAG (Retrieval Augmented Generation)."""
import os
from typing import List, Optional
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, TextLoader, PyPDFLoader
from langchain_core.documents import Document
from langchain_community.embeddings import SentenceTransformerEmbeddings
from langchain_community.vectorstores import Chroma
from config import Config


class DocumentProcessor:
    """Handles loading and processing advising documents."""
    
    def __init__(self, docs_path: Optional[str] = None):
        """
        Initialize the document processor.
        
        Args:
            docs_path: Path to documents directory
        """
        self.docs_path = docs_path or "./data/advising_docs"
        self.embeddings = SentenceTransformerEmbeddings(model_name="all-MiniLM-L6-v2")
        self.vector_store = None
        self.loaded_documents: List[Document] = []
    
    def load_documents(self) -> List[Document]:
        """
        Load all documents from the docs directory.
        
        Supports .txt and .pdf files.
        
        Returns:
            List of loaded documents
        """
        if not os.path.exists(self.docs_path):
            print(f"Docs directory not found: {self.docs_path}")
            return []
        
        documents = []
        
        # Load text files
        try:
            txt_loader = DirectoryLoader(
                self.docs_path,
                glob="**/*.txt",
                loader_cls=TextLoader,
                recursive=True
            )
            documents.extend(txt_loader.load())
            print(f"Loaded {len([d for d in documents if d.metadata.get('source', '').endswith('.txt')])} text documents")
        except Exception as e:
            print(f"Error loading text files: {e}")
        
        # Load PDF files
        try:
            pdf_loader = DirectoryLoader(
                self.docs_path,
                glob="**/*.pdf",
                loader_cls=PyPDFLoader,
                recursive=True
            )
            documents.extend(pdf_loader.load())
            print(f"Loaded {len([d for d in documents if d.metadata.get('source', '').endswith('.pdf')])} PDF documents")
        except Exception as e:
            print(f"Error loading PDF files: {e}")
        
        self.loaded_documents = documents
        return documents
    
    def split_documents(self, documents: List[Document], 
                       chunk_size: int = 1000,
                       chunk_overlap: int = 200) -> List[Document]:
        """
        Split documents into chunks for better retrieval.
        
        Args:
            documents: Documents to split
            chunk_size: Size of each chunk
            chunk_overlap: Overlap between chunks
            
        Returns:
            List of split documents
        """
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", " ", ""]
        )
        
        split_docs = splitter.split_documents(documents)
        print(f"Split into {len(split_docs)} chunks")
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
