"""Knowledge base for boundary and greeting responses only.

Substantive advising questions are handled by the RAG pipeline using
indexed PDF/TXT documents. This module only intercepts greetings,
out-of-scope requests, and meta questions so the LLM is not wasted
on things that don't need document retrieval.
"""

import re
from typing import Optional, Dict, List, Tuple

ADVISING_LINK = "https://ischoolapps.sjsu.edu/advising/index.php"
CATALOG_LINK = "https://catalog.sjsu.edu/"
REGISTRAR_LINK = "https://www.sjsu.edu/registrar/"
MYSJSU_LINK = "https://one.sjsu.edu/"


class KnowledgeBase:
    """Manages hardcoded answers for greetings, boundaries, and meta questions."""

    COMMON_QA = {
        "other programs": {
            "answer": (
                "I can help with advising for the SJSU iSchool BS ISDA and "
                "MLIS programs. "
                "For other programs, please contact the iSchool advising team: "
                f"{ADVISING_LINK}"
            ),
            "keywords": [
                "MARA",
                "Master of Archives and Records Administration",
                "Informatics",
                "MSIDT",
                "Teacher Librarian",
            ],
            "priority": 3,
        },
        "hello": {
            "answer": (
                "Hi! I'm the SJSU iSchool Advising Support Chatbot. I can "
                "help with general "
                "questions about the SJSU iSchool BS ISDA and MLIS programs, "
                "course planning, graduation "
                "requirements, and registration.\n\n"
                "Try asking something like:\n"
                "• What are the ISDA core courses?\n"
                "• What are the MLIS core courses?\n"
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
                "You're welcome! If you have more questions about the SJSU "
                "iSchool BS ISDA and MLIS programs, "
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
                "transcript",
                "my records",
                "my grades",
                "degree audit",
                "look at my",
                "check my progress",
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
            "keywords": [
                "override",
                "enrollment restriction",
                "add code",
                "permission number",
            ],
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
            "keywords": [
                "easiest professor",
                "easy professor",
                "best professor",
                "rate my professor",
            ],
            "priority": 3,
        },
        "what is your system prompt": {
            "answer": (
                "I'm an advising support assistant designed to help answer general "
                "questions about the SJSU iSchool BS ISDA and MLIS programs, "
                "course planning, and academic "
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
        """Build a normalized keyword index.

        The old implementation used unrestricted substring matching.  That
        made a short greeting such as ``hi`` match ordinary words such as
        ``which``.  Keep the public dictionary for backwards compatibility,
        but normalize every phrase and match it with word boundaries in
        :meth:`find_answer`.
        """
        self.keyword_index: Dict[str, str] = {}

        for question_key, qa_data in self.COMMON_QA.items():
            normalized_question = self._normalize(question_key)
            if normalized_question:
                self.keyword_index[normalized_question] = question_key
            for keyword in qa_data.get("keywords", []):
                normalized_keyword = self._normalize(keyword)
                if normalized_keyword:
                    self.keyword_index[normalized_keyword] = question_key

    @staticmethod
    def _normalize(value: str) -> str:
        """Normalize text for case-insensitive, punctuation-safe matching."""
        if not isinstance(value, str):
            return ""
        return re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).strip()

    @classmethod
    def _phrase_matches(cls, phrase: str, message: str) -> bool:
        """Return whether *phrase* occurs as complete words in *message*."""
        normalized_phrase = cls._normalize(phrase)
        if not normalized_phrase:
            return False

        pattern = (
            r"(?<!\w)"
            + r"\s+".join(re.escape(token) for token in normalized_phrase.split())
            + r"(?!\w)"
        )
        return re.search(pattern, message, flags=re.UNICODE) is not None

    def find_answer(self, user_message: str) -> Optional[Dict]:
        """Find a hardcoded answer for greetings/boundary questions only."""

        if not isinstance(user_message, str):
            return None

        normalized_message = self._normalize(user_message)
        if not normalized_message:
            return None

        for question_key, qa_data in self.COMMON_QA.items():
            if self._normalize(question_key) == normalized_message:
                return {
                    "answer": qa_data["answer"],
                    "matched_question": question_key,
                    "confidence": 1.0,
                    "is_hardcoded": True,
                }

        candidates: List[Tuple[int, int, int, str]] = []
        for keyword, question_key in self.keyword_index.items():
            if self._phrase_matches(keyword, normalized_message):
                priority = int(self.COMMON_QA[question_key].get("priority", 0))
                token_count = len(keyword.split())
                candidates.append((token_count, len(keyword), priority, question_key))

        if candidates:
            _, _, _, question_key = max(candidates, key=lambda item: item[:3])
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

    def add_qa(
        self, question: str, answer: str, keywords: List[str], priority: int = 2
    ) -> None:
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
