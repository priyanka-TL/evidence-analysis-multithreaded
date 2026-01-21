-- ============================================
-- Migration: 002 - Seed Initial Data
-- Description: Populate initial data for Haryana and Bihar
-- Author: AI Assistant
-- Date: 2026-01-21
-- ============================================

-- ============================================
-- STATES
-- ============================================
INSERT INTO states (state_name, state_code, relevance_mode, thresholds) VALUES
('Haryana', 'HR', 'mixed', '{"relevant": 0.7, "partially_relevant": 0.5, "irrelevant": 0.3}'),
('Bihar', 'BR', 'strict_yes_no', '{"relevant": 0.8, "partially_relevant": 0, "irrelevant": 0.2}');

-- ============================================
-- PROGRAMS
-- ============================================
INSERT INTO programs (state_id, program_name, program_id, program_type, description) VALUES
((SELECT id FROM states WHERE state_code = 'HR'), 
 'haryana program', 
 'PGM_172135_AAO_SCHOOL_CHALE', 
 'enrollment',
 'Aao School Chalein 2025-26 - Enrollment improvement program for Haryana'),

((SELECT id FROM states WHERE state_code = 'BR'), 
 'bihar program', 
 'PGM_BIHAR_EVIDENCE', 
 'quality',
 'Bihar evidence quality assurance program');

-- ============================================
-- PROMPT TEMPLATES
-- ============================================

-- 1. Universal Base Prompt (Works for all evidence types)
INSERT INTO prompt_templates (template_name, template_type, evidence_type, prompt_text, variables, description) VALUES
('base_evidence_validator', 'BASE', 'ALL', 
'You are an educational evidence validator analyzing {evidence_type} submissions for the {state_name} state education department.

Your task is to carefully examine the provided {evidence_type} and answer the following questions accurately based ONLY on what you can observe in the evidence.

IMPORTANT RESPONSE FORMAT:
- For each question, provide EXACTLY ONE answer in the "answers" array
- Provide your detailed reasoning in the "reasonings" array
- The number of answers MUST match the number of questions
- If you cannot determine an answer from the evidence, state "Cannot determine from evidence"
- Be specific and cite what you see in the {evidence_type}

QUESTIONS TO ANSWER:
{questions}

Remember: Base your analysis strictly on observable evidence. Do not make assumptions.',
'["evidence_type", "state_name", "questions"]',
'Universal base prompt for all evidence types'
);

-- 2. Enrollment Data Extraction Suffix (For enrollment tasks)
INSERT INTO prompt_templates (template_name, template_type, evidence_type, prompt_text, variables, description) VALUES
('enrollment_suffix', 'SUFFIX', 'ALL',
'

ADDITIONAL ENROLLMENT DATA EXTRACTION:
If this evidence contains enrollment data, extract the following:
- enrollment_2024: Total student enrollment for the year 2024 (integer)
- enrollment_2025: Total student enrollment for the year 2025 (integer)  
- enrollment_increase_percentage: Calculate the percentage change from 2024 to 2025 (float)

Formula: enrollment_increase_percentage = ((enrollment_2025 - enrollment_2024) / enrollment_2024) * 100

If enrollment data is not visible or cannot be determined, set these fields to null.
Be precise and extract exact numbers as shown in the evidence.',
'[]',
'Suffix for enrollment-related tasks to extract enrollment numbers'
);

-- 3. Bihar Strict Mode Suffix
INSERT INTO prompt_templates (template_name, template_type, evidence_type, prompt_text, variables, description) VALUES
('bihar_strict_suffix', 'SUFFIX', 'ALL',
'

BIHAR STATE REQUIREMENTS:
- For each question, answer with ONLY "YES" or "NO"
- No additional explanations in the answer field
- Keep reasoning brief and factual
- Focus on observable facts only',
'[]',
'Bihar state-specific suffix for strict YES/NO responses'
);

-- 4. Haryana Detailed Mode Suffix
INSERT INTO prompt_templates (template_name, template_type, evidence_type, prompt_text, variables, description) VALUES
('haryana_detailed_suffix', 'SUFFIX', 'ALL',
'

HARYANA STATE REQUIREMENTS:
- Provide detailed, descriptive answers
- Include specific observations from the evidence
- Explain reasoning clearly
- If applicable, mention what is present and what might be missing',
'[]',
'Haryana state-specific suffix for detailed responses'
);

-- 5. Image-Specific Instructions
INSERT INTO prompt_templates (template_name, template_type, evidence_type, prompt_text, variables, description) VALUES
('image_specific_instructions', 'PREFIX', 'image',
'IMAGE ANALYSIS GUIDELINES:
- Carefully examine all text visible in the image
- Note any forms, certificates, or official documents
- Observe people, settings, and activities if present
- Read any handwritten or printed text
- Look for dates, numbers, names, and signatures

',
'[]',
'Specific instructions for analyzing image evidence'
);

