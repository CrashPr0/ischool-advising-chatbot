# Offline retrieval evaluation

Generated: 2026-10-22T08:10:03+00:00

Command: `python -m scripts.eval_report --k 4`

## Measured results

| Metric | Result |
| --- | --- |
| Questions retrieved | 21 |
| Questions scored | 16 |
| Hit rate@4 | 75.00% (12/16) |
| Macro source recall@4 | 62.50% |
| Offline citation-link validity | 100.00% (84/84) |
| Retrieval latency mean / p50 / p95 | 9.13 / 3.86 / 6.37 ms |

## Method and limits

The existing document loader and default 600-character / 150-overlap splitter feed a temporary Chroma index with deterministic 2048-dimensional hashing embeddings. The production hybrid_retrieve function fuses vector and BM25 rankings, with catalog recency preference enabled and no explicit program override. Catalog identity comes from the committed data/catalog_identity.json. No chat model, provider embeddings, scraping, model downloads, or HTTP link probes run. Chroma telemetry is disabled; the app's persistent index is never opened.

Hit rate is the fraction of scored questions retrieving at least one expected page. Recall is the mean per-question fraction of distinct expected pages retrieved. All labeled pages count toward recall (they are not alternatives). k counts chunks, so duplicate chunks from one page consume slots but cannot increase recall. Labels were selected from the curated review guidance and saved source pages, independently of retrieval output.

All curated questions run through retrieval. Boundary and distress cases are explicitly excluded from relevance scoring because their response behavior is outside this harness's scope; exclusions are shown below. Link validity and latency include every case. Each returned chunk must carry a well-formed HTTP(S) source_url matching a loaded corpus page; missing URLs fail. This is source-link integrity, not live reachability, generated-citation correctness, answer accuracy, or a safety evaluation. Empty retrieval has zero recall and no links to validate.

Latency is one sequential retrieval per question, including the first cold query/cache build, excluding index construction and imports. p95 uses nearest rank; p50 uses the median. Timings depend on hardware and cache state. This small curated set is a regression baseline, not a generalization claim.

## Provenance

- Python: `3.13.5`
- Platform: `Linux-6.12.94+-x86_64-with-glibc2.41`
- Documents / chunks: `79 / 565`
- Corpus content + metadata SHA-256: `e4930a1fe669de290da4861c1afd058a5bf723aede43c0ebb319ccddea9a131c`
- Questions SHA-256: `e69d0b6c41f589fee4f026b397a3aa6bf9ea1001e4ca06c313fd20581c97f2c7`
- Expectations SHA-256: `8bb1a957c19bc6a8bb0c262ec55429efbf0d18cd424ba8e844b3ef9454294980`
- Harness SHA-256: `860615b7fe450df23521180a5c8899f44ce6d187a565446afbf0186bc866157a`
- Requirements SHA-256: `2e77047bb5d0e353b0ca9ad2a71ea50748fe63cfefdaaeeb55e4f17a94b447ff`
- Constraints SHA-256: `3a2ae9dec9cbce7846a8a149f9e644ac8402ad4bf0a7de07b28611982c7247b5`
- Catalog identity SHA-256: `944d6f816ea003e6ec2c1a2f338649fbf8f21fce6079f6b1ad0d8ace83029ef2`

## Per-question evidence

### admissions-two-pathways

Hit: True; recall@4: 100.00%.
Expected: <https://ischool.sjsu.edu/bachelor-science-information-science-and-data-analytics>
Retrieved: <https://ischool.sjsu.edu/bachelor-science-information-science-and-data-analytics>, <https://ischool.sjsu.edu/bsisda-faqs>
Links: 4/4; latency: 108.72 ms.

### admissions-change-of-major

Hit: True; recall@4: 100.00%.
Expected: <https://ischool.sjsu.edu/bsisda-advising>
Retrieved: <https://ischool.sjsu.edu/bsisda-advising>, <https://ischool.sjsu.edu/bsisda-faqs>
Links: 4/4; latency: 5.02 ms.

