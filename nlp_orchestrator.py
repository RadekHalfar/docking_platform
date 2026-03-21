"""
LLM Orchestrator CLI

Command-line interface for natural language workflow orchestration.
"""
import sys
import os
import logging
import argparse
import json
from pathlib import Path

from services.llm_orchestrator import WorkflowOrchestrator, OllamaClient, WorkflowParser

# Verbosity levels
VERBOSITY_LOW = 0       # Minimal output (step summaries only)
VERBOSITY_NORMAL = 1    # Normal output (current behavior)
VERBOSITY_MCP_DEMO = 2  # Full MCP demonstration mode (show JSON-RPC messages)

# Global verbosity setting
current_verbosity = VERBOSITY_NORMAL

# Setup logging with custom filter
class VerbosityFilter(logging.Filter):
    """Filter log messages based on verbosity level."""
    
    def filter(self, record):
        global current_verbosity
        
        # In low verbosity, suppress all service logs
        if current_verbosity == VERBOSITY_LOW:
            # Only allow messages from nlp_orchestrator main module
            if not record.name.startswith('__main__'):
                return False
        
        # In MCP demo mode, suppress all logs (we show custom formatted output)
        elif current_verbosity == VERBOSITY_MCP_DEMO:
            # Suppress all logging - we'll use print statements for clean MCP protocol display
            return False
        
        # In normal verbosity, show all INFO logs (current behavior)
        elif current_verbosity == VERBOSITY_NORMAL:
            return True
        
        return True

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add verbosity filter to root logger
verbosity_filter = VerbosityFilter()
for handler in logging.getLogger().handlers:
    handler.addFilter(verbosity_filter)


