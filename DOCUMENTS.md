# Sample Advising Documents Guide

This guide shows how to format advising documents for optimal RAG (Retrieval Augmented Generation) performance.

## Document Format Best Practices

### 1. **File Types Supported**
- `.txt` - Plain text (recommended for easy editing)
- `.pdf` - PDF documents (auto-extracts text)

### 2. **File Organization**

**Organize by topic:**
```
data/advising_docs/
├── degree_requirements/
│   ├── MLIS_requirements.txt
│   ├── MITM_requirements.txt
│   └── MA_Archival_requirements.txt
├── courses/
│   ├── core_courses.txt
│   ├── electives.txt
│   └── prerequisites.txt
├── policies/
│   ├── academic_standards.txt
│   ├── graduation_checklist.txt
│   └── enrollment_policies.txt
└── guides/
    ├── course_planning.txt
    └── career_guide.txt
```

### 3. **Content Structure - Best Practices**

For optimal vector retrieval, structure documents with:

```
# [Title or Main Topic]

## Section 1: Overview
[Brief description of the topic]

## Section 2: Requirements
- Clear bullet points
- Numbered lists for sequence
- Specific details (course codes, credits, GPA)

## Section 3: Examples
Real examples help the RAG system understand context

## Section 4: Contact Information
[How to get more help]
```

### 4. **Example Documents**

#### Example 1: Course Requirements

```
# MLIS Program Requirements

## Overview
The Master of Library and Information Science (MLIS) is a 36-unit professional 
degree designed for students seeking careers in information science, libraries, 
and related fields.

## Core Requirements (36 units)

### Information Foundation Courses (12 units)
- INFO 200: Foundations of Information and Library Science (3 units)
- INFO 203: Organization of Information (3 units)
- INFO 210: Information Policy and Management (3 units)
- INFO 211: Research Methods (3 units)

### Specialization Courses (12 units)
Choose from:
- INFO 230: Advanced Information Architecture
- INFO 240: User Experience Research
- INFO 260: Digital Libraries
- INFO 265: Data Curation and Preservation

### Capstone (6 units)
- INFO 250: Capstone Project

### Electives (6 units)
Choose any remaining INFO courses

## Graduation Timeline
- Full-time students: 2 years (12-16 units per semester)
- Part-time students: 3-4 years (6-8 units per semester)

## Prerequisites
- Bachelor's degree from accredited institution
- Minimum 2.75 undergraduate GPA
- Writing sample (may be required)
- TOEFL (for international students, 80+ iBT)

## Contact Information
iSchool Student Services
Email: advising@sjsu.edu
Phone: (408) 924-2470
Hours: Monday-Friday, 9am-4pm
Location: Washington Square Hall, Room 108
```

#### Example 2: Graduation Checklist

```
# Graduation Checklist - MLIS Students

## 1-2 Semesters Before Graduation

### Academic Review
- [ ] Check your degree audit on SIS
- [ ] Verify all required courses are completed or in progress
- [ ] Confirm your current GPA is 3.0 or higher (if required)
- [ ] Review elective selections with your advisor

### Course Planning
- [ ] Plan remaining required courses
- [ ] Register for final semester courses
- [ ] Check for prerequisites you may have missed
- [ ] Discuss capstone project timeline with advisor

### Financial Planning
- [ ] Review your student account balance
- [ ] Plan for final tuition payment
- [ ] Check for outstanding holds on your account

## 1 Semester Before Graduation

### Submit Graduation Petition
**IMPORTANT: DO THIS FIRST!**
- [ ] Complete graduation petition form
- [ ] Submit to Registrar's Office
- [ ] Deadline: Usually 8 weeks before graduation

### Final Course Registration
- [ ] Register for final semester courses
- [ ] Confirm all courses are available
- [ ] Check course meeting times match your schedule

### Diploma Planning
- [ ] Verify name on records is correct
- [ ] Plan for diploma delivery (mailed or picked up)

## Final Semester

### Capstone Project
- [ ] Finalize capstone research topic
- [ ] Complete capstone project
- [ ] Present capstone (if required)
- [ ] Submit final capstone document

### Grade Confirmation
- [ ] Monitor for grade postings
- [ ] Ensure all courses show passing grades
- [ ] Verify final GPA meets graduation requirements

### Commencement
- [ ] Order cap and gown (if attending ceremony)
- [ ] Confirm graduation ceremony date and time
- [ ] Check diploma mailing address

## Common Issues and Solutions

### Issue: Did not meet prerequisite
**Solution:** The prerequisite must be completed before taking the course.
Contact your advisor for alternatives.

### Issue: GPA below 3.0
**Solution:** If your major requires 3.0 GPA, contact advising immediately
about options. You may need additional coursework.

### Issue: Outstanding holds on account
**Solution:** Contact Financial Aid or Student Services to resolve.
Holds prevent graduation.

## Contact Your Advisor
If you have questions about graduation requirements:
- Email: advising@sjsu.edu
- Phone: (408) 924-2470
- Visit: Washington Square Hall, Room 108
- Office Hours: Mon-Fri, 9am-4pm
```

