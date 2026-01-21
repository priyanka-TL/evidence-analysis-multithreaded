# Database-Driven Prompt Management System

## Overview

This implementation replaces hardcoded prompts in `1-main-parallel-script.py` with a flexible, database-driven system that supports:

- **State-specific prompts** (Haryana, Bihar, etc.)
- **Program-specific prompts**
- **Task pattern matching** (e.g., all enrollment tasks)
- **Evidence type-specific instructions** (image, PDF, Excel)
- **Priority-based prompt composition**
- **Wildcard support** ('ALL' for universal prompts)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Evidence Processing                       │
│                  (1-main-parallel-script.py)                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Prompt Engine                           │
│            (Composes prompts from templates)                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Prompt Repository                          │
│        (Priority-based template retrieval from DB)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   PostgreSQL Database                        │
│         (States, Programs, Templates, Assignments)           │
└─────────────────────────────────────────────────────────────┘
```

## Database Schema

### Core Tables

1. **states** - State configurations (Haryana, Bihar, etc.)
2. **programs** - Programs within states
3. **prompt_templates** - Reusable prompt text components
4. **prompt_assignments** - Maps templates to contexts with priorities
5. **extraction_rules** - Defines what fields to extract for each context
6. **audit_log** - Tracks all changes

### Key Design Decisions

#### Direct state_code and program_name (Not Foreign Keys)

The `prompt_assignments` and `extraction_rules` tables use **direct string fields** instead of foreign keys:

```sql
CREATE TABLE prompt_assignments (
    state_code VARCHAR(10) DEFAULT 'ALL',      -- Not a FK!
    program_name VARCHAR(255) DEFAULT 'ALL',   -- Not a FK!
    task_pattern VARCHAR(255) DEFAULT '%',
    template_id INTEGER REFERENCES prompt_templates(id),
    priority INTEGER DEFAULT 1
);
```

**Benefits:**
- ✅ Wildcard support: Use 'ALL' for universal prompts
- ✅ Faster queries: No JOINs needed for prompt lookup
- ✅ Flexible: Can match against partial state/program names
- ✅ Simple: Direct string matching with priorities

#### Priority-Based Matching

When retrieving prompts, the system matches in this order:

1. **Specific** (state='HR', program='haryana program', task='%enroll%') → Priority 10
2. **State-level** (state='HR', program='ALL', task='%enroll%') → Priority 8
3. **Universal** (state='ALL', program='ALL', task='%') → Priority 1

Higher priority prompts override lower priority ones.

#### Template Types

- **BASE**: Core prompt text with question placeholders
- **PREFIX**: Added before BASE (e.g., evidence type-specific instructions)
- **SUFFIX**: Added after BASE (e.g., state-specific requirements)
- **FULL**: Complete standalone prompt (overrides composition)

## Directory Structure

```
database/
├── connection.py                 # Database connection pooling
├── migrations/
│   ├── 001_create_schema.sql    # Create all tables
│   └── 002_seed_data.sql        # Initial Haryana & Bihar data
└── repositories/
    ├── base_repository.py       # Common CRUD operations
    └── prompt_repository.py     # Prompt retrieval logic
```

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs `psycopg2-binary` for PostgreSQL connectivity.

### 2. Configure Database

Create a `.env` file with database credentials:

```env
# Option 1: Connection string (recommended)
DATABASE_URL=postgresql://user:password@localhost:5432/evidence_analysis

# Option 2: Individual parameters
DB_HOST=localhost
DB_PORT=5432
DB_NAME=evidence_analysis
DB_USER=postgres
DB_PASSWORD=your_password
```

### 3. Run Migrations

```bash
# Create database (if not exists)
createdb evidence_analysis

# Run schema migration
psql -d evidence_analysis -f database/migrations/001_create_schema.sql

# Load seed data
psql -d evidence_analysis -f database/migrations/002_seed_data.sql
```

### 4. Test Connection

```bash
cd database
python connection.py
```

Expected output:
```
✓ Database connection successful!
PostgreSQL version: {'version': 'PostgreSQL 14.x...'}
```

## Usage Examples

### Example 1: Get Prompt for Haryana Enrollment Task

```python
from database.repositories.prompt_repository import prompt_repository

# Get prompts for Haryana enrollment task
prompts = prompt_repository.get_prompt_for_context(
    state_code='HR',
    program_name='haryana program',
    task_name='Enrollment Data 2024-25',
    evidence_type='image'
)

print(f"Base prompt: {prompts['base_prompt']['prompt_text']}")
print(f"Suffixes: {len(prompts['suffixes'])} suffix templates")
print(f"Prefixes: {len(prompts['prefixes'])} prefix templates")
```

**Result:**
- Base: `base_evidence_validator` (universal)
- Prefix: `image_specific_instructions` (for images)
- Suffixes: `enrollment_suffix` + `haryana_detailed_suffix` (enrollment + Haryana-specific)

### Example 2: Get Prompt for Bihar Task

```python
prompts = prompt_repository.get_prompt_for_context(
    state_code='BR',
    program_name='bihar program',
    task_name='School Infrastructure Check',
    evidence_type='pdf'
)

# Bihar gets strict YES/NO suffix
# Result includes bihar_strict_suffix for YES/NO only responses
```

### Example 3: Create New Template

```python
# Add a new state-specific template
template_id = prompt_repository.create_template(
    template_name='karnataka_suffix',
    template_type='SUFFIX',
    evidence_type='ALL',
    prompt_text='\n\nKARNATAKA REQUIREMENTS:\n- Provide bilingual responses (English + Kannada)\n- Include district codes',
    variables=[],
    description='Karnataka state-specific requirements'
)

