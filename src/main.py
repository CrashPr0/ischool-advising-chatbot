"""CLI interface for the iSchool Advising Chatbot with RAG."""

import sys

try:
    from .rag_chatbot import RAGAdvisingChatbot
    from .document_processor import setup_sample_docs
except ImportError:  # pragma: no cover - supports direct ``python src/main.py``
    from rag_chatbot import RAGAdvisingChatbot
    from document_processor import setup_sample_docs


def print_header():
    """Print application header."""
    print("=" * 70)
    print("iSchool Senior Advising Chatbot - RAG Enhanced")
    print("Powered by Langchain with Document Retrieval")
    print("=" * 70)
    print()


def print_help():
    """Print help information."""
    print("\nCommands:")
    print("  'exit'          - Quit the chatbot")
    print("  'clear'         - Clear conversation history")
    print("  'docs'          - Load and process advising documents")
    print("  'questions'     - Show available hardcoded questions")
    print("  'help'          - Show this help message")
    print()


def main():
    """Main entry point for the RAG chatbot CLI."""
    print_header()

    # Check available providers
    available_providers = RAGAdvisingChatbot.get_available_providers()

    if not available_providers:
        print("ERROR: No LLM providers configured.")
        print("Please set up your API keys in the .env file")
        print("See .env.example for configuration options")
        sys.exit(1)

    print(f"Available LLM Providers: {', '.join(available_providers)}")
    print()

    # Initialize chatbot
    try:
        chatbot = RAGAdvisingChatbot()
        print("Chatbot initialized successfully!")
        print("- Knowledge base loaded with common questions")
        print("- Ready for document retrieval (RAG)")
        print()
    except Exception as e:
        print(f"ERROR: Failed to initialize chatbot: {e}")
        sys.exit(1)

    # Show initial help
    print("Type 'help' for commands, or ask a question!")
    print("-" * 70)
    print()

    # Interactive loop
    while True:
        try:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            user_lower = user_input.lower()

            # Handle commands
            if user_lower == "exit":
                print("Goodbye!")
                break

            elif user_lower == "clear":
                chatbot.clear_history()
                print("Conversation history cleared.")
                continue

            elif user_lower == "help":
                print_help()
                continue

            elif user_lower == "docs":
                setup_sample_docs()
                print("Processing documents...")
                chatbot.process_documents()
                print("Document processing finished.")
                continue

            elif user_lower == "questions":
                questions = chatbot.get_knowledge_base_questions()
                print(f"\nAvailable hardcoded questions ({len(questions)}):")
                for q in questions:
                    print(f"  - {q}")
                print()
                continue

            # Process user message with RAG
            print("\nAssistant: ", end="", flush=True)

            result = chatbot.chat(user_input)
            response = result["response"]
            source = result["source"]

            # Print response
            print(response)

            # Show source information
            if source == "knowledge_base":
                print("\n[Source: Knowledge Base]")
            elif source == "rag":
                print(
                    "\n[Source: Document Retrieval + LLM] "
                    f"({len(result['retrieved_docs'])} documents)"
                )
            elif source == "llm":
                print("\n[Source: LLM]")

            print()

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"ERROR: {e}")
            print()


if __name__ == "__main__":
    main()