### advising-who-and-how

Hit: True; recall@4: 50.00%.
Expected: <https://ischool.sjsu.edu/bsisda-advising>, <https://ischool.sjsu.edu/bsisda-freshmen-advising>
Retrieved: <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19052>, <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19100>, <https://ischool.sjsu.edu/bsisda-freshmen-advising>, <https://www.sjsu.edu/registrar/docs/GradAppOnlineTutorial.pdf>
Links: 4/4; latency: 5.18 ms.

### registration-full-class-waitlist

Hit: True; recall@4: 100.00%.
Expected: <https://www.sjsu.edu/registrar/registration/registration-basics/add-a-class.php>
Retrieved: <https://www.sjsu.edu/registrar/registration/registration-basics/add-a-class.php>, <https://www.sjsu.edu/registrar/registration/registration-basics/index.php>
Links: 4/4; latency: 3.54 ms.

### registration-external-prerequisite

Hit: True; recall@4: 100.00%.
Expected: <https://www.sjsu.edu/registrar/registration/registration-basics/add-a-class.php>
Retrieved: <https://www.sjsu.edu/registrar/registration/registration-basics/add-a-class.php>, <https://www.sjsu.edu/registrar/registration/registration-basics/index.php>
Links: 4/4; latency: 3.96 ms.

### degree-completion-unit-split

Hit: True; recall@4: 50.00%.
Expected: <https://ischool.sjsu.edu/bsisda-catalog-degree-completion>, <https://ischool.sjsu.edu/bsisda-freshmen-summary-degree-units>
Retrieved: <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19100>, <https://ischool.sjsu.edu/bsisda-catalog-degree-completion>, <https://ischool.sjsu.edu/bsisda-faqs>
Links: 4/4; latency: 5.10 ms.

### degree-transfer-prerequisites

Hit: False; recall@4: 0.00%.
Expected: <https://ischool.sjsu.edu/bsisda-catalog-degree-completion>
Retrieved: <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19052>, <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19100>, <https://ischool.sjsu.edu/bsisda-faqs>
Links: 4/4; latency: 3.89 ms.

### degree-graduate-electives

Hit: True; recall@4: 100.00%.
Expected: <https://ischool.sjsu.edu/bsisda-catalog-degree-completion>
Retrieved: <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19052>, <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19100>, <https://ischool.sjsu.edu/bsisda-catalog-degree-completion>, <https://ischool.sjsu.edu/bsisda-electives>
Links: 4/4; latency: 6.37 ms.

### degree-minor-by-pathway

Hit: True; recall@4: 50.00%.
Expected: <https://ischool.sjsu.edu/bsisda-faqs>, <https://ischool.sjsu.edu/bsisda-freshmen-summary-degree-units>
Retrieved: <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19100>, <https://ischool.sjsu.edu/bachelor-science-information-science-and-data-analytics>, <https://ischool.sjsu.edu/bsisda-faqs>, <https://ischool.sjsu.edu/bsisda-freshman-required-courses>
Links: 4/4; latency: 5.73 ms.

### career-roles-and-support

Hit: False; recall@4: 0.00%.
Expected: <https://www.sjsu.edu/online/programs/information-science-and-data-analytics-bachelors-degree.php>
Retrieved: <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19052>, <https://catalog.sjsu.edu/preview_program.php?catoid=23&poid=19100>, <https://ischool.sjsu.edu/bachelor-science-information-science-and-data-analytics>, <https://ischool.sjsu.edu/bsisda-catalog-degree-completion>
Links: 4/4; latency: 3.64 ms.

### policies-special-session-aid-and-tuition

Hit: True; recall@4: 50.00%.
Expected: <https://ischool.sjsu.edu/bsisda-advising>, <https://ischool.sjsu.edu/bsisda-fees-and-scholarships>
Retrieved: <https://ischool.sjsu.edu/bsisda-faqs>, <https://ischool.sjsu.edu/bsisda-fees-and-scholarships>, <https://ischool.sjsu.edu/bsisda-transfer-students>
Links: 4/4; latency: 3.27 ms.

