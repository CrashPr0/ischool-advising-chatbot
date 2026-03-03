"""Example usage of the RAG Advising Chatbot."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from rag_chatbot import RAGAdvisingChatbot
from document_processor import setup_sample_docs


def main():
    """Demonstrate RAG chatbot usage."""
    
    print("=" * 70)
    print("RAG Advising Chatbot - Example Usage")
    print("=" * 70)
    print()
    
    # Initialize chatbot
    print("1. Initializing chatbot...")
    try:
        chatbot = RAGAdvisingChatbot()
        print("   ✓ Chatbot ready!\n")
    except Exception as e:
        print(f"   ✗ Error: {e}\n")
        return
    
    # Test knowledge base (hardcoded answers)
    print("2. Testing Knowledge Base (hardcoded answers)...")
    print("-" * 70)
    
    test_questions = [
        "what do i need to graduate",
        "how many units do i need",
        "hello"
    ]
    
    for question in test_questions:
        result = chatbot.chat(question)
        print(f"Q: {question}")
        print(f"Source: {result['source']} (Confidence: {result['confidence']:.1%})")
        print(f"A: {result['response'][:150]}...")
        print()
    
    # Clear for next test
    chatbot.clear_history()
    
    # Test with documents
    print("\n3. Testing Document Retrieval (RAG)...")
    print("-" * 70)
    
    # Setup sample documents
    print("Creating sample documents...")
    setup_sample_docs()
    
    print("Processing documents...")
    chatbot.process_documents("./data/advising_docs")
    
    # Ask question that uses documents
    question = "Tell me about the MLIS program requirements"
    print(f"\nQ: {question}")
    
    result = chatbot.chat(question)
    
    print(f"Source: {result['source']}")
    print(f"Documents retrieved: {len(result['retrieved_docs'])}")
    
    if result['retrieved_docs']:
        print("\nRetrieved document sources:")
        for i, doc in enumerate(result['retrieved_docs'], 1):
            print(f"  {i}. {doc['source']}")
    
    print(f"\nResponse (first 300 chars):")
    print(f"{result['response'][:300]}...")
    
    # Show conversation history
    print("\n" + "=" * 70)
    print("4. Conversation History Summary")
    print("-" * 70)
    
    history = chatbot.get_history()
    for i, exchange in enumerate(history, 1):
        print(f"\n{i}. Q: {exchange['user_message']}")
        print(f"   Source: {exchange['source']}")
    
    # Display available hardcoded questions
    print("\n" + "=" * 70)
    print("5. Available Hardcoded Questions")
    print("-" * 70)
    print("\nCommon questions with instant answers:\n")
    
    questions = chatbot.get_knowledge_base_questions()
    for q in questions:
        print(f"  • {q}")
    
    print("\n" + "=" * 70)
    print("Example complete! Try running main.py for interactive mode.")
    print("=" * 70)


if __name__ == "__main__":
    main()
