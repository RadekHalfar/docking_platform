# Verbosity Levels Guide

`nlp_orchestrator.py` supports three verbosity levels that control output detail during workflow execution.

The examples assume an LLM is configured (`LLM_PROVIDER`/`LLM_MODEL` in `.env`
for a `script-runner` run, exported in the shell for a run on the host, or
`--llm-provider`/`--llm-model`); see [LLM_ORCHESTRATION.md](LLM_ORCHESTRATION.md).

## Overview

| Level | Flag | Purpose | Use Case |
|-------|------|---------|----------|
| **Low** | `--verbose low` (default) | Minimal output, step summaries only | Production use, clean output |
| **Normal** | `--verbose normal` | Show service logs and detailed progress | Development and debugging |
| **MCP Demo** | `--verbose mcp_demo` | Full MCP JSON-RPC communication | Demonstrating Model Context Protocol |

## Low Verbosity (Default)

**Usage:**
```bash
python nlp_orchestrator.py "Download 6lu7 and ATP, prepare and dock"
# or explicitly:
python nlp_orchestrator.py --verbose low "Download 6lu7 and ATP, prepare and dock"
```

**Output Example:**
```
📥 STEP 1: Getting input files...
   ✅ Got protein (RCSB download) and 1 ligand(s) (RCSB download)

🧪 STEP 2: Preparing ligands...
   ✅ Prepared 1 ligand(s)

🧬 STEP 3: Preparing protein...
   ✅ Protein prepared successfully

⚛️  STEP 4: Running molecular docking...
   ✅ Docking completed: 1 successful

🎨 STEP 5: Generating visualization...
   ✅ Visualization prepared: 1 ligand(s)

✅ WORKFLOW COMPLETED SUCCESSFULLY
```

**Features:**
- Clean, emoji-based step indicators
- Brief success/failure messages
- No internal service logging
- Ideal for production workflows and demos

## Normal Verbosity

**Usage:**
```bash
python nlp_orchestrator.py --verbose normal "Download 6lu7 and ATP, prepare and dock"
```

**Output Example:**
```
2026-10-29 07:04:38,816 - services.llm_orchestrator.llm_adapters - INFO - Calling Ollama with model: gemma3:4b
2026-10-29 07:04:50,886 - services.llm_orchestrator.orchestrator - INFO - Starting workflow execution: job_20261029_070450_7a1a17

================================================================================
STEP 1: Getting input files
================================================================================
2026-10-29 07:04:50,887 - services.llm_orchestrator.orchestrator - INFO - Calling download_structures on http://rcsb-downloader:8001
2026-10-29 07:04:52,123 - services.llm_orchestrator.orchestrator - INFO - ✅ Got protein (RCSB download): /workspace/orchestrator_output/job_20261029_070450_7a1a17/downloads/6LU7.pdb
2026-10-29 07:04:52,123 - services.llm_orchestrator.orchestrator - INFO - ✅ Got 1 ligand(s) (RCSB download)

================================================================================
STEP 2: Preparing ligands
================================================================================
2026-10-29 07:04:52,124 - services.llm_orchestrator.orchestrator - INFO - Calling prepare_ligands on http://ligand-preparer:8002
...
```

**Features:**
- Full Python logging with timestamps
- Service names and log levels
- Detailed progress information
- File paths and parameters
- Ideal for development and troubleshooting

## MCP Demo Mode

**Usage:**
```bash
python nlp_orchestrator.py --verbose mcp_demo "Download 6lu7 and ATP, prepare and dock"
```

**Output Example:**
```
📥 STEP 1: Getting input files...

................................................................................
📡 MCP Client → Server (http://rcsb-downloader:8001)
................................................................................

JSON-RPC:
{
  "jsonrpc": "2.0",
  "id": "orchestrator_job_20261029_070450_7a1a17_3f9c2a1b",
  "method": "tool_call",
  "params": {
    "name": "download_structures",
    "arguments": {
      "protein_id": "6lu7",
      "ligand_ids": ["ATP"],
      "formats": {
        "protein": "pdb",
        "ligand": "sdf"
      },
      "save_dir": "/workspace/orchestrator_output/job_20261029_070450_7a1a17/downloads"
    }
  }
}
................................................................................
📡 MCP Server (http://rcsb-downloader:8001) → Client
................................................................................

JSON-RPC:
{
  "jsonrpc": "2.0",
  "id": "orchestrator_job_20261029_070450_7a1a17_3f9c2a1b",
  "result": {
    "protein_path": "/app/orchestrator_output/job_20261029_070450_7a1a17/downloads/6LU7.pdb",
    "ligand_paths": ["/app/orchestrator_output/job_20261029_070450_7a1a17/downloads/ATP.sdf"],
    "sources": {"protein": "https://files.rcsb.org/download/6LU7.pdb", "ligands": ["..."]},
    "checksums": {"protein": "...", "ligands": ["..."]},
    "status": "ok"
  }
}
────────────────────────────────────────────────────────────────────────────────
```

