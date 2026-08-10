# Service Selection and Dependencies

## Overview

The docking platform uses an LLM-driven approach to understand natural language commands and automatically select and configure which services to run. This document explains:

1. **How the LLM selects services** - what triggers each service
2. **Input/output requirements** - what each service needs and produces
3. **Service dependencies** - order and data flow between services
4. **Required parameters** - what must be asked from the user

Canonical source of workflow metadata:
- Step order, defaults, required parameters, and rerun invalidation rules are centrally defined in [services/llm_orchestrator/workflow_definition.py](../services/llm_orchestrator/workflow_definition.py).
- Parser and rerun planner consume this module directly to avoid drift.

---

## Part 1: Service Selection (LLM Decision Making)

### The Selection Logic

The LLM parser in [services/llm_orchestrator/workflow_llm_parser.py](../services/llm_orchestrator/workflow_llm_parser.py) uses the `SYSTEM_PROMPT` to determine which services to include based on user keywords:

#### Service Selection Rules

| Service | Selected When | Required Parameters |
|---------|---------------|-------------------|
| **get_input_files** | Protein/ligand IDs are mentioned (downloads from RCSB, or uses local files) | `protein_id` |
| **prepare_ligands** | User wants to prepare OR dock | `ligand_ids` |
| **prepare_protein** | User wants to prepare OR dock | `protein_id` |
| **dock** | User explicitly requests docking | `protein_id`, `ligand_ids`, `center`, `box_size` |
| **visualize** | User mentions: visualize, show results, render, view, display, image, picture, photo | `protein_id`, `ligand_ids` |

### Example Service Selections

```
User: "Download 6lu7 and ATP, prepare and dock"
→ Services: ["get_input_files", "prepare_ligands", "prepare_protein", "dock"]
→ Missing: center, box_size

User: "Dock 5ogo with two WWO ligands and render image"
→ Services: ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"]
→ Missing: center, box_size

User: "Just download 1ABC"
→ Services: ["get_input_files"]
→ Missing: none (only get_input_files is needed)

User: "Show results of docking"
→ Services: ["visualize"]
→ Missing: None (if previous results exist)
```

---

## Part 2: Input/Output Requirements for Each Service

### 1. GET_INPUT_FILES Service (`rcsb_downloader`)