#### Example 3: Course Planning Guide

```
# Course Planning Guide

## How to Plan Your Course Schedule

### Step 1: Check Your Degree Audit
1. Log into Student Information System (SIS)
2. Go to Academic Audit or Degree Progress
3. Review completed courses
4. Note remaining required courses

### Step 2: Understand Prerequisites
Before registering, check if courses require:
- Other courses to be completed first
- Minimum GPA requirements
- Senior standing or major status

Example Prerequisites:
- INFO 250 (Capstone): Requires INFO 200, 203, 210, 211
- INFO 260 (Digital Libraries): Requires INFO 203
- INFO 240 (UX Research): Requires INFO 211 (Research Methods)

### Step 3: Plan Your Semesters

**Full-time Plan (2 years):**
```
Semester 1: Core courses (15 units)
- INFO 200 (3) - Foundation
- INFO 203 (3) - Organization
- INFO 210 (3) - Policy
- Elective (3-6)

Semester 2: Core + Specialization (15 units)
- INFO 211 (3) - Research
- INFO 230 (3) - Architecture
- INFO 240 (3) - UX Research  
- Elective (3-6)

[Continue for 4 semesters]
```

**Part-time Plan (3-4 years):**
```
Take 1-2 courses per semester (6-9 units)
- Spreads workload
- Allows time for work/life balance
- Requires careful prerequisite planning
```

### Step 4: Register for Courses

1. Know your registration window (time-based)
2. Have course CRN numbers ready
3. Register at SIS or use Web Registration
4. Confirm registration immediately

### Step 5: Plan Your Graduation Semester

Your final semester should include:
- INFO 250 (Capstone) - 6 units
- Remaining electives - 0-3 units
- Keep course load light for capstone focus

## Course Load Guidelines

- **Full-time:** 12-16 units per semester
- **Part-time with job:** 6-9 units per semester  
- **Minimum for full-time status:** 12 units
- **Maximum realistic:** 16 units (with capstone: keep at 12)

## Common Planning Mistakes

- ❌ Not checking prerequisites
- ❌ Registering for all courses at once
- ❌ Taking capstone with too many other courses
- ❌ Forgetting to register for final semester

## Tips for Success

1. **Plan early** - Map out your entire program
2. **Meet with advisor** - Get personalized guidance
3. **Understand prerequisites** - Avoid blocking issues
4. **Check course availability** - Some courses are not every semester
5. **Keep flexibility** - Life happens, have backup plans

## Contact Information
Advising Office: advising@sjsu.edu | (408) 924-2470
```

### 5. **Document Quality Checklist**

When adding documents, ensure:

- ✓ **Clear headings** - Use `# `, `## `, `### ` for hierarchy
- ✓ **Specific information** - Include course codes (e.g., "INFO 200")
- ✓ **Contact details** - Phone, email, office location
- ✓ **Examples** - Concrete examples help RAG understand
- ✓ **Lists** - Bullet points and numbered lists are scannable
- ✓ **Dates** - Note when information was last updated
- ✓ **Links** - Include URLs where applicable (optional)
- ✓ **Plain language** - Avoid jargon where possible

### 6. **Optimal Document Size**

**Good:**
- 500-2000 words per document
- Covers one main topic thoroughly
- Includes examples and specific details

**Too small:**
- Under 300 words may not provide enough context

**Too large:**  
- Over 3000 words should be split into multiple files
- Large documents don't retrieve as well

### 7. **Using Different Document Types**

**Text Files (.txt):**
```
- Easy to edit
- No formatting overhead
- Plain text, no special encoding
- Best for policy documents
```

**PDF Files (.pdf):**
```
- Good for official documents
- Preserves original formatting
- Can extract from printed guides
- Text must be machine-readable (not scanned images)
```

**Recommended Approach:**
1. Use `.txt` files for easy maintenance
2. Use `.pdf` for official/published documents
3. Mix formats based on availability

### 8. **Updating Documents**

When updating information:
1. Note the update date in the document
2. Delete old version from `data/advising_docs/`
3. Add updated version
4. Re-run `docs` command in chatbot to re-index

### 9. **Testing Your Documents**

After adding documents:
1. Run `docs` in chatbot to load them
2. Ask related questions to test retrieval
3. Check if responses use your document content
4. Refine document structure if needed

## Example Document Creation Workflow

```
1. Write document in Word/Google Docs
2. Save as .txt or export as .pdf
3. Place in data/advising_docs/
4. Run 'docs' command in chatbot
5. Test with related questions
6. Refine content based on results
7. Re-run 'docs' to update index
```

## Files Already Included

When you run `docs` first time:
- `degree_requirements.txt` - Sample MLIS requirements
- This demonstrates the expected format

## Need More Examples?

Contact the iSchool Advising Office for:
- Official policy documents
- Graduation checklists
- Course descriptions
- Program handbooks
- Career guides

---

**Remember:** Better documents = Better answers from your RAG chatbot!