**Features:**
- Full JSON-RPC 2.0 protocol messages
- Client → Server requests
- Server → Client responses
- All service calls with complete payloads
- Ideal for:
  - Teaching/demonstrating Model Context Protocol
  - Understanding MCP architecture
  - Debugging communication issues
  - Protocol-level analysis

## When to Use Each Level

### Use **Low Verbosity** when:
- Running production workflows
- You want clean, readable output
- Presenting to stakeholders
- Running automated scripts
- You only care about success/failure

### Use **Normal Verbosity** when:
- Developing or debugging
- You need detailed progress information
- Troubleshooting service issues
- Understanding timing and performance
- Need file paths and parameters

### Use **MCP Demo Mode** when:
- Teaching about Model Context Protocol
- Demonstrating microservice architecture
- Debugging JSON-RPC communication
- Understanding service interactions
- Creating educational content
- Analyzing protocol-level behavior

## Examples

### Production Workflow
```bash
# Clean output for end users
python nlp_orchestrator.py "Dock 6lu7 with ATP at center 10,20,30 and box size 25,30,35"
```

### Development and Debugging
```bash
# See what's happening under the hood
python nlp_orchestrator.py --verbose normal "Dock 6lu7 with ATP at center 10,20,30 and box size 25,30,35"
```

### MCP Education/Demo
```bash
# Show complete JSON-RPC protocol
python nlp_orchestrator.py --verbose mcp_demo "Dock 6lu7 with ATP at center 10,20,30 and box size 25,30,35"
```

### Interactive Mode with Custom Verbosity
```bash
# Interactive with normal verbosity
python nlp_orchestrator.py --verbose normal --interactive

# Interactive with MCP demo mode
python nlp_orchestrator.py --verbose mcp_demo --interactive
```

## Technical Details

### Implementation
- **Low**: Filters out all service-level logging, only prints step summaries to stdout
  (or to the `on_message` sink a caller passes `WorkflowOrchestrator`; the GUI's
  `OrchestratorBridge` passes its run's log queue)
- **Normal**: Standard Python logging with INFO level
- **MCP Demo**: Suppresses all logging (`VerbosityFilter` drops every record) and prints the step lines plus the formatted JSON-RPC messages instead

The three integer constants (`VERBOSITY_LOW = 0`, `VERBOSITY_NORMAL = 1`, `VERBOSITY_MCP_DEMO = 2`) are
defined once in `services/llm_orchestrator/orchestrator.py` and imported by `nlp_orchestrator.py` —
there is no duplication.

### Log Filtering
The verbosity system uses a custom `VerbosityFilter` in `nlp_orchestrator.py` that:
- Filters messages by logger name
- Controls log level dynamically
- Preserves normal logging for CLI messages
- Adds formatted JSON output for MCP demo mode

### Service Logging
Service `__init__` modules (`ligand_preparer`, `protein_preparer`, `moldina_runner`,
`rcsb_downloader`) do **not** call `logging.basicConfig()`. That call belongs only to application
entry points (`nlp_orchestrator.py`, `*/server.py`). This prevents service libraries from silently
overriding the logging configuration set by the caller.

### Environment Variables
No variable sets the verbosity or the log level; use `--verbose` and
`--debug`. `PYTHONUNBUFFERED=1` (set in every container) keeps the output
unbuffered, so lines appear as they happen.

### Service Port Configuration
Each MCP service reads its port from an environment variable defined in `common/config.py`
(`SERVICE_PORT_ENV_VARS`). The shared helper `get_service_port(service_name)` encapsulates the
env-var lookup with a fallback to `SERVICE_DEFAULT_PORTS`. The cache directory follows the same
pattern via `get_cache_dir()` / `CACHE_DIR_ENV`.
