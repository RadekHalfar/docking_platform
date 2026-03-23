# LLM Workflow Orchestration

Natural-language interface for molecular docking workflows using modular LLM providers and MCP services.

---

## Overview

This system lets you control the full molecular docking pipeline with plain-language commands. A provider-configured LLM parses each request, extracts parameters, and orchestrates calls across MCP services.

### Key Features

- **Natural Language Input** - Describe workflows in plain English.
- **Modular LLM Architecture** - Provider interface + adapters (default: Ollama).
- **Runtime Model Switching** - Change model with flags/env vars, no rebuild required.
- **MCP Integration** - Coordinates all services through a single workflow.
- **Smart Defaults** - Applies defaults when optional parameters are omitted.
- **Validation** - Checks required parameters and prompts for missing values.
- **Docker Deployment** - Runs with a single Docker command.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    User Input                       │
│  "Download ATP, HEM and protein 6lu7, prepare      │
│   and dock with center 10,20,30 and box 25,30,35"  │
└───────────────────┬─────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────┐
│      LLM Provider Adapter (default: Ollama)         │
│  - Provider interface (BaseLLMClient)               │
│  - Adapter implementation (OllamaAdapter)           │
│  - Factory-based selection (create_llm_client)      │
└───────────────────┬─────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────┐
│           Workflow Parser & Validator               │
│  - Validates parameters                             │
│  - Applies defaults                                 │
│  - Checks for missing required params               │
└───────────────────┬─────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────┐
│          Workflow Orchestrator                      │
│  - Sequences MCP service calls                      │
│  - Manages file paths                               │
│  - Handles errors                                   │
└──────┬──────┬──────┬──────┬──────────────────────────┘
       │      │      │      │
       ▼      ▼      ▼      ▼      ▼
    RCSB  Ligand Protein Moldina PyMOL
    Down   Prep   Prep   Docking Visual
```

---

## Quick Start

### 1. Start All Services

```bash
# Start Docker services including Ollama
docker compose up -d

# Wait for Ollama to start (~30 seconds)
docker ps
```

### 2. Download LLM Model

```bash
# Download Qwen 2.5 Coder 7B Q4 (default, ~4.7GB)
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# OR download DeepSeek Coder 6.7B Q4 (alternative, ~4.1GB)
docker exec -it mcp-llm ollama pull deepseek-coder:6.7b-instruct-q4_K_M

# OR download Mistral (alternative, ~4GB)
docker exec -it mcp-llm ollama pull mistral

# OR download Phi-3 (smaller, faster, ~2GB)
docker exec -it mcp-llm ollama pull phi3
```

### 3. Check Service Health

```bash
python nlp_orchestrator.py --check-health
```

### 4. Optional: Select Provider/Model at Runtime

```bash
# Defaults: --llm-provider ollama --llm-endpoint http://localhost:11434 --llm-model qwen2.5-coder:7b-instruct-q4_K_M
python nlp_orchestrator.py --llm-model mistral "Download 6lu7 and ATP"

# Use Qwen 2.5 Coder 7B Q4
python nlp_orchestrator.py --llm-model qwen2.5-coder:7b-instruct-q4_K_M "Download 6lu7 and ATP"

# Explicit provider and endpoint
python nlp_orchestrator.py --llm-provider ollama --llm-endpoint http://localhost:11434 --llm-model phi3 "Download 6lu7"
```

### 5. Run Your First Workflow

```bash
python nlp_orchestrator.py "Download ligands ATP, HEM and protein 6lu7, prepare them for docking and perform docking with center 10,20,30 and box size 25,30,35"
```

---

## Usage

### Command-Line Mode

**Basic usage:**
```bash
python nlp_orchestrator.py "YOUR_NATURAL_LANGUAGE_COMMAND"
```

**Examples:**

```bash
# Simple download
python nlp_orchestrator.py "Download protein 6lu7"

