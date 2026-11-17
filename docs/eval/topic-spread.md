# Summary chunks and topic spread: retrieval comparison

Command: `python -m scripts.eval_topic_spread --k 8 --summarizer offline`

Corpus: 101 source documents, 743 page chunks, 71 summary chunks (52 page, 19 topic). k = 8; TOPIC_SPREAD_MAX_PER_TOPIC = 3. Hashing embeddings + BM25 through the production hybrid_retrieve, with exact (brute-force) vector search so runs are deterministic; no rag_chatbot pinning, no answer generation.

**Summaries are an offline extractive stand-in (first sentences of each page; a title list per topic), not model output.** They test the plumbing (storage, filtering, citations, spread). Real summary quality needs `--summarizer llm` with provider credentials.

Flags-off parity (summary index, both flags off, vs chunk-only baseline): **identical for every question**

## Exact-answer questions (curated scored set + specific)

Spread applies (broad question, no course code) to 3 of 31: admissions-change-of-major, degree-transfer-prerequisites, policies-missing-enrollment-appointment. The rest keep the fused order under TOPIC_SPREAD.

| Setting | Hit rate | Mean recall | Regressed vs baseline |
| --- | --- | --- | --- |
| baseline | 93.55% | 87.10% | none |
| spread | 93.55% | 87.10% | none |
| summaries | 96.77% | 90.32% | none |
| both | 96.77% | 90.32% | none |

## Broad multi-topic questions

Topic recall: share of the labeled topics present in the top k. Page recall counts a page only when a returned chunk's own source_url is that page; covered recall also counts pages listed by a returned topic summary.

| Setting | Topic recall | Distinct topics | Page recall | Covered recall | Summary chunks in top k |
| --- | --- | --- | --- | --- | --- |
| baseline | 41.67% | 2.8 | 29.00% | 29.00% | 0.0 |
| spread | 63.33% | 4.0 | 52.00% | 52.00% | 0.0 |
| summaries | 41.67% | 2.4 | 29.00% | 34.00% | 1.8 |
| both | 53.33% | 3.0 | 41.00% | 46.00% | 2.2 |

## Per-question detail

### admissions-two-pathways

Question: I have about 60 transferable units. What is the difference between the two-year online ISDA pathway and the four-year on-campus pathway, and which one is F-1 visa eligible?

- baseline: recall 100%; topics advising, overview, requirements
- spread: recall 100%; topics advising, overview, requirements
- summaries: recall 100%; topics advising, overview, requirements
- both: recall 100%; topics advising, overview, requirements

### admissions-change-of-major

Question: I am already an SJSU student and I want to change my major to ISDA. What requirements do I have to meet, and when are the applications due?

- baseline: recall 100%; topics advising, graduation
- spread: recall 100%; topics advising, graduation, registration, requirements
- summaries: recall 100%; topics advising, graduation
- both: recall 100%; topics advising, deadlines, graduation, registration

### advising-who-and-how

Question: Who is my ISDA advisor, and how do I make an appointment?

- baseline: recall 50%; topics advising, graduation, requirements
- spread: recall 50%; topics advising, graduation, requirements
- summaries: recall 50%; topics advising, graduation, requirements
- both: recall 50%; topics advising, graduation, requirements

### registration-full-class-waitlist

Question: The ISDA class I need is full. In MySJSU it shows a blue square. How do I get on the waitlist, and does that reserve a seat for me?

- baseline: recall 100%; topics advising, registration, requirements
- spread: recall 100%; topics advising, registration, requirements
- summaries: recall 100%; topics costs, registration, requirements
- both: recall 100%; topics costs, registration, requirements

### registration-external-prerequisite

Question: I finished the prerequisite at a community college, but MySJSU will not let me add the next ISDA class. How do I get a permission number?

- baseline: recall 100%; topics advising, registration
- spread: recall 100%; topics advising, registration
- summaries: recall 100%; topics advising, registration
- both: recall 100%; topics advising, registration

### degree-completion-unit-split

Question: For the two-year ISDA degree-completion program, how are the 60 units divided among major courses, electives, and university requirements?

- baseline: recall 100%; topics advising, requirements
- spread: recall 100%; topics advising, requirements
- summaries: recall 100%; topics advising, overview, requirements
- both: recall 100%; topics advising, overview, requirements

### degree-transfer-prerequisites

Question: What prerequisite skills does the catalog say I need before I start the ISDA major as a transfer student?

- baseline: recall 0%; topics advising, requirements
- spread: recall 0%; topics advising, graduation, registration, requirements
- summaries: recall 100%; topics advising, requirements
- both: recall 100%; topics advising, requirements

