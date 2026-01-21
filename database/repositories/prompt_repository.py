"""
Prompt Repository - Manages prompt templates and assignments.

This repository handles:
- Retrieving appropriate prompts based on context (state, program, task, evidence type)
- Priority-based prompt matching
- Wildcard support ('ALL' for universal prompts)
- Template composition
"""

import logging
from typing import Dict, List, Optional, Any
from database.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class PromptRepository(BaseRepository):
    """Repository for managing prompt templates and assignments."""
    
    def __init__(self):
        super().__init__('prompt_templates')
    
    def get_prompt_for_context(self, 
                                state_code: str, 
                                program_name: str, 
                                task_name: str,
                                evidence_type: str) -> Dict[str, Any]:
        """
        Get the complete prompt configuration for a specific context.
        
        This method implements priority-based matching:
        1. Specific match (state_code + program_name + task pattern)
        2. State-specific (state_code + 'ALL' program + task pattern)
        3. Universal ('ALL' + 'ALL' + task pattern)
        
        Args:
            state_code: State code (e.g., 'HR', 'BR')
            program_name: Program name (e.g., 'haryana program')
            task_name: Task name for pattern matching
            evidence_type: Evidence type ('image', 'pdf', 'excel')
        
        Returns:
            Dictionary with:
                - templates: List of template dicts sorted by priority
                - base_prompt: The BASE template text
                - prefixes: List of PREFIX template texts
                - suffixes: List of SUFFIX template texts
                - full_templates: List of FULL template texts
        """
        try:
            # Query to get all matching templates ordered by priority
            query = """
                SELECT DISTINCT
                    pt.id,
                    pt.template_name,
                    pt.template_type,
                    pt.evidence_type,
                    pt.prompt_text,
                    pt.variables,
                    pa.priority,
                    pa.state_code,
                    pa.program_name,
                    pa.task_pattern
                FROM prompt_templates pt
                JOIN prompt_assignments pa ON pt.id = pa.template_id
                WHERE 
                    pa.is_active = TRUE
                    AND (
                        -- Exact match
                        (pa.state_code = %s AND pa.program_name = %s AND %s ILIKE pa.task_pattern)
                        OR
                        -- State-specific, all programs
                        (pa.state_code = %s AND pa.program_name = 'ALL' AND %s ILIKE pa.task_pattern)
                        OR
                        -- Universal
                        (pa.state_code = 'ALL' AND pa.program_name = 'ALL' AND %s ILIKE pa.task_pattern)
                    )
                    AND (
                        pt.evidence_type = %s OR pt.evidence_type = 'ALL'
                    )
                ORDER BY pa.priority DESC, pt.template_type
            """
            
            params = (
                state_code, program_name, task_name,  # Exact match
                state_code, task_name,                # State-specific
                task_name,                            # Universal
                evidence_type                         # Evidence type filter
            )
            
            templates = self.db.execute_query(query, params)
            
            if not templates:
                logger.warning(
                    f"No prompts found for context: state={state_code}, "
                    f"program={program_name}, task={task_name}, evidence={evidence_type}"
                )
                return self._get_fallback_prompt()
            
            # Organize templates by type
            base_prompt = None
            prefixes = []
            suffixes = []
            full_templates = []
            
            for template in templates:
                template_type = template['template_type']
                
                if template_type == 'BASE':
                    # Take the highest priority BASE template
                    if base_prompt is None:
                        base_prompt = template
                elif template_type == 'PREFIX':
                    prefixes.append(template)
                elif template_type == 'SUFFIX':
                    suffixes.append(template)
                elif template_type == 'FULL':
                    full_templates.append(template)
            
            result = {
                'templates': templates,
                'base_prompt': base_prompt,
                'prefixes': prefixes,
                'suffixes': suffixes,
                'full_templates': full_templates
            }
            
            logger.info(
                f"Retrieved {len(templates)} template(s) for "
                f"state={state_code}, program={program_name}, task={task_name}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error retrieving prompts for context: {e}")
            raise
    
    def get_template_by_name(self, template_name: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific prompt template by name.
        
        Args:
            template_name: Name of the template
        
        Returns:
            Template dictionary or None if not found
        """
        try:
            return self.find_by_field('template_name', template_name, fetch_one=True)
        except Exception as e:
            logger.error(f"Error retrieving template by name: {e}")
            raise
    
    def get_templates_by_type(self, template_type: str, evidence_type: str = 'ALL') -> List[Dict[str, Any]]:
        """
        Get all templates of a specific type.
        
        Args:
            template_type: Type of template ('BASE', 'PREFIX', 'SUFFIX', 'FULL')
            evidence_type: Evidence type filter ('ALL', 'image', 'pdf', 'excel')
        
        Returns:
            List of template dictionaries
        """
        query = """
            SELECT * FROM prompt_templates 
            WHERE template_type = %s 
            AND (evidence_type = %s OR evidence_type = 'ALL')
            ORDER BY created_at DESC
        """
        try:
            return self.db.execute_query(query, (template_type, evidence_type))
        except Exception as e:
            logger.error(f"Error retrieving templates by type: {e}")
            raise
    
    def get_all_assignments_for_state(self, state_code: str) -> List[Dict[str, Any]]:
        """
        Get all prompt assignments for a specific state.
        
        Args:
            state_code: State code (e.g., 'HR', 'BR')
        
        Returns:
            List of assignment dictionaries with template details
        """
        query = """
            SELECT 
                pa.*,
                pt.template_name,
                pt.template_type,
                pt.evidence_type
            FROM prompt_assignments pa
            JOIN prompt_templates pt ON pa.template_id = pt.id
            WHERE pa.is_active = TRUE
            AND (pa.state_code = %s OR pa.state_code = 'ALL')
            ORDER BY pa.priority DESC
        """
        try:
            return self.db.execute_query(query, (state_code,))
        except Exception as e:
            logger.error(f"Error retrieving assignments for state: {e}")
            raise
    
    def create_template(self, 
                        template_name: str,
                        template_type: str,
                        evidence_type: str,
                        prompt_text: str,
                        variables: Optional[List[str]] = None,
                        description: Optional[str] = None) -> int:
        """
        Create a new prompt template.
        
        Args:
            template_name: Unique name for the template
            template_type: Type ('BASE', 'PREFIX', 'SUFFIX', 'FULL')
            evidence_type: Evidence type ('ALL', 'image', 'pdf', 'excel')
            prompt_text: The actual prompt text
            variables: List of variable names used in the template
            description: Optional description
        
        Returns:
            ID of the created template
        """
        data = {
            'template_name': template_name,
            'template_type': template_type,
            'evidence_type': evidence_type,
            'prompt_text': prompt_text,
            'variables': variables or [],
            'description': description,
            'version': 1
        }
        
        try:
            return self.insert(data, return_id=True)
        except Exception as e:
            logger.error(f"Error creating template: {e}")
            raise
    
    def assign_template(self,
                        template_id: int,
                        state_code: str = 'ALL',
                        program_name: str = 'ALL',
                        task_pattern: str = '%',
                        priority: int = 1) -> int:
        """
        Assign a template to a specific context.
        
        Args:
            template_id: ID of the template to assign
            state_code: State code or 'ALL'
            program_name: Program name or 'ALL'
            task_pattern: SQL LIKE pattern for task matching (default '%')
            priority: Priority (higher = more specific)
        
        Returns:
            ID of the created assignment
        """
        # Use direct SQL since this is for a different table
        query = """
            INSERT INTO prompt_assignments 
            (template_id, state_code, program_name, task_pattern, priority)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """
        params = (template_id, state_code, program_name, task_pattern, priority)
        
        try:
            with self.db.get_cursor() as cursor:
                cursor.execute(query, params)
                result = cursor.fetchone()
                logger.info(
                    f"Assigned template {template_id} to "
                    f"state={state_code}, program={program_name}, pattern={task_pattern}"
                )
                return result['id']
        except Exception as e:
            logger.error(f"Error assigning template: {e}")
            raise
    
    def _get_fallback_prompt(self) -> Dict[str, Any]:
        """
        Get a basic fallback prompt when no specific prompt is found.
        
        Returns:
            Dictionary with minimal prompt structure
        """
        logger.warning("Using fallback prompt - no specific prompt found")
        
        fallback_template = {
            'id': -1,
            'template_name': 'fallback',
            'template_type': 'BASE',
            'evidence_type': 'ALL',
            'prompt_text': 'Analyze the evidence and answer the following questions:\n{questions}',
            'variables': ['questions'],
            'priority': 0,
            'state_code': 'ALL',
            'program_name': 'ALL',
            'task_pattern': '%'
        }
        
        return {
            'templates': [fallback_template],
            'base_prompt': fallback_template,
            'prefixes': [],
            'suffixes': [],
            'full_templates': []
        }


# Global instance
prompt_repository = PromptRepository()
