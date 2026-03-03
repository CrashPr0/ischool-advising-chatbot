"""Hardcoded knowledge base for common advising questions."""
from typing import Optional, Dict, List


class KnowledgeBase:
    """Manages hardcoded answers for common questions."""
    
    # Common questions and their answers
    COMMON_QA = {
        # Graduation and Requirements
        "what do i need to graduate": {
            "answer": """To graduate from the iSchool, you typically need to:
1. Complete all major requirements (typically 120+ units)
2. Maintain a minimum GPA of 2.0 in major courses
3. Complete general education requirements
4. File your graduation petition with the registrar
5. Complete any remaining prerequisites or electives

Specific requirements vary by program. I recommend reviewing your degree audit or speaking with an advisor.""",
            "keywords": ["graduate", "graduation", "requirements", "need to graduate"],
            "priority": 1
        },
        
        "how many units do i need": {
            "answer": """Most iSchool programs require 120 units for a bachelor's degree. However, this can vary:
- Some specialized programs may require 121-124 units
- Check your specific program requirements
- Your degree audit shows exactly what you need

Contact the registrar or a department advisor for your specific requirement.""",
            "keywords": ["units", "credits", "how many"],
            "priority": 1
        },
        
        # Course Information
        "what courses do i need to take": {
            "answer": """Your required courses depend on your major and current progress. To find out:
1. Check your degree audit on the Student Information System (SIS)
2. Review your major's course requirements
3. Meet with your advisor who can create a personalized plan

I recommend making an appointment with an iSchool advisor for a detailed course plan.""",
            "keywords": ["courses", "required courses", "take"],
            "priority": 1
        },
        
        "when can i graduate": {
            "answer": """To determine your graduation timeline:
1. Log into your Student Information System (SIS) degree audit
2. Count remaining required units and courses
3. Check course availability and scheduling
4. Meet with an advisor to plan your final semester(s)

Most students can graduate in 1-3 more semesters depending on their progress.""",
            "keywords": ["when", "graduate", "timeline", "when can i"],
            "priority": 1
        },
        
        # Academic Support
        "what is the gpa requirement": {
            "answer": """The minimum GPA requirements at SJSU are:
- Overall: 2.0 GPA to stay in good academic standing
- Major courses: Many programs require 2.0 in major courses
- Some courses: Certain prerequisites require at least a C grade

Some programs have higher GPA requirements. Check your specific program requirements.""",
            "keywords": ["gpa", "minimum gpa", "grade", "requirement"],
            "priority": 1
        },
        
        # General Questions
        "how do i contact an advisor": {
            "answer": """To contact an iSchool advisor:
1. Visit the iSchool advising office in person
2. Call the advising line (check website for hours)
3. Send an email to advising@sjsu.edu
4. Book an appointment through the online scheduling system

Office hours are typically Monday-Friday, 9am-4pm.""",
            "keywords": ["contact", "advisor", "reach", "meeting", "appointment"],
            "priority": 2
        },
        
        "what is the ischool": {
            "answer": """The iSchool (School of Information) at SJSU offers programs in:
- Library and Information Science (MLIS)
- Information Technology Management (MAITM)
- Archival Science (MA)
- Digital Archives (Certificate)

We focus on preparing information professionals for 21st-century careers.""",
            "keywords": ["ischool", "school of information", "what is"],
            "priority": 2
        },
        
        "hello": {
            "answer": """Hello! I'm the iSchool Senior Advising Assistant. I'm here to help you with questions about:
- Graduation requirements
- Course scheduling and prerequisites
- Program requirements
- Academic policies
- Career guidance

What can I help you with today?""",
            "keywords": ["hi", "hello", "hey", "greetings"],
            "priority": 3
        },
        
        "thanks": {
            "answer": """You're welcome! Feel free to ask if you have any other questions about your program or graduation.""",
            "keywords": ["thanks", "thank you", "appreciate"],
            "priority": 3
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
        """
        Find a hardcoded answer for a user message.
        
        Args:
            user_message: The user's question
            
        Returns:
            Dict with 'answer' and 'matched_question' if found, None otherwise
        """
        user_lower = user_message.lower().strip()
        
        # First, try exact key matches
        for question_key in self.COMMON_QA.keys():
            if question_key == user_lower:
                qa_data = self.COMMON_QA[question_key]
                return {
                    "answer": qa_data["answer"],
                    "matched_question": question_key,
                    "confidence": 1.0,
                    "is_hardcoded": True
                }
        
        # Then, try keyword matching
        for keyword, question_key in self.keyword_index.items():
            if keyword in user_lower or user_lower in keyword:
                qa_data = self.COMMON_QA[question_key]
                return {
                    "answer": qa_data["answer"],
                    "matched_question": question_key,
                    "confidence": 0.8,
                    "is_hardcoded": True
                }
        
        # Try word-by-word matching for multiple keywords
        best_match = None
        best_score = 0
        
        for question_key, qa_data in self.COMMON_QA.items():
            keywords = qa_data.get("keywords", [])
            if not keywords:
                continue
            
            user_words = set(user_lower.split())
            
            for keyword in keywords:
                keyword_words = set(keyword.split())
                intersection = user_words.intersection(keyword_words)
                
                if intersection:
                    score = len(intersection) / len(keyword_words)
                    
                    if score > best_score:
                        best_score = score
                        best_match = {
                            "answer": qa_data["answer"],
                            "matched_question": question_key,
                            "confidence": score,
                            "is_hardcoded": True
                        }
        
        # Return match if confidence is above threshold
        if best_match and best_score >= 0.5:
            return best_match
        
        return None
    
    def get_all_questions(self) -> List[str]:
        """Get all available hardcoded questions."""
        return list(self.COMMON_QA.keys())
    
    def add_qa(self, question: str, answer: str, keywords: List[str], priority: int = 2) -> None:
        """
        Add a new Q&A to the knowledge base.
        
        Args:
            question: The question key
            answer: The answer text
            keywords: List of keywords for matching
            priority: Priority level (1=high, 2=medium, 3=low)
        """
        self.COMMON_QA[question] = {
            "answer": answer,
            "keywords": keywords,
            "priority": priority
        }
        # Rebuild index
        self._build_keyword_index()


# Global instance
_knowledge_base_instance = None


def get_knowledge_base() -> KnowledgeBase:
    """Get or create the global knowledge base instance."""
    global _knowledge_base_instance
    if _knowledge_base_instance is None:
        _knowledge_base_instance = KnowledgeBase()
    return _knowledge_base_instance
