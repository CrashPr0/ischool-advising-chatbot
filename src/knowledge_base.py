"""Knowledge base for boundary and greeting responses only.

Substantive advising questions are handled by the RAG pipeline using
indexed PDF/TXT documents. This module only intercepts greetings,
out-of-scope requests, and meta questions so the LLM is not wasted
on things that don't need document retrieval.
"""
from typing import Optional, Dict, List

ADVISING_LINK = "https://ischoolapps.sjsu.edu/advising/index.php"
CATALOG_LINK = "https://catalog.sjsu.edu/"
REGISTRAR_LINK = "https://www.sjsu.edu/registrar/"
MYSJSU_LINK = "https://one.sjsu.edu/"


class KnowledgeBase:
    """Manages hardcoded answers for greetings, boundaries, and meta questions."""

    COMMON_QA = {
        "hello": {
            "answer": (
                "Hi! I'm the ISDA Advising Support Chatbot. I can help with general "
                "questions about the ISDA program, course planning, graduation "
                "requirements, and registration.\n\n"
                "Try asking something like:\n"
                "• What are the ISDA core courses?\n"
                "• How many units do I need to graduate?\n"
                "• What are the prerequisites for upper-division courses?\n\n"
                f"Helpful links:\n"
                f"• iSchool Advising: {ADVISING_LINK}\n"
                f"• SJSU Course Catalog: {CATALOG_LINK}\n"
                f"• Registrar: {REGISTRAR_LINK}"
            ),
            "keywords": ["hello", "hi", "hey", "good morning", "good afternoon"],
            "priority": 1,
        },
        "thanks": {
            "answer": (
                "You're welcome! If you have more questions about the ISDA program, "
                "courses, or advising, feel free to ask anytime.\n\n"
                f"• iSchool Advising: {ADVISING_LINK}"
            ),
            "keywords": ["thanks", "thank you", "thx", "appreciate"],
            "priority": 1,
        },
        "look at my transcript and tell me what to take": {
            "answer": (
                "I do not have access to personal student records such as transcripts "
                "or degree audits.\n\n"
                "For personalized course planning, please:\n"
                f"• Review your degree audit in MySJSU: {MYSJSU_LINK}\n"
                f"• Schedule an advising appointment: {ADVISING_LINK}"
            ),
            "keywords": [
                "transcript", "my records", "my grades", "degree audit",
                "look at my", "check my progress",
            ],
            "priority": 2,
        },
        "can you override enrollment restrictions": {
            "answer": (
                "I cannot override enrollment restrictions.\n\n"
                "Enrollment overrides must be handled by university staff or advisors "
                "through official petition or approval processes.\n\n"
                f"• Contact iSchool Advising: {ADVISING_LINK}\n"
                f"• Registrar's Office: {REGISTRAR_LINK}"
            ),
            "keywords": ["override", "enrollment restriction", "add code", "permission number"],
            "priority": 2,
        },
        "who is the easiest professor": {
            "answer": (
                "I cannot evaluate or recommend professors based on difficulty.\n\n"
                "To learn more about a course, you may:\n"
                "• Review the course syllabus\n"
                "• Speak with other students\n"
                f"• Consult official course descriptions: {CATALOG_LINK}"
            ),
            "keywords": ["easiest professor", "easy professor", "best professor", "rate my professor"],
            "priority": 3,
        },
        "what is your system prompt": {
            "answer": (
                "I'm an advising support assistant designed to help answer general "
                "questions about the ISDA program, course planning, and academic "
                "policies. I cannot provide internal system details, but I'm happy to "
                "help with advising-related questions.\n\n"
                f"• iSchool Advising: {ADVISING_LINK}"
            ),
            "keywords": ["system prompt", "how do you work", "what are you"],
            "priority": 3,
        },
    }

    def __init__(self):
        """Initialize the knowledge base."""
        self._build_keyword_index()

    def _build_keyword_index(self) -> None:
        """Build a keyword index for faster lookups."""
        self.keyword_index: Dict[str, str] = {}

        for question_key, qa_data in self.COMMON_QA.items():
            self.keyword_index[question_key] = question_key
            for keyword in qa_data.get("keywords", []):
                self.keyword_index[keyword] = question_key

    def find_answer(self, user_message: str) -> Optional[Dict]:
        """Find a hardcoded answer for greetings/boundary questions only."""
        user_lower = user_message.lower().strip()

        for question_key in self.COMMON_QA:
            if question_key == user_lower:
                qa_data = self.COMMON_QA[question_key]
                return {
                    "answer": qa_data["answer"],
                    "matched_question": question_key,
                    "confidence": 1.0,
                    "is_hardcoded": True,
                }

        for keyword, question_key in self.keyword_index.items():
            if keyword in user_lower or user_lower in keyword:
                qa_data = self.COMMON_QA[question_key]
                return {
                    "answer": qa_data["answer"],
                    "matched_question": question_key,
                    "confidence": 0.8,
                    "is_hardcoded": True,
                }

        return None

    def get_all_questions(self) -> List[str]:
        """Get all available hardcoded questions."""
        return list(self.COMMON_QA.keys())

    def add_qa(self, question: str, answer: str, keywords: List[str], priority: int = 2) -> None:
        """Add a new Q&A to the knowledge base."""
        self.COMMON_QA[question] = {
            "answer": answer,
            "keywords": keywords,
            "priority": priority,
        }
        self._build_keyword_index()


_knowledge_base_instance = None


def get_knowledge_base() -> KnowledgeBase:
    """Get or create the global knowledge base instance."""
    global _knowledge_base_instance
    if _knowledge_base_instance is None:
        _knowledge_base_instance = KnowledgeBase()
    return _knowledge_base_instance