-- 6. PDF-Specific Instructions
INSERT INTO prompt_templates (template_name, template_type, evidence_type, prompt_text, variables, description) VALUES
('pdf_specific_instructions', 'PREFIX', 'pdf',
'PDF DOCUMENT ANALYSIS GUIDELINES:
- Read through all pages carefully
- Extract information from tables and forms
- Note headers, footers, and document metadata
- Look for official stamps, signatures, or seals
- Pay attention to structured data like enrollment records

',
'[]',
'Specific instructions for analyzing PDF documents'
);

-- 7. Excel-Specific Instructions
INSERT INTO prompt_templates (template_name, template_type, evidence_type, prompt_text, variables, description) VALUES
('excel_specific_instructions', 'PREFIX', 'excel',
'SPREADSHEET ANALYSIS GUIDELINES:
- Examine all sheets in the workbook
- Read column headers and row labels
- Extract numerical data accurately
- Look for summary rows or calculated fields
- Note any formulas or data patterns

',
'[]',
'Specific instructions for analyzing Excel spreadsheets'
);

-- ============================================
-- PROMPT ASSIGNMENTS
-- ============================================

-- Universal base prompt (lowest priority - fallback)
INSERT INTO prompt_assignments (state_code, program_name, task_pattern, template_id, priority) VALUES
('ALL', 'ALL', '%', (SELECT id FROM prompt_templates WHERE template_name = 'base_evidence_validator'), 1);

-- Haryana enrollment tasks (high priority)
INSERT INTO prompt_assignments (state_code, program_name, task_pattern, template_id, priority) VALUES
('HR', 'ALL', '%enroll%', (SELECT id FROM prompt_templates WHERE template_name = 'enrollment_suffix'), 10),
('HR', 'ALL', '%enroll%', (SELECT id FROM prompt_templates WHERE template_name = 'haryana_detailed_suffix'), 10);

-- Bihar strict mode (all tasks)
INSERT INTO prompt_assignments (state_code, program_name, task_pattern, template_id, priority) VALUES
('BR', 'ALL', '%', (SELECT id FROM prompt_templates WHERE template_name = 'bihar_strict_suffix'), 8);

-- Evidence type-specific prefixes (medium priority)
INSERT INTO prompt_assignments (state_code, program_name, task_pattern, template_id, priority) VALUES
('ALL', 'ALL', '%', (SELECT id FROM prompt_templates WHERE template_name = 'image_specific_instructions'), 5),
('ALL', 'ALL', '%', (SELECT id FROM prompt_templates WHERE template_name = 'pdf_specific_instructions'), 5),
('ALL', 'ALL', '%', (SELECT id FROM prompt_templates WHERE template_name = 'excel_specific_instructions'), 5);

-- ============================================
-- EXTRACTION RULES
-- ============================================

-- Universal fields (all states, all programs)
INSERT INTO extraction_rules (state_code, program_name, task_pattern, field_name, data_type, is_required, is_enrollment) VALUES
('ALL', 'ALL', '%', 'answers', 'list', TRUE, FALSE),
('ALL', 'ALL', '%', 'reasonings', 'list', TRUE, FALSE);

-- Haryana enrollment-specific fields
INSERT INTO extraction_rules (state_code, program_name, task_pattern, field_name, data_type, is_required, is_enrollment) VALUES
('HR', 'ALL', '%enroll%', 'enrollment_2024', 'integer', FALSE, TRUE),
('HR', 'ALL', '%enroll%', 'enrollment_2025', 'integer', FALSE, TRUE),
('HR', 'ALL', '%enroll%', 'enrollment_increase_percentage', 'float', FALSE, TRUE);

-- Bihar - Same enrollment fields if needed
INSERT INTO extraction_rules (state_code, program_name, task_pattern, field_name, data_type, is_required, is_enrollment) VALUES
('BR', 'ALL', '%enroll%', 'enrollment_2024', 'integer', FALSE, TRUE),
('BR', 'ALL', '%enroll%', 'enrollment_2025', 'integer', FALSE, TRUE),
('BR', 'ALL', '%enroll%', 'enrollment_increase_percentage', 'float', FALSE, TRUE);

-- ============================================
-- Verification Queries
-- ============================================

-- Count records inserted
DO $$
DECLARE
    state_count INTEGER;
    program_count INTEGER;
    template_count INTEGER;
    assignment_count INTEGER;
    rule_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO state_count FROM states;
    SELECT COUNT(*) INTO program_count FROM programs;
    SELECT COUNT(*) INTO template_count FROM prompt_templates;
    SELECT COUNT(*) INTO assignment_count FROM prompt_assignments;
    SELECT COUNT(*) INTO rule_count FROM extraction_rules;
    
    RAISE NOTICE 'Seed data inserted successfully:';
    RAISE NOTICE '  States: %', state_count;
    RAISE NOTICE '  Programs: %', program_count;
    RAISE NOTICE '  Prompt Templates: %', template_count;
    RAISE NOTICE '  Prompt Assignments: %', assignment_count;
    RAISE NOTICE '  Extraction Rules: %', rule_count;
END $$;

-- ============================================
-- Seed data complete
-- ============================================