# Download and prepare
python nlp_orchestrator.py "Download ATP and 6lu7, then prepare them"

# Full workflow
python nlp_orchestrator.py "Download ligands ATP, HEM and protein 6lu7, prepare them for docking and dock with center 10,20,30 and box size 25,30,35"

# With custom parameters
python nlp_orchestrator.py "Download 1ABC and ligand NAD, prepare at pH 5.0, dock with exhaustiveness 16"
```

### Interactive Mode

```bash
python nlp_orchestrator.py --interactive
```

Then type commands interactively:
```
🧬 > Download 6lu7 and ATP
🧬 > Prepare them and dock with center 15,25,35 and box 30,30,30
🧬 > quit
```

### Options

```bash
python nlp_orchestrator.py --help

Options:
  --interactive, -i         Run in interactive mode
  --llm-provider NAME       LLM provider name (default: ollama)
  --llm-endpoint URL        LLM API endpoint URL (default: http://localhost:11434)
  --llm-model MODEL         LLM model name (default: qwen2.5-coder:7b-instruct-q4_K_M)
  --llm-api-key KEY         Optional API key for providers that require auth
  --output-dir DIR          Output directory (default: ./orchestrator_output)
  --check-health            Check all services and exit
  --list-models             List available models from selected provider
  --debug                   Enable debug logging
```

---

## Natural Language Examples

### What You Can Say

**Download structures:**
- "Download protein 6lu7"
- "Download ligands ATP and HEM"
- "Get protein 1ABC and ligand NAD from RCSB"

**Preparation:**
- "Prepare them for docking"
- "Prepare at pH 7.4"
- "Prepare protein and ligands with hydrogens"

**Docking:**
- "Dock with center 10,20,30 and box size 25,30,35"
- "Run docking with exhaustiveness 16"
- "Dock using 8 CPUs and population 1000"

**Complete workflows:**
- "Download ATP, HEM and protein 6lu7, prepare and dock"
- "Get 1ABC and NAD, prepare them, then dock with center 15,25,35 and box 30,30,30"
- "Download ligands from file ligands.txt and protein 6lu7, prepare at pH 5.0, dock with high exhaustiveness"

### Supported Services

The LLM can identify these workflow stages:

1. **`download`** - Download structures from RCSB
2. **`prepare_ligands`** - Prepare ligands for docking
3. **`prepare_protein`** - Prepare protein for docking
4. **`dock`** - Run molecular docking
5. **`visualize`** - Create PyMOL visualization

### Supported Parameters

**Structure selection:**
- `protein_id` - PDB ID (e.g., "6lu7", "1ABC")
- `ligand_ids` - List of ligands (e.g., "ATP", "HEM", "NAD")

**Preparation:**
- `ph` - pH value (default: 7.4)
- `add_hydrogens` - Add hydrogens (default: true)
- `remove_waters` - Remove waters (default: true)

**Docking (critical):**
- `center` - Docking box center [x, y, z]
- `box_size` - Box dimensions [x, y, z] in Angstroms
- `exhaustiveness` - Search thoroughness (default: 8)
- `cpu` - Number of CPUs (default: 4)
- `population` - PSO population size (default: 500)

**Visualization:**
- `show_surface` - Show protein surface (default: true)
- `render_image` - Render PNG image (default: true)

---

## Missing Parameters

If critical parameters are missing or inconsistent (like invalid `center`/`box_size`), the orchestrator now uses a chatbot-style clarification flow:

1. Parse the request and validate required fields
2. Ask short, specific follow-up questions only for missing/invalid fields
3. Offer explicit default confirmation for docking coordinates
4. Update workflow state after each answer and continue until complete

Notes:
- The system does not guess required values.
- Coordinate input accepts both `10,20,30` and `[10,20,30]` formats.

**Example:**
```bash
$ python nlp_orchestrator.py "Download 6lu7 and ATP, dock them"

I am missing docking box center [x, y, z]. Do you want to use default [10, 20, 30]? [Y/n]: n
Please enter docking box center as x,y,z (e.g., 10,20,30): [10,20,30]

I am missing docking box size [x, y, z]. Do you want to use default [25, 30, 35]? [Y/n]: y

Execute this workflow? [Y/n]:
```

---

## Output

Each workflow execution creates a job directory:

```
orchestrator_output/
└── job_1730304123/
    ├── downloads/
    │   ├── 6lu7.pdb
    │   ├── ATP.sdf
    │   └── HEM.sdf
    ├── prepared/
    │   ├── 6lu7.pdbqt
    │   ├── ATP.pdbqt
    │   └── HEM.pdbqt
    ├── docking_results/
    │   ├── ATP_out.pdbqt
    │   ├── HEM_out.pdbqt
    │   └── moldina_config.txt
    ├── visualization/
    │   ├── visualization.pml
    │   ├── visualization.pse
    │   └── docking_visualization.png
    └── workflow_results.json
```

**workflow_results.json** contains:
```json
{
  "job_id": "job_1730304123",
  "status": "completed",
  "services_executed": ["download", "prepare_ligands", "prepare_protein", "dock", "visualize"],
  "results": {
    "download": {...},
    "prepare_ligands": {...},
    "dock": {...}
  },
  "errors": []
}
```

---

## Hardware Requirements

### Minimum

- **RAM:** 8 GB (for small models like Phi-3)
- **Storage:** 10 GB (for model + data)
- **GPU:** Optional (CPU-only works but slower)

### Recommended

- **RAM:** 16+ GB
- **Storage:** 20+ GB
- **GPU:** NVIDIA GPU with 6+ GB VRAM (for Llama 2, Mistral)

### Optimal

- **RAM:** 32+ GB (your system!)
- **GPU:** NVIDIA GPU with 12+ GB VRAM
- **Storage:** 50+ GB

**Your system (40GB RAM + NVIDIA GPU) is ideal for running large models like Llama 2 or Mistral with fast inference!**

---

## Model Selection

### Available Models

| Model | Size | Speed | Quality | Use Case |
|-------|------|-------|---------|----------|
| **qwen2.5-coder:7b-instruct-q4_K_M** | ~4.7GB | Medium | High | Project default |
| **deepseek-coder:6.7b-instruct-q4_K_M** | ~4.1GB | Medium | High | Strong alternative |
| **llama2** | ~4GB | Medium | High | Stable alternative |
| **mistral** | ~4GB | Fast | High | Fast alternative |
| **phi3** | ~2GB | Very Fast | Good | Resource-constrained systems |
| **codellama** | ~4GB | Medium | High | Code-focused (alternative) |

### Download Models

```bash
# Qwen 2.5 Coder 7B Q4 (project default)
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# DeepSeek Coder 6.7B Q4 (strong alternative)
docker exec -it mcp-llm ollama pull deepseek-coder:6.7b-instruct-q4_K_M

# Mistral (alternative)
docker exec -it mcp-llm ollama pull mistral

# Phi-3 (lightweight)
docker exec -it mcp-llm ollama pull phi3
```

### List Available Models

```bash
python nlp_orchestrator.py --list-models

# OR
docker exec -it mcp-llm ollama list
```

### Use Different Model

```bash
python nlp_orchestrator.py --llm-model mistral "Download 6lu7"

# Qwen 2.5 Coder 7B Q4
python nlp_orchestrator.py --llm-model qwen2.5-coder:7b-instruct-q4_K_M "Download 6lu7"
```

### Switch Models Without Rebuild

```bash
# Pull model once
docker exec -it mcp-llm ollama pull qwen2.5-coder:7b-instruct-q4_K_M

# Use it immediately (no docker image rebuild)
python nlp_orchestrator.py --llm-model qwen2.5-coder:7b-instruct-q4_K_M "Download 6lu7 and ATP, prepare and dock"
```

---

## Troubleshooting

### Ollama Not Starting

**Check if running:**
```bash
docker ps | grep ollama
```

**View logs:**
```bash
docker logs mcp-llm
```

**Restart:**
```bash
docker compose restart ollama
```

### Model Not Found

**Download the model:**
```bash
docker exec -it mcp-llm ollama pull llama2
```

**Check available models:**
```bash
docker exec -it mcp-llm ollama list
```

### GPU Not Detected

**Check NVIDIA Docker support:**
```bash
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```

**If not working:**
- Install NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html
- Restart Docker Desktop

### Slow Inference

**Use smaller model:**
```bash
docker exec -it mcp-llm ollama pull phi3
python nlp_orchestrator.py --llm-model phi3 "..."
```

**Check GPU usage:**
```bash
nvidia-smi
```

### LLM Returns Invalid JSON

**Try different model:**
```bash
python nlp_orchestrator.py --llm-model mistral "..."
```

**Enable debug mode:**
```bash
python nlp_orchestrator.py --debug "..."
```

### Empty Docking Coordinates From LLM

Some models may return empty arrays for docking coordinates, for example `center: []` or `box_size: []`.
The orchestrator flags these for clarification (instead of running docking with invalid values) and asks follow-up questions.

Accepted input format at the prompt:
```text
10,20,30
[10,20,30]
```

If parsing fails, safe defaults are applied:
- Center: `[10.0, 20.0, 30.0]`
- Box size: `[25.0, 30.0, 35.0]`

### Invalid Protein ID Clarification

If the LLM extracts an invalid protein ID (for example `6lu`), it is reported as invalid (not missing) and the orchestrator asks for a corrected 4-character PDB ID before continuing.

### Service Connection Errors

**Check all services:**
```bash
python nlp_orchestrator.py --check-health
```

**Restart services:**
```bash
docker compose restart
```

---

## Best Practices

### Do

- **Be specific** with protein and ligand IDs
- **Provide docking parameters** (center, box_size) when possible
- **Check service health** before running workflows
- **Use appropriate model** for your hardware
- **Review workflow summary** before execution
- **Keep Ollama updated** for best performance

### Do Not

- **Use vague descriptions** ("dock some ligands")
- **Omit critical parameters** without being prepared to enter them
- **Run multiple heavy workflows** simultaneously
- **Use CPU-only** if you have a GPU (much slower)
- **Ignore error messages** (they contain useful info)

---

## API Integration

### Python API

You can use the orchestrator programmatically:

```python
from services.llm_orchestrator import WorkflowOrchestrator, OllamaClient, WorkflowParser

# Initialize components
llm_client = OllamaClient(model="llama2")
orchestrator = WorkflowOrchestrator(output_base="./my_output")
parser = WorkflowParser()

# Parse user input
user_input = "Download 6lu7 and ATP, prepare and dock"
parsed = llm_client.parse_workflow(user_input)

# Validate workflow
workflow = parser.validate_workflow(parsed)

# Check for missing parameters
if parser.has_missing_parameters():
    print(parser.format_missing_prompt())
    # Handle missing parameters...

# Execute workflow
results = orchestrator.execute_workflow(workflow, job_id="my_job")

print(f"Status: {results['status']}")
print(f"Output: {orchestrator.output_base / results['job_id']}")
```

---

## Performance Tips

### For Faster Inference

1. **Use GPU:** Ensure NVIDIA Docker support is enabled
2. **Choose smaller model:** Phi-3 is much faster than Llama 2
3. **Reduce temperature:** Lower temperature = faster but less creative
4. **Keep model loaded:** Ollama keeps models in memory after first use

### For Better Quality

1. **Use Llama 2 or Mistral:** Better understanding of complex requests
2. **Be explicit:** Include all parameters in your request
3. **Use proper terminology:** "docking box center" vs "center point"

---

## Examples Gallery

### Example 1: Quick Screening

```bash
python nlp_orchestrator.py "Download ATP and 6lu7, prepare them, dock with center 10,20,30 and box 20,20,20, use exhaustiveness 4"
```

**Output:** Fast docking (~2-5 min per ligand)

### Example 2: Standard Workflow

```bash
python nlp_orchestrator.py "Download ligands ATP, HEM, NAD and protein 6lu7, prepare at pH 7.4, dock with center 10,20,30 and box size 25,30,35 using 8 CPUs"
```

**Output:** Standard quality docking (~10-20 min per ligand)

### Example 3: High-Quality Publication

```bash
python nlp_orchestrator.py "Download 6lu7 and ATP, prepare with optimization, dock with center 10,20,30 and box 25,30,35, exhaustiveness 32, population 2000, then visualize with ray tracing"
```

**Output:** Publication-quality results (~30-60 min per ligand)

---

## Advanced Configuration

### Custom Ollama Settings

Edit `docker-compose.yml`:

```yaml
ollama:
  environment:
    - OLLAMA_HOST=0.0.0.0
    - OLLAMA_KEEP_ALIVE=24h  # Keep model loaded
    - OLLAMA_NUM_PARALLEL=2  # Parallel requests
    - OLLAMA_MAX_LOADED_MODELS=2
```

### Custom Output Directory

```bash
python nlp_orchestrator.py --output-dir /path/to/results "..."
```

### Logging Configuration

```bash
# Debug mode
python nlp_orchestrator.py --debug "..."

# Or set environment variable
export LOG_LEVEL=DEBUG
python nlp_orchestrator.py "..."
```

---

## Testing

### Run Tests

```bash
# All tests
python -m pytest tests/test_llm_orchestrator.py -v

# Adapter and provider wiring tests
python -m pytest tests/test_llm_adapters.py -v

# Specific test
python -m pytest tests/test_llm_orchestrator.py::TestLLMArchitecture::test_parse_workflow_with_llm -v

# With coverage
python -m pytest tests/test_llm_orchestrator.py --cov=services.llm_orchestrator
```

### Manual Testing

```bash
# Check health
python nlp_orchestrator.py --check-health

# List models
python nlp_orchestrator.py --list-models

# Test parsing (without execution)
python nlp_orchestrator.py --debug "Download 6lu7"  # Stop before execution
```

---

## Roadmap

### Planned Features

- [ ] Support for custom ligand files (not from RCSB)
- [ ] Batch workflow execution
- [ ] Workflow templates
- [ ] Result comparison and ranking
- [ ] Web UI for orchestrator
- [ ] Support for more LLM backends (local Transformers, vLLM)
- [ ] Workflow history and replay
- [ ] Parameter suggestions based on protein structure

---

## FAQ

**Q: Do I need an internet connection?**  
A: Only for initial model download and RCSB structure downloads. LLM runs locally.

**Q: Can I use a different LLM?**  
A: Yes. The CLI uses a provider/model abstraction. Today, the built-in adapter is Ollama, and you can switch Ollama models via `--llm-model` without rebuild. New providers can be added as adapters.

**Q: How accurate is the parameter parsing?**  
A: Very good for clear, explicit commands. If ambiguous, the system will prompt for clarification.

**Q: Can I run this without a GPU?**  
A: Yes! CPU-only works but is slower. Remove the GPU config from docker-compose.yml.

**Q: What if the LLM makes a mistake?**  
A: The workflow parser validates all parameters. Invalid values are caught before execution.

**Q: Can I modify the workflow after parsing?**  
A: Yes, in interactive mode you can provide missing parameters. For programmatic use, modify the workflow dict before execution.

---

## References

- **Ollama:** https://ollama.com/
- **Llama 2:** https://ai.meta.com/llama/
- **Mistral:** https://mistral.ai/
- **Model Context Protocol (MCP):** Used for service communication
- **Docking Parameters Guide:** See `docs/DOCKING_PARAMETERS.md`

---

*Last updated: March 23, 2026*