### degree-graduate-electives

Question: Can I use a School of Information graduate course, such as INFO 246, as an ISDA elective, and will that course count later toward a master's degree?

- baseline: recall 100%; topics advising, requirements
- spread: recall 100%; topics advising, requirements
- summaries: recall 100%; topics advising, requirements
- both: recall 100%; topics advising, requirements

### degree-minor-by-pathway

Question: I am a freshman in the on-campus ISDA program and I want to add a minor. A friend in the online transfer program said minors are not allowed. Which of us is right?

- baseline: recall 50%; topics advising, overview, registration, requirements
- spread: recall 50%; topics advising, overview, registration, requirements
- summaries: recall 50%; topics advising, overview, registration, requirements
- both: recall 50%; topics advising, overview, registration, requirements

### career-roles-and-support

Question: What kinds of jobs does the ISDA bachelor's prepare students for, and what career support can I use while I am still in the program?

- baseline: recall 100%; topics advising, overview, requirements
- spread: recall 100%; topics advising, overview, requirements
- summaries: recall 100%; topics overview, requirements
- both: recall 100%; topics overview, requirements

### policies-special-session-aid-and-tuition

Question: I am in the Special Session ISDA program. Can I receive a Cal Grant, and what is the current tuition per unit?

- baseline: recall 50%; topics admissions, advising, costs, requirements
- spread: recall 50%; topics admissions, advising, costs, requirements
- summaries: recall 50%; topics admissions, advising, costs, requirements
- both: recall 50%; topics admissions, advising, costs, requirements

### policies-missing-enrollment-appointment

Question: I do not have an enrollment appointment in MySJSU, so I cannot register. Why would that happen, and where do I find the dates?

- baseline: recall 100%; topics advising, registration
- spread: recall 100%; topics advising, deadlines, graduation, registration
- summaries: recall 100%; topics advising, registration
- both: recall 100%; topics advising, deadlines, graduation, registration

### mlis-core

Question: What are the MLIS core courses?

- baseline: recall 100%; topics careers, requirements
- spread: recall 100%; topics careers, requirements
- summaries: recall 100%; topics careers, requirements
- both: recall 100%; topics careers, requirements

### mlis-apply

Question: How do I apply to the MLIS program?

- baseline: recall 100%; topics admissions, deadlines, graduation
- spread: recall 100%; topics admissions, deadlines, graduation
- summaries: recall 100%; topics admissions, deadlines
- both: recall 100%; topics admissions, deadlines

### mlis-eportfolio

Question: What is the MLIS e-portfolio?

- baseline: recall 100%; topics overview, requirements
- spread: recall 100%; topics overview, requirements
- summaries: recall 100%; topics requirements
- both: recall 100%; topics requirements

### mlis-advising

Question: How do I schedule an MLIS advising appointment?

- baseline: recall 100%; topics advising, careers, requirements
- spread: recall 100%; topics advising, careers, requirements
- summaries: recall 100%; topics advising, requirements
- both: recall 100%; topics advising, requirements

### mara-core

Question: What are the MARA required core courses?

- baseline: recall 100%; topics requirements
- spread: recall 100%; topics requirements
- summaries: recall 100%; topics requirements
- both: recall 100%; topics requirements

### mara-electives

Question: How many elective units can MARA students take from MLIS or Informatics?

- baseline: recall 100%; topics advising, requirements
- spread: recall 100%; topics advising, requirements
- summaries: recall 100%; topics advising, requirements
- both: recall 100%; topics advising, requirements

### mara-eportfolio

Question: What is the MARA culminating experience and when do I take MARA 289?

- baseline: recall 100%; topics careers, requirements
- spread: recall 100%; topics careers, requirements
- summaries: recall 100%; topics careers, requirements
- both: recall 100%; topics careers, requirements

### mara-admission

Question: What GPA and degree do I need for MARA admission? Is the GRE required?

- baseline: recall 100%; topics admissions
- spread: recall 100%; topics admissions
- summaries: recall 100%; topics admissions
- both: recall 100%; topics admissions

### mara-units

Question: How many total units does the Master of Archives and Records Administration (MARA) require?

- baseline: recall 100%; topics careers, overview, requirements
- spread: recall 100%; topics careers, overview, requirements
- summaries: recall 100%; topics overview, requirements
- both: recall 100%; topics overview, requirements

### mara-apply

Question: How do I apply to the MARA program?

- baseline: recall 100%; topics admissions, overview
- spread: recall 100%; topics admissions, overview
- summaries: recall 100%; topics admissions
- both: recall 100%; topics admissions

