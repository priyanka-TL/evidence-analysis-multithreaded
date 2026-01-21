-- ============================================
-- Migration: 001 - Create Prompt Management Schema
-- Description: Initial database schema for prompt management system
-- Author: AI Assistant
-- Date: 2026-01-21
-- ============================================

-- Enable UUID extension (PostgreSQL)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- Table: states
-- Purpose: Store state-level configuration
-- ============================================
CREATE TABLE IF NOT EXISTS states (
    id SERIAL PRIMARY KEY,
    state_name VARCHAR(100) NOT NULL UNIQUE,
    state_code VARCHAR(10) NOT NULL UNIQUE,
    relevance_mode VARCHAR(50) NOT NULL DEFAULT 'mixed',
    thresholds JSONB DEFAULT '{"relevant": 0.7, "partially_relevant": 0.5, "irrelevant": 0.3}',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_relevance_mode CHECK (relevance_mode IN ('strict_yes_no', 'mixed', 'descriptive'))
);

-- Index for fast lookups
CREATE INDEX idx_states_code ON states(state_code) WHERE is_active = TRUE;
CREATE INDEX idx_states_name ON states(state_name) WHERE is_active = TRUE;

-- ============================================
-- Table: programs
-- Purpose: Store program information
-- ============================================
CREATE TABLE IF NOT EXISTS programs (
    id SERIAL PRIMARY KEY,
    state_id INTEGER REFERENCES states(id) ON DELETE CASCADE,
    program_name VARCHAR(255) NOT NULL,
    program_id VARCHAR(100) NOT NULL,
    program_type VARCHAR(50),
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(state_id, program_name)
);

-- Index for fast lookups
CREATE INDEX idx_programs_state ON programs(state_id) WHERE is_active = TRUE;
CREATE INDEX idx_programs_name ON programs(program_name) WHERE is_active = TRUE;

-- ============================================
-- Table: prompt_templates
-- Purpose: Store reusable prompt components
-- ============================================
CREATE TABLE IF NOT EXISTS prompt_templates (
    id SERIAL PRIMARY KEY,
    template_name VARCHAR(100) NOT NULL UNIQUE,
    template_type VARCHAR(50) NOT NULL,
    evidence_type VARCHAR(50) DEFAULT 'ALL',
    prompt_text TEXT NOT NULL,
    variables JSONB DEFAULT '[]',
    description TEXT,
    version INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_template_type CHECK (template_type IN ('BASE', 'SUFFIX', 'PREFIX', 'FULL')),
    CONSTRAINT chk_evidence_type CHECK (evidence_type IN ('ALL', 'image', 'pdf', 'excel'))
);

-- Index for fast lookups
CREATE INDEX idx_prompt_templates_type ON prompt_templates(template_type, evidence_type) WHERE is_active = TRUE;
CREATE INDEX idx_prompt_templates_name ON prompt_templates(template_name) WHERE is_active = TRUE;

-- ============================================
-- Table: prompt_assignments
-- Purpose: Map prompts to specific contexts
-- ============================================
CREATE TABLE IF NOT EXISTS prompt_assignments (
    id SERIAL PRIMARY KEY,
    state_code VARCHAR(10) NOT NULL DEFAULT 'ALL',
    program_name VARCHAR(255) NOT NULL DEFAULT 'ALL',
    task_pattern VARCHAR(255) NOT NULL DEFAULT '%',
    template_id INTEGER REFERENCES prompt_templates(id) ON DELETE CASCADE,
    priority INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(state_code, program_name, task_pattern, template_id)
);

-- Index for fast prompt lookup
CREATE INDEX idx_prompt_assignments_lookup ON prompt_assignments(state_code, program_name, template_id) 
    WHERE is_active = TRUE;
CREATE INDEX idx_prompt_assignments_priority ON prompt_assignments(priority DESC) WHERE is_active = TRUE;