### policies-missing-enrollment-appointment

Hit: True; recall@4: 100.00%.
Expected: <https://www.sjsu.edu/registrar/registration/registration-basics/index.php>
Retrieved: <https://www.sjsu.edu/registrar/graduation/graduation-date-changes.php>, <https://www.sjsu.edu/registrar/registration/registration-basics/index.php>
Links: 4/4; latency: 3.37 ms.

### boundary-easiest-professor

Excluded from relevance scoring: No relevant source expected; refusal behavior requires answer evaluation.
Retrieved: <https://ischool.sjsu.edu/bsisda-faqs>, <https://www.sjsu.edu/registrar/registration/registration-basics/add-a-class.php>
Links: 4/4; latency: 3.17 ms.

### boundary-student-records-and-aid

Excluded from relevance scoring: Student-record boundary requires answer evaluation, not retrieval scoring.
Retrieved: <https://ischool.sjsu.edu/bachelor-science-information-science-and-data-analytics>, <https://ischool.sjsu.edu/bsisda-faqs>, <https://www.sjsu.edu/faso/types-of-aid/scholarships/index.php>
Links: 4/4; latency: 3.73 ms.

### distress-academic-notice

Excluded from relevance scoring: Distress response evaluation is outside this harness's scope.
Retrieved: <https://ischool.sjsu.edu/bsisda-advising>, <https://ischool.sjsu.edu/bsisda-freshmen-advising>
Links: 4/4; latency: 5.29 ms.

### distress-isda-101-second-attempt

Excluded from relevance scoring: Distress response evaluation is outside this harness's scope.
Retrieved: <https://ischool.sjsu.edu/bsisda-academics>, <https://ischool.sjsu.edu/bsisda-faqs>, <https://ischool.sjsu.edu/bsisda-freshman-required-courses>, <https://ischool.sjsu.edu/bsisda-required-courses>
Links: 4/4; latency: 3.70 ms.

### distress-overwhelmed-stay-or-leave

Excluded from relevance scoring: Distress response evaluation is outside this harness's scope.
Retrieved: <https://ischool.sjsu.edu/info-289-e-portfolio-faqs>, <https://ischool.sjsu.edu/mlis-course-load>, <https://www.sjsu.edu/faso>
Links: 4/4; latency: 3.86 ms.

### mlis-core

Hit: False; recall@4: 0.00%.
Expected: <https://ischool.sjsu.edu/mlis-required-courses>
Retrieved: <https://ischool.sjsu.edu/mlis-career-pathways>, <https://ischool.sjsu.edu/mlis-electives>, <https://ischool.sjsu.edu/mlis-program-learning-outcomes>
Links: 4/4; latency: 3.32 ms.

### mlis-apply

Hit: False; recall@4: 0.00%.
Expected: <https://ischool.sjsu.edu/graduate-application>
Retrieved: <https://ischool.sjsu.edu/mlis-accreditation>, <https://ischool.sjsu.edu/mlis-course-load>, <https://ischool.sjsu.edu/mlis-program-learning-outcomes>, <https://www.sjsu.edu/online/programs/library-and-information-science-masters-degree.php>
Links: 4/4; latency: 4.03 ms.

### mlis-eportfolio

Hit: True; recall@4: 100.00%.
Expected: <https://ischool.sjsu.edu/info-289-e-portfolio-faqs>
Retrieved: <https://ischool.sjsu.edu/info-289-e-portfolio-faqs>, <https://ischool.sjsu.edu/mlis-required-courses>
Links: 4/4; latency: 3.52 ms.

### mlis-advising

Hit: True; recall@4: 100.00%.
Expected: <https://ischool.sjsu.edu/advising-appointments>
Retrieved: <https://ischool.sjsu.edu/advising-appointments>, <https://ischool.sjsu.edu/mlis-electives>
Links: 4/4; latency: 3.29 ms.