# Assign it to Karnataka context
prompt_repository.assign_template(
    template_id=template_id,
    state_code='KA',
    program_name='ALL',
    task_pattern='%',
    priority=8
)
```

## Seed Data Included

### States
- **Haryana** (HR): Mixed relevance mode, detailed responses
- **Bihar** (BR): Strict YES/NO mode

### Templates
1. `base_evidence_validator` - Universal base prompt
2. `enrollment_suffix` - Enrollment data extraction
3. `bihar_strict_suffix` - Bihar YES/NO requirement
4. `haryana_detailed_suffix` - Haryana detailed requirement
5. `image_specific_instructions` - Image analysis guidance
6. `pdf_specific_instructions` - PDF analysis guidance
7. `excel_specific_instructions` - Excel analysis guidance

### Assignments
- Universal base prompt for all contexts (priority 1)
- Haryana enrollment tasks get enrollment + detailed suffixes (priority 10)
- Bihar tasks get strict suffix (priority 8)
- Evidence-type prefixes for all contexts (priority 5)

## Testing Queries

### Verify Seed Data

```sql
-- Check states
SELECT state_name, state_code, relevance_mode FROM states;

-- Check templates
SELECT template_name, template_type, evidence_type FROM prompt_templates;

-- Check assignments
SELECT state_code, program_name, task_pattern, priority 
FROM prompt_assignments 
ORDER BY priority DESC;

-- Check what prompt Haryana enrollment task gets
SELECT 
    pt.template_name,
    pt.template_type,
    pa.priority,
    pa.state_code,
    pa.program_name
FROM prompt_assignments pa
JOIN prompt_templates pt ON pa.template_id = pt.id
WHERE 
    (pa.state_code = 'HR' OR pa.state_code = 'ALL')
    AND 'Enrollment 2024' ILIKE pa.task_pattern
ORDER BY pa.priority DESC;
```

### Count Records

```sql
SELECT 
    (SELECT COUNT(*) FROM states) as states,
    (SELECT COUNT(*) FROM programs) as programs,
    (SELECT COUNT(*) FROM prompt_templates) as templates,
    (SELECT COUNT(*) FROM prompt_assignments) as assignments,
    (SELECT COUNT(*) FROM extraction_rules) as rules;
```

## Benefits Over Hardcoded Prompts

| Aspect | Hardcoded (Old) | Database-Driven (New) |
|--------|----------------|---------------------|
| **Maintenance** | Edit code, redeploy | Update DB, instant effect |
| **State-specific** | Duplicate code blocks | Single template, multiple assignments |
| **Extensibility** | Add more if/else | INSERT new rows |
| **Testing** | Need full deployment | Test queries directly |
| **Audit** | Git history | audit_log table |
| **Flexibility** | Static | Dynamic priority matching |
| **Scalability** | ~150 lines × N states | ~20 lines + N DB rows |

## Migration Strategy

### Phase 1: Parallel Operation (Current)
- Keep old hardcoded prompts
- Add new DB-driven system
- Compare results
- Fallback to hardcoded if DB fails

### Phase 2: Gradual Rollout
- Enable DB prompts for new states
- Keep existing states on hardcoded
- Monitor for issues

### Phase 3: Full Migration
- All states use DB prompts
- Remove hardcoded prompts
- Simplify code to ~50 lines

## Next Steps

1. ✅ **Database Setup** (COMPLETED)
   - Schema migration created
   - Seed data created
   - Connection module created
   - Repository classes created

2. ⏳ **Business Logic Layer** (IN PROGRESS)
   - ConfigLoader (loads from DB + .env fallback)
   - PromptEngine (composes prompts from templates)
   - CSVQuestionLoader (loads questions from CSV)
   - EvidenceProcessor (unified processor)

3. ⏳ **Integration** (PENDING)
   - Update 1-main-parallel-script.py
   - Add fallback logic
   - Testing with sample data

4. ⏳ **Testing** (PENDING)
   - Process 100 Haryana samples
   - Process 100 Bihar samples
   - Compare with old system results
   - Verify identical output

## Troubleshooting

### Connection Issues

```python
from database.connection import test_connection

if not test_connection():
    print("Database connection failed!")
    print("Check your .env file and ensure PostgreSQL is running")
```

### Query Debugging

Enable detailed logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### No Prompts Found

If `get_prompt_for_context()` returns empty:
- Check if state_code exists in seed data
- Verify task_pattern matching (use '%' for wildcard)
- Check is_active flags
- Review priority assignments

## Performance Considerations

### Indexes

The schema includes strategic indexes:
- `idx_states_code` on states(state_code)
- `idx_prompt_assignments_lookup` on (state_code, program_name, template_id)
- `idx_extraction_rules_lookup` on (state_code, program_name, task_pattern)

### Caching

Consider adding in-memory caching for frequently used prompts:

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def get_cached_prompt(state_code, program_name, task_name, evidence_type):
    return prompt_repository.get_prompt_for_context(
        state_code, program_name, task_name, evidence_type
    )
```

## Support

For questions or issues:
1. Check this README
2. Review `DATABASE_SCHEMA_VISUAL.md` for ERD
3. Review `PROMPT_FILTERING_BENEFITS.md` for design rationale
4. Check logs for error messages

---

**Status**: Phase 1 Complete (Database foundation ready)
**Next**: Build PromptEngine and integrate with main script