### mara-289-eligibility

Question: What must I complete before registering for MARA 289? Do I need an approved candidacy form?

- baseline: recall 100%; topics careers, requirements
- spread: recall 100%; topics careers, requirements
- summaries: recall 100%; topics careers, requirements
- both: recall 100%; topics careers, requirements

### mara-283-prerequisites

Question: What are the prerequisites for MARA 283 Enterprise Content Management and Digital Preservation?

- baseline: recall 100%; topics requirements
- spread: recall 100%; topics requirements
- summaries: recall 100%; topics requirements
- both: recall 100%; topics requirements

### mara-informatics-elective-limit

Question: Can I count 9 units of Informatics courses toward my MARA electives?

- baseline: recall 100%; topics advising, requirements
- spread: recall 100%; topics advising, requirements
- summaries: recall 100%; topics advising, requirements
- both: recall 100%; topics advising, requirements

### mara-professional-experience-elective

Question: Is MARA 294 Professional Experience required or an elective, and how much work is each unit?

- baseline: recall 100%; topics careers, requirements
- spread: recall 100%; topics careers, requirements
- summaries: recall 100%; topics careers, requirements
- both: recall 100%; topics careers, requirements

### mara-required-units

Question: How many required course units are in MARA, before electives?

- baseline: recall 100%; topics requirements
- spread: recall 100%; topics requirements
- summaries: recall 100%; topics requirements
- both: recall 100%; topics requirements

### fees-payment-deadline

Question: What is the fee payment deadline?

- baseline: recall 100%; topics costs, deadlines, graduation
- spread: recall 100%; topics costs, deadlines, graduation
- summaries: recall 100%; topics costs, deadlines
- both: recall 100%; topics costs, deadlines

### isda-150-prerequisites

Question: What are the prerequisites for ISDA 150?

- baseline: recall 100%; topics courses, requirements
- spread: recall 100%; topics courses, requirements
- summaries: recall 100%; topics courses, requirements
- both: recall 100%; topics courses, requirements

### bsisda-core-courses

Question: What are the BS ISDA core courses?

- baseline: recall 0%; topics courses, requirements
- spread: recall 0%; topics courses, requirements
- summaries: recall 0%; topics overview, requirements
- both: recall 0%; topics overview, requirements

### graduation-steps

Question: What are the steps to apply for graduation?

- baseline: recall 50%; topics graduation
- spread: recall 50%; topics graduation
- summaries: recall 50%; topics graduation
- both: recall 50%; topics graduation

### broad-bsisda-before-applying

Question: What should I know before applying to the BS ISDA program?

- baseline: recall 25%, topic recall 25%; topics graduation, overview, requirements
- spread: recall 50%, topic recall 50%; topics advising, graduation, overview, requirements
- summaries: recall 25%, topic recall 25%; topics overview, requirements
- both: recall 25%, topic recall 25%; topics overview, requirements

### broad-mlis-before-applying

Question: What should I know before applying to the MLIS program?

- baseline: recall 0%, topic recall 25%; topics careers, overview, requirements
- spread: recall 50%, topic recall 75%; topics admissions, careers, deadlines, overview, requirements
- summaries: recall 0%, topic recall 25%; topics overview, requirements
- both: recall 0%, topic recall 50%; topics admissions, careers, overview, requirements

### broad-new-mlis-student

Question: I am a new MLIS student. What should I know about advising, registering for classes, paying fees, and graduating?

- baseline: recall 50%, topic recall 50%; topics advising, costs, requirements
- spread: recall 50%, topic recall 50%; topics advising, careers, costs, deadlines, requirements
- summaries: recall 50%, topic recall 50%; topics advising, costs, requirements
- both: recall 50%, topic recall 50%; topics advising, costs, requirements

### broad-finishing-bsisda

Question: What should I know about finishing my BS ISDA degree: remaining requirements, deadlines, applying to graduate, and career options?

- baseline: recall 50%, topic recall 75%; topics advising, deadlines, graduation, overview
- spread: recall 50%, topic recall 75%; topics advising, deadlines, graduation, overview
- summaries: recall 50%, topic recall 75%; topics advising, graduation, overview, requirements
- both: recall 50%, topic recall 75%; topics advising, graduation, overview, requirements

### broad-mara-overview

Question: What do MARA students need to know about required courses, electives, the e-portfolio, professional experience, and applying?

- baseline: recall 20%, topic recall 33%; topics requirements
- spread: recall 60%, topic recall 67%; topics careers, requirements
- summaries: recall 20%, topic recall 33%; topics requirements
- both: recall 80%, topic recall 67%; topics careers, requirements