def main():
    """Main entry point for orchestrator CLI."""
    parser = argparse.ArgumentParser(
        description="Natural Language Molecular Docking Workflow Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default (low verbosity) - minimal output
  python nlp_orchestrator.py "Download ligands ATP, HEM and protein 6lu7, prepare them and dock with center 10,20,30 and box size 25,30,35"
  
  # Normal verbosity - show service logs
  python nlp_orchestrator.py --verbose normal "Download 6lu7 and ATP, prepare and dock"
  
  # MCP demo mode - show full JSON-RPC communication
  python nlp_orchestrator.py --verbose mcp_demo "Download 6lu7 and ATP, prepare and dock"
  
  # Interactive mode
  python nlp_orchestrator.py --interactive
  
  # Health check
  python nlp_orchestrator.py --check-health
"""
    )
    
    parser.add_argument(
        "command",
        nargs="?",
        help="Natural language workflow command"
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Run in interactive mode"
    )
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        help="Ollama API URL (default: http://localhost:11434)"
    )
    parser.add_argument(
        "--model",
        default="llama2",
        help="Ollama model to use (default: llama2)"
    )
    parser.add_argument(
        "--output-dir",
        default="./orchestrator_output",
        help="Base directory for workflow outputs (default: ./orchestrator_output)"
    )
    parser.add_argument(
        "--check-health",
        action="store_true",
        help="Check health of all services and exit"
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List available Ollama models and exit"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--verbose",
        choices=["low", "normal", "mcp_demo"],
        default="low",
        help="Verbosity level: 'low' (minimal, step summaries only), 'normal' (show service logs), 'mcp_demo' (full MCP JSON-RPC demonstration)"
    )
    
    args = parser.parse_args()
    
    # Set verbosity level
    global current_verbosity
    if args.verbose == "low":
        current_verbosity = VERBOSITY_LOW
    elif args.verbose == "normal":
        current_verbosity = VERBOSITY_NORMAL
    elif args.verbose == "mcp_demo":
        current_verbosity = VERBOSITY_MCP_DEMO
        # Don't set DEBUG level - we'll use custom output instead
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Initialize components
    llm_client = OllamaClient(base_url=args.ollama_url, model=args.model)
    orchestrator = WorkflowOrchestrator(output_base=args.output_dir, verbosity=current_verbosity)
    parser_obj = WorkflowParser()
    
    # Handle special commands
    if args.list_models:
        print("Available Ollama models:")
        models = llm_client.list_models()
        if models:
            for model in models:
                print(f"  - {model}")
        else:
            print("  No models found or Ollama not accessible")
        return 0
    
    if args.check_health:
        print("Checking service health...")
        print("\nOllama LLM:")
        ollama_ok = llm_client.check_health()
        print(f"  {'✅' if ollama_ok else '❌'} Ollama API ({args.ollama_url})")
        
        print("\nMCP Services:")
        health = orchestrator.check_services_health()
        for service, ok in health.items():
            print(f"  {'✅' if ok else '❌'} {service}")
        
        all_ok = ollama_ok and all(health.values())
        if all_ok:
            print("\n✅ All services are healthy!")
            return 0
        else:
            print("\n❌ Some services are not healthy. Please start them first.")
            return 1
    
    # Interactive mode
    if args.interactive:
        print("="*80)
        print("LLM Workflow Orchestrator - Interactive Mode")
        print("="*80)
        print("\nType your workflow request in natural language.")
        print("Example: 'Download ligands ATP, HEM and protein 6lu7, prepare and dock'")
        print("\nType 'quit' or 'exit' to stop.\n")
        
        while True:
            try:
                user_input = input("🧬 > ").strip()
                
                if not user_input:
                    continue
                
                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("Goodbye!")
                    break
                
                if user_input.lower() in ['help', 'h', '?']:
                    print("\nSupported commands:")
                    print("  - Natural language workflow requests")
                    print("  - 'help' - Show this help")
                    print("  - 'quit' - Exit the program")
                    continue
                
                # Process workflow
                process_workflow(user_input, llm_client, orchestrator, parser_obj)
                
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                print(f"❌ Error: {e}")
        
        return 0
    
    # Single command mode
    if args.command:
        return process_workflow(args.command, llm_client, orchestrator, parser_obj)
    else:
        parser.print_help()
        return 1


def process_workflow(user_input: str, llm_client: OllamaClient, orchestrator: WorkflowOrchestrator, parser_obj: WorkflowParser) -> int:
    """
    Process a workflow from natural language input.
    
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    try:
        print("\n" + "="*80)
        print("Processing workflow request...")
        print("="*80)
        print(f"\n📝 Input: {user_input}\n")
        
        # Parse with LLM
        print("🤖 Parsing with LLM...")
        parsed = llm_client.parse_workflow(user_input)
        
        # Validate and enrich
        print("✅ Validating workflow...")
        workflow = parser_obj.validate_workflow(parsed)
        
        # Show summary
        print("\n" + parser_obj.get_workflow_summary())
        
        # Check for missing parameters
        if parser_obj.has_missing_parameters():
            print("\n⚠️  Missing required parameters:")
            print(parser_obj.format_missing_prompt())
            
            # Prompt for missing parameters
            print("\nPlease provide missing parameters:")
            updates = {}
            
            for param in parser_obj.get_missing_parameters():
                if param == "center":
                    value = input("  Docking box center [x, y, z]: ").strip()
                    try:
                        coords = [float(x.strip()) for x in value.split(',')]
                        if len(coords) == 3:
                            updates["center"] = coords
                        else:
                            print("    Invalid format. Using default: [10, 20, 30]")
                            updates["center"] = [10.0, 20.0, 30.0]
                    except ValueError:
                        print("    Invalid format. Using default: [10, 20, 30]")
                        updates["center"] = [10.0, 20.0, 30.0]
                
                elif param == "box_size":
                    value = input("  Docking box size [x, y, z]: ").strip()
                    try:
                        sizes = [float(x.strip()) for x in value.split(',')]
                        if len(sizes) == 3:
                            updates["box_size"] = sizes
                        else:
                            print("    Invalid format. Using default: [25, 30, 35]")
                            updates["box_size"] = [25.0, 30.0, 35.0]
                    except ValueError:
                        print("    Invalid format. Using default: [25, 30, 35]")
                        updates["box_size"] = [25.0, 30.0, 35.0]
                
                elif param == "protein_id":
                    value = input("  Protein PDB ID (4 chars): ").strip()
                    if parser_obj.validate_protein_id(value):
                        updates["protein_id"] = value
                    else:
                        print("    Invalid PDB ID format")
                        return 1
                
                elif param == "ligand_ids":
                    value = input("  Ligand IDs (comma-separated): ").strip()
                    ligands = [x.strip() for x in value.split(',')]
                    updates["ligand_ids"] = ligands
            
            # Update workflow
            parser_obj.update_parameters(updates)
            workflow = parser_obj.parsed_workflow
            
            print("\n" + parser_obj.get_workflow_summary())
        
        # Confirm execution
        print("\n" + "="*80)
        confirm = input("Execute this workflow? [Y/n]: ").strip().lower()
        if confirm and confirm not in ['y', 'yes']:
            print("Workflow cancelled.")
            return 0
        
        # Execute workflow
        print("\n" + "="*80)
        print("Executing workflow...")
        print("="*80 + "\n")
        
        results = orchestrator.execute_workflow(workflow)
        
        # Show results
        print("\n" + "="*80)
        print("WORKFLOW RESULTS")
        print("="*80)
        print(f"\nJob ID: {results['job_id']}")
        print(f"Status: {results['status']}")
        print(f"Services Executed: {', '.join(results['services_executed'])}")
        
        if results.get("errors"):
            print("\nErrors:")
            for error in results["errors"]:
                print(f"  ❌ {error}")
            return 1
        
        print("\n✅ Workflow completed successfully!")
        print(f"\nResults saved to: {orchestrator.output_base / results['job_id']}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Failed to process workflow: {e}", exc_info=True)
        print(f"\n❌ Failed to process workflow: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