**Purpose:** Obtain protein and ligand structures — either downloaded from RCSB Protein Data Bank, or copied in from local files (see [Local File Input](LLM_ORCHESTRATION.md#local-file-input))

**Input Parameters:**
```json
{
  "protein_id": "6lu7",           // 4-char PDB ID (required)
  "ligand_ids": ["ATP", "HEM"],   // List of ligand codes (required)
  "formats": {                    // Optional
    "protein": "pdb",            // or "mmcif"
    "ligand": "sdf"              // or "mol", "mol2"
  },
  "save_dir": "/app/downloads"    // Output directory
}
```

**Outputs:**
```json
{
  "protein_path": "/app/downloads/6lu7.pdb",
  "ligand_paths": [
    "/app/downloads/ATP.sdf",
    "/app/downloads/HEM.sdf"
  ],
  "protein_metadata": {...},
  "ligand_metadata": {...}
}
```

**Data Flow:** Files are saved to the downloads directory

---

### 2. PREPARE_LIGANDS Service (`ligand_preparer`)

**Purpose:** Convert ligand SDF files to PDBQT format with proper protonation and charges

**Input Parameters:**
```json
{
  "ligand_paths": [                    // From get_input_files service
    "/app/downloads/ATP.sdf",
    "/app/downloads/HEM.sdf"
  ],
  "output_dir": "/app/prepared",       // Where to save prepared files
  "ph": 7.4,                           // Protonation pH (default)
  "add_hydrogens": true,               // Add explicit hydrogens
  "generate_3d": true,                 // Generate 3D conformer
  "optimize": true                     // Optimize geometry
}
```

**Outputs:**
```json
{
  "prepared_ligands": [
    {
      "original_path": "/app/downloads/ATP.sdf",
      "pdbqt_path": "/app/prepared/ATP.pdbqt",
      "success": true,
      "rotatable_bonds": 5
    },
    {
      "original_path": "/app/downloads/HEM.sdf",
      "pdbqt_path": "/app/prepared/HEM.pdbqt",
      "success": true,
      "rotatable_bonds": 0
    }
  ]
}
```

**Data Flow:** Consumes ligand files from get_input_files → produces PDBQT files for docking

---

### 3. PREPARE_PROTEIN Service (`protein_preparer`)

**Purpose:** Convert protein PDB file to PDBQT format with protonation and AutoDock atom types

**Input Parameters:**
```json
{
  "protein_paths": [                   // From get_input_files service
    "/app/downloads/6lu7.pdb"
  ],
  "output_dir": "/app/prepared",       // Where to save prepared file
  "ph": 7.4,                           // Protonation pH
  "add_hydrogens": true,               // Add explicit hydrogens
  "remove_waters": true                // Remove water molecules
}
```

**Outputs:**
```json
{
  "prepared_proteins": [
    {
      "original_path": "/app/downloads/6lu7.pdb",
      "pdbqt_path": "/app/prepared/6lu7.pdbqt",
      "success": true,
      "num_atoms": 2547
    }
  ]
}
```

**Data Flow:** Consumes protein from get_input_files → produces PDBQT file for docking

---

### 4. MOLDINA_RUNNER Service (Docking)

**Purpose:** Run molecular docking simulation using Moldina (PSO-based optimizer)

**Input Parameters:**
```json
{
  "receptor_path": "/app/prepared/6lu7.pdbqt",     // From protein preparation
  "ligand_paths": [                                // From ligand preparation
    "/app/prepared/ATP.pdbqt",
    "/app/prepared/HEM.pdbqt"
  ],
  "output_dir": "/app/docking_results",
  "center": [12.3, 34.5, -5.6],                   // Search box center [x,y,z] (REQUIRED)
  "box_size": [20.0, 20.0, 20.0],                 // Search box size [x,y,z] (REQUIRED)
  "cpu": 1,                                        // CPU threads
  "population": 500,                               // PSO population size
  "exhaustiveness": 8,                             // Search exhaustiveness
  "num_modes": 15,                                 // Number of binding modes
  "energy_range": 100.0                            // Energy range for clustering
}
```

**Outputs:**
```json
{
  "successful": 2,
  "failed": 0,
  "results": [
    {
      "ligand_id": "ATP",
      "output_path": "/app/docking_results/ATP_docking.pdbqt",
      "best_affinity": -8.5,
      "num_poses": 9,
      "poses": [
        {
          "pose_rank": 1,
          "affinity": -8.5,
          "rmsd_lb": 0.0,
          "rmsd_ub": 0.0
        },
        ...
      ]
    },
    {
      "ligand_id": "HEM",
      "output_path": "/app/docking_results/HEM_docking.pdbqt",
      "best_affinity": -7.2,
      "num_poses": 9,
      ...
    }
  ]
}
```

**Data Flow:** Consumes prepared protein and ligands → produces docked poses with binding affinities

---

### 5. Visualization: `convert_docked_poses` on LIGAND_PREPARER Service

**Purpose:** Convert each ligand's docked-pose PDBQT into PDBQT/PDB/SDF/MOL2
files for the GUI's in-browser 3Dmol.js viewer and download picker (see
[GUI_GUIDE.md](GUI_GUIDE.md)). No server-side rendering — PDBQT is native
(no conversion needed), PDB/SDF/MOL2 come from a single Meeko PDBQT→RDKit
parse per ligand, restoring proper bond orders. This replaced the old
`PYMOL_VISUALIZER` service as the default `visualize` step; that service
(port 8005, `visualize_docking`) is still present and callable directly, but
is no longer part of the default workflow — see the note at the top of
[PYMOL_VISUALIZATION.md](PYMOL_VISUALIZATION.md).

**Input Parameters:**
```json
{
  "pose_results": [                                // From docking (run_docking's results)
    {
      "ligand_id": "ATP",
      "output_path": "/app/docking_results/ATP_docking.pdbqt",
      "modes": [{"mode": 1, "affinity": -8.5, "rmsd_lb": 0.0, "rmsd_ub": 0.0}]
    }
  ],
  "output_dir": "/app/visualization",
  "formats": ["sdf", "pdb", "mol2"]                // Optional, this is the default
}
```

**Outputs:**
```json
{
  "converted": [
    {
      "ligand_id": "ATP",
      "num_poses": 1,
      "conversion_method": "meeko",
      "sdf_path": "/app/visualization/ATP_poses.sdf",
      "pdb_path": "/app/visualization/ATP_poses.pdb",
      "mol2_path": "/app/visualization/ATP_poses.mol2"
    }
  ],
  "errors": [],
  "status": "ok"
}
```

**Data Flow:** Consumes docked ligands (and the already-prepared receptor
PDB, referenced as-is) → produces per-format pose files for the viewer/downloads

---

## Part 3: Service Dependencies and Execution Order

### Dependency Graph

```
User Input
    ↓
LLM Parser → determines which services
    ↓
Workflow Validator → checks for missing parameters
    ↓
Orchestrator executes in this ORDER:

1. GET_INPUT_FILES (if needed)
   ├─ Input: protein_id, ligand_ids (or protein_file, ligand_files for local input)
   ├─ Output: protein_path, ligand_paths
   └─ State saved: workflow_state["protein_file"], workflow_state["ligand_files"]

2. PREPARE_LIGANDS (depends on GET_INPUT_FILES)
   ├─ Input: ligand_files from state
   ├─ Output: prepared_ligands PDBQT paths
   └─ State saved: workflow_state["prepared_ligands"]

3. PREPARE_PROTEIN (depends on GET_INPUT_FILES)
   ├─ Input: protein_file from state
   ├─ Output: prepared_protein PDBQT path
   └─ State saved: workflow_state["prepared_protein"]

4. DOCK (depends on PREPARE_LIGANDS and PREPARE_PROTEIN)
   ├─ Input: prepared_protein, prepared_ligands, center, box_size
   ├─ Output: docking_results with poses and affinities
   └─ State saved: workflow_state["docking_results"]

5. VISUALIZE (depends on PREPARE_PROTEIN and optionally DOCK)
   ├─ Input: prepared_protein_pdb, docked ligand poses (from DOCK)
   ├─ Output: per-ligand PDB/SDF/MOL2 pose files (convert_docked_poses on LIGAND_PREPARER)
   └─ Complete!
```

### Critical Dependencies

**CANNOT RUN WITHOUT:**
- `prepare_ligands` requires output from `get_input_files`
- `prepare_protein` requires output from `get_input_files`
- `dock` requires output from both `prepare_ligands` AND `prepare_protein`
- `visualize` requires output from `prepare_protein` (and optionally `dock`)

### Data Flow Through Services

```
get_input_files → preparation → docking → visualization
   ↓                  ↓           ↓              ↓
 Input:          Consumes:   Consumes:     Consumes:
protein_id        ligand     prepared      docking
ligand_ids        files      molecules     results
                   protein    + params
                   files      (center,
                              box_size)
```

---

## Part 4: Required Parameters and Clarification

### Automatically Provided (from worflow parser)

These come from parsing the user's natural language command:

```json
"parameters": {
  "protein_id": "6lu7",           // Extracted from user input
  "ligand_ids": ["ATP", "HEM"],   // Extracted from user input
  "ph": 7.4,                      // Default
  "exhaustiveness": 8,            // Default
  "cpu": 1,                       // Default
  "population": 500,              // Default
  "num_modes": 15,                // Default
  "energy_range": 100.0,          // Default
  "add_hydrogens": true,          // Default
  "remove_waters": true,          // Default
  "formats": {...}                // Defaults
}
```

### Must Ask User For

These are **CRITICAL** and determine if docking can proceed:

| Parameter | Service | Type | Example | When Missing |
|-----------|---------|------|---------|--------------|
| `center` | moldina | [x, y, z] floats | [12.3, 34.5, -5.6] | Ask: "Where should the search box be centered?" |
| `box_size` | moldina | [x, y, z] floats | [20.0, 20.0, 20.0] | Ask: "What should the search box dimensions be?" |
| `protein_id` | get_input_files, dock | 4-char string | "6lu7" | Ask: "What protein PDB ID?" |
| `ligand_ids` | get_input_files, prepare | list of strings | ["ATP", "HEM"] | Ask: "Which ligands?" |

### Optional Parameters (Can Override Defaults)

| Parameter | Default | Range | Impact |
|-----------|---------|-------|--------|
| `ph` | 7.4 | 0-14 | Affects protonation state |
| `exhaustiveness` | 8 | 1-32 | Higher = slower but more thorough |
| `cpu` | 1 | 1-max | Parallel threads for docking |
| `population` | 500 | 100-4000 | PSO particles |
| `num_modes` | 15 | 1-20 | Number of binding poses |

---

## Part 5: Code References

### Service Selection
- **File:** [workflow_llm_parser.py](../services/llm_orchestrator/workflow_llm_parser.py)
- **Key:** `SYSTEM_PROMPT` - Contains service selection rules
- **Function:** `parse_workflow_with_llm()`

### Validation
- **File:** [workflow_definition.py](../services/llm_orchestrator/workflow_definition.py)
- **Key:** `REQUIRED_PARAMETERS_BY_STEP` dict - Defines required parameters per service
  (imported into [workflow_parser.py](../services/llm_orchestrator/workflow_parser.py), which uses it in `validate_workflow()`)
- **Function:** `validate_workflow()`

### Orchestration
- **File:** [orchestrator.py](../services/llm_orchestrator/orchestrator.py)
- **Key:** `execute_workflow()` - Runs services in order
- **Function:** `call_mcp_tool()` - Calls individual services

### Schemas
- **File:** [common/schemas.py](../common/schemas.py)
- **Key:** `JobSpec` - Complete job specification

---

## Part 6: Partial Rerun Invalidation Matrix

When a parameter changes in an interactive session, only the directly affected
steps are re-executed. The rerun planner lives in
[rerun_planner.py](../services/llm_orchestrator/rerun_planner.py).

### Parameter → invalidated steps

| Changed Parameter | get_input_files | prepare_ligands | prepare_protein | dock | visualize |
|---|---|---|---|---|---|
| `protein_id` | ✅ | | ✅ | ✅ | ✅ |
| `ligand_ids` | ✅ | ✅ | | ✅ | ✅ |
| `center` | | | | ✅ | ✅ |
| `box_size` | | | | ✅ | ✅ |
| `ph` | | ✅ | ✅ | ✅ | ✅ |
| `exhaustiveness` | | | | ✅ | ✅ |
| `cpu` | | | | ✅ | ✅ |
| `population` | | | | ✅ | ✅ |
| `num_modes` | | | | ✅ | ✅ |
| `energy_range` | | | | ✅ | ✅ |

### Examples

- **Change ligand**: reruns get_input_files → prepare_ligands → dock → visualize (protein preparation reused)
- **Change center/box_size**: reruns dock → visualize only (prepared molecules reused)
- **Change exhaustiveness**: reruns dock → visualize only

The planned steps are additionally intersected with the services from the
original workflow, so a step the user never requested will not be included.

---

## Part 7: Example Workflow

### Example: User says "Download 6lu7 and ATP, prepare, dock at 10,20,30 with box 25,30,35, and show results"

#### Step 1: LLM Parsing
```json
{
  "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
  "parameters": {
    "protein_id": "6lu7",
    "ligand_ids": ["ATP"],
    "center": [10.0, 20.0, 30.0],
    "box_size": [25.0, 30.0, 35.0],
    "ph": 7.4,
    ...defaults...
  },
  "missing_required": []
}
```

#### Step 2: Workflow Validation
- All required parameters present ✓
- All services can be executed ✓

#### Step 3: Orchestrator Execution

```
1️⃣ GET_INPUT_FILES
  Input: protein_id="6lu7", ligand_ids=["ATP"]
  Output: 6lu7.pdb, ATP.sdf
  State: workflow_state["protein_file"] = ".../6lu7.pdb"
         workflow_state["ligand_files"] = [".../ATP.sdf"]

2️⃣ PREPARE_LIGANDS
  Input: workflow_state["ligand_files"]
  Output: ATP.pdbqt
  State: workflow_state["prepared_ligands"] = [".../ATP.pdbqt"]

3️⃣ PREPARE_PROTEIN
  Input: workflow_state["protein_file"]
  Output: 6lu7.pdbqt
  State: workflow_state["prepared_protein"] = ".../6lu7.pdbqt"

4️⃣ DOCK
  Input: receptor=6lu7.pdbqt, ligands=[ATP.pdbqt], center, box_size
  Output: ATP_docking.pdbqt with affinity=-8.5
  State: workflow_state["docking_results"] = {...}

5️⃣ VISUALIZE
  Input: receptor=6lu7.pdbqt, ligands=[ATP_docking.pdbqt], affinities=[-8.5]
  Output: docking_viz.pml, docking_viz.png
  Complete! ✅
```

---

## Part 7: Troubleshooting

### "Missing required parameters"

**Problem:** User didn't provide enough information

**Solution:** Check [workflow_parser.py](../services/llm_orchestrator/workflow_parser.py) `get_missing_parameters()` and ask for:
- `protein_id` - "What is the protein PDB ID?"
- `ligand_ids` - "Which ligands to dock?"
- `center` - "What are the center coordinates [x, y, z]?"
- `box_size` - "What is the search box size [x, y, z]?"

### Service fails

**Problem:** One service in the chain fails

**Solution:** Check the error in the previous step's output. Each service depends on the prior ones:
- If `get_input_files` fails → nothing else can run
- If `prepare_ligands` fails → docking can't proceed
- If `prepare_protein` fails → docking can't proceed
- If `dock` fails → visualization uses prepared ligands instead

### No options for input/output specification

**Problem:** User asks "What inputs does prepare_ligands need?"

**Solution:** Direct them to the `REQUIRED_PARAMETERS_BY_STEP` dict in [workflow_definition.py](../services/llm_orchestrator/workflow_definition.py) (line ~50), or check this document's Part 2.

---

## Summary Table

| Service | Depends On | Required Input | Outputs | When Used |
|---------|-----------|----------------|---------|-----------|
| **get_input_files** | None | protein_id, ligand_ids | protein_path, ligand_paths | Always (if IDs or local files given) |
| **prepare_ligands** | get_input_files | ligand_paths | prepared_ligands (PDBQT) | When preparing or docking |
| **prepare_protein** | get_input_files | protein_path | prepared_protein (PDBQT) | When preparing or docking |
| **dock** | prepare_ligands, prepare_protein | receptor, ligands, center, box_size | docking_results | When docking requested |
| **visualize** | prepare_protein (+ dock optional) | receptor, ligands | visualization script, PNG | When visualization requested |

