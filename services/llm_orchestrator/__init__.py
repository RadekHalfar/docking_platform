"""
LLM Orchestrator

This module provides natural language workflow orchestration for the docking system.
Uses pluggable LLM providers to parse user input and coordinate MCP services.
"""

from .orchestrator import WorkflowOrchestrator
from .llm_base import BaseLLMClient
from .llm_adapters import OllamaAdapter, OpenAIAdapter, AnthropicAdapter, OpenAICompatibleAdapter
from .llm_factory import create_llm_client
from .workflow_llm_parser import (
    parse_workflow_with_llm,
    generate_llm_follow_up_question,
    extract_updates_with_llm,
	extract_box_source_choice_with_llm,
    classify_session_intent,
    extract_updates_from_command_with_llm,
)
from .workflow_parser import WorkflowParser
from .workflow_state_manager import WorkflowStateManager
from .interactive_session import InteractiveSession, save_results_bundle, validate_bundle_name
from .rerun_planner import compute_rerun_steps, describe_rerun_plan
from .job_comparator import load_job_affinities, compare_jobs
from .job_cleanup import find_prunable_jobs, delete_jobs

__all__ = [
	'WorkflowOrchestrator',
	'WorkflowParser',
	'WorkflowStateManager',
	'InteractiveSession',
	'save_results_bundle',
	'validate_bundle_name',
	'compute_rerun_steps',
	'describe_rerun_plan',
	'BaseLLMClient',
	'OllamaAdapter',
    'OpenAIAdapter',
    'AnthropicAdapter',
    'OpenAICompatibleAdapter',
	'create_llm_client',
	'parse_workflow_with_llm',
	'generate_llm_follow_up_question',
	'extract_updates_with_llm',
	'extract_box_source_choice_with_llm',
    'classify_session_intent',
    'extract_updates_from_command_with_llm',
    'load_job_affinities',
    'compare_jobs',
    'find_prunable_jobs',
    'delete_jobs',
]