-- ============================================
-- Table: extraction_rules
-- Purpose: Define field extraction patterns
-- ============================================
CREATE TABLE IF NOT EXISTS extraction_rules (
    id SERIAL PRIMARY KEY,
    state_code VARCHAR(10) NOT NULL DEFAULT 'ALL',
    program_name VARCHAR(255) NOT NULL DEFAULT 'ALL',
    task_pattern VARCHAR(255) NOT NULL,
    field_name VARCHAR(100) NOT NULL,
    data_type VARCHAR(50) NOT NULL DEFAULT 'string',
    extract_pattern VARCHAR(255),
    default_value TEXT,
    is_required BOOLEAN DEFAULT FALSE,
    is_enrollment BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_data_type CHECK (data_type IN ('string', 'integer', 'float', 'boolean', 'list', 'json')),
    UNIQUE(state_code, program_name, task_pattern, field_name)
);

-- Index for fast extraction rule lookup
CREATE INDEX idx_extraction_rules_lookup ON extraction_rules(state_code, program_name, task_pattern) 
    WHERE is_active = TRUE;
CREATE INDEX idx_extraction_rules_enrollment ON extraction_rules(is_enrollment) 
    WHERE is_enrollment = TRUE AND is_active = TRUE;

-- ============================================
-- Table: audit_log
-- Purpose: Track all changes to prompts and configs
-- ============================================
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    record_id INTEGER NOT NULL,
    action VARCHAR(20) NOT NULL,
    old_values JSONB,
    new_values JSONB,
    changed_by VARCHAR(100) DEFAULT 'system',
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT chk_action CHECK (action IN ('INSERT', 'UPDATE', 'DELETE'))
);

-- Index for audit queries
CREATE INDEX idx_audit_log_table ON audit_log(table_name, record_id);
CREATE INDEX idx_audit_log_date ON audit_log(changed_at DESC);

-- ============================================
-- Triggers for updated_at timestamp
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_states_updated_at BEFORE UPDATE ON states
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_programs_updated_at BEFORE UPDATE ON programs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_prompt_templates_updated_at BEFORE UPDATE ON prompt_templates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_prompt_assignments_updated_at BEFORE UPDATE ON prompt_assignments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_extraction_rules_updated_at BEFORE UPDATE ON extraction_rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- Views for easier querying
-- ============================================
CREATE OR REPLACE VIEW v_active_prompts AS
SELECT 
    pa.id,
    pa.state_code,
    pa.program_name,
    pa.task_pattern,
    pt.template_name,
    pt.template_type,
    pt.evidence_type,
    pt.prompt_text,
    pa.priority,
    pa.created_at
FROM prompt_assignments pa
JOIN prompt_templates pt ON pa.template_id = pt.id
WHERE pa.is_active = TRUE AND pt.is_active = TRUE
ORDER BY pa.priority DESC;

CREATE OR REPLACE VIEW v_extraction_rules_active AS
SELECT 
    state_code,
    program_name,
    task_pattern,
    field_name,
    data_type,
    is_enrollment,
    is_required
FROM extraction_rules
WHERE is_active = TRUE
ORDER BY state_code, program_name, task_pattern;

-- ============================================
-- Grant permissions (adjust as needed)
-- ============================================
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO evidence_app;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO evidence_app;

-- ============================================
-- Comments for documentation
-- ============================================
COMMENT ON TABLE states IS 'Stores state-level configuration including relevance modes and thresholds';
COMMENT ON TABLE programs IS 'Stores program information linked to states';
COMMENT ON TABLE prompt_templates IS 'Reusable prompt templates that can be composed together';
COMMENT ON TABLE prompt_assignments IS 'Maps prompts to specific contexts (state/program/task)';
COMMENT ON TABLE extraction_rules IS 'Defines which fields to extract for specific contexts';
COMMENT ON TABLE audit_log IS 'Audit trail for all configuration changes';

COMMENT ON COLUMN states.relevance_mode IS 'Values: strict_yes_no, mixed, descriptive';
COMMENT ON COLUMN prompt_templates.template_type IS 'Values: BASE, SUFFIX, PREFIX, FULL';
COMMENT ON COLUMN prompt_templates.evidence_type IS 'Values: ALL, image, pdf, excel';
COMMENT ON COLUMN prompt_assignments.task_pattern IS 'SQL LIKE pattern to match task names';
COMMENT ON COLUMN extraction_rules.is_enrollment IS 'TRUE if this field is enrollment-related';

-- ============================================
-- Migration complete
-- ============================================
