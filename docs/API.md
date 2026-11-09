# MCP Docking System API Documentation

## Overview

This document describes the JSON-RPC 2.0 API for all MCP servers in the docking system.

All communication uses JSON-RPC 2.0 protocol over HTTP POST to `/mcp` endpoint.

## Common Endpoints

All services implement these standard endpoints:

Implementation note:
Service MCP tool declarations are now built via shared helpers in `common/mcp_tooling.py` to keep parameter schemas consistent across services. This does not change the wire API.

### Health Check
```
GET /health
```

Response:
```json
{
  "status": "ok",
  "service": "service_name"
}
```

### List Tools
```json
{
  "jsonrpc": "2.0",
  "id": "request_id",
  "method": "tools/list"
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": "request_id",
  "result": {
    "tools": [
      {
        "name": "tool_name",
        "description": "Tool description",
        "parameters": [...],
        "returns": {...}
      }
    ]
  }
}
```

## 1. RCSB Downloader Service

**Port:** 8001  
**Base URL:** `http://localhost:8001`

### Tool: download_structures

Downloads protein structures and ligand molecules from RCSB PDB.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `protein_id` | string | No | PDB identifier (4 characters, e.g., '1ABC'). Omit to skip the protein download (e.g. with a local protein file) |
| `ligand_ids` | array | No | List of ligand identifiers or names (e.g., ['ATP', 'HEM', 'aspirin']) |
| `enable_name_resolution` | boolean | No | Resolve ligand names to chem_comp IDs via RCSB search (default: false; the interactive flow uses `search_ligands` plus a user choice instead) |
| `formats` | object | No | File formats: `{"protein": "pdb"\|"cif", "ligand": "sdf"\|"mol"}` |
| `save_dir` | string | No | Directory to save files (default: "./data") |

**Request Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_1",
  "method": "tool_call",
  "params": {
    "name": "download_structures",
    "arguments": {
      "protein_id": "1ABC",
      "ligand_ids": ["ATP", "HEM"],
      "formats": {
        "protein": "pdb",
        "ligand": "sdf"
      },
      "save_dir": "/data/jobs/job_001/raw"
    }
  }
}
```

**Response Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_1",
  "result": {
    "protein_path": "/data/jobs/job_001/raw/1ABC.pdb",
    "ligand_paths": [
      "/data/jobs/job_001/raw/ATP.sdf",
      "/data/jobs/job_001/raw/HEM.sdf"
    ],
    "sources": {
      "protein": "https://files.rcsb.org/download/1ABC.pdb",
      "ligands": [
        "https://files.rcsb.org/ligands/view/ATP_ideal.sdf",
        "https://files.rcsb.org/ligands/view/HEM_ideal.sdf"
      ]
    },
    "checksums": {
      "protein": "a1b2c3d4e5f6...",
      "ligands": ["g7h8i9j0k1l2...", "m3n4o5p6q7r8..."]
    },
    "status": "ok"
  }
}
```

**Error Response:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_1",
  "error": {
    "code": -32000,
    "message": "Tool execution failed: Invalid PDB ID: INVALID"
  }
}
```

An invalid protein ID fails the whole call. A ligand that can't be
downloaded doesn't: it is logged and left out of `ligand_paths`.

### Tool: search_ligands

Searches the names in RCSB's chemical component dictionary (`chem_comp.name`,
usually the systematic name) and returns ranked component-ID candidates (used
by the CLI's ligand search, see
[LLM_ORCHESTRATION.md](LLM_ORCHESTRATION.md#search-a-ligand-by-name-phase-9);
the GUI doesn't call it).

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | string | Yes | Ligand search text (e.g., 'adenosine') |
| `max_results` | number | No | Maximum number of candidates (default: 10) |

No match gives an empty `candidates` list.

**Response Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "search_1",
  "result": {
    "query": "adenosine",
    "candidates": [
      {"component_id": "A1II9", "name": "A1II9", "score": 1.0},
      {"component_id": "ADN", "name": "ADN", "score": 0.959},
      {"component_id": "AMP", "name": "AMP", "score": 0.917}
    ]
  }
}
```

---

## 2. Ligand Preparer Service

**Port:** 8002  
**Base URL:** `http://localhost:8002`

### Tool: prepare_ligands

Prepares ligand molecules for molecular docking by converting them to PDBQT format with proper charges, hydrogens, and rotatable bonds. Compatible with Moldina and AutoDock Vina.

**Preparation Steps:**
1. Read ligand from SDF/MOL/MOL2 file
2. Add explicit hydrogens at specified pH
3. Generate 3D coordinates if missing
4. Optimize geometry with MMFF force field
5. Assign Gasteiger partial charges
6. Detect and mark rotatable bonds
7. Convert to PDBQT format

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `ligand_paths` | array | Yes | List of paths to ligand files (SDF, MOL, MOL2, or an already-prepared PDBQT, used as-is with `"conversion_method": "provided"`) |
| `output_dir` | string | No | Directory to save prepared ligands (default: "./data/prepared") |
| `ph` | number | No | pH for protonation state (default: 7.4) |
| `add_hydrogens` | boolean | No | Add explicit hydrogens (default: true) |
| `generate_3d` | boolean | No | Generate 3D coordinates if missing (default: true) |
| `optimize` | boolean | No | Optimize 3D structure with force field (default: true) |

**Request Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_2",
  "method": "tool_call",
  "params": {
    "name": "prepare_ligands",
    "arguments": {
      "ligand_paths": [
        "/data/jobs/job_001/raw/ATP.sdf",
        "/data/jobs/job_001/raw/HEM.sdf"
      ],
      "output_dir": "/data/jobs/job_001/prepared",
      "ph": 7.4,
      "add_hydrogens": true,
      "optimize": true
    }
  }
}
```

**Response Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_2",
  "result": {
    "prepared_ligands": [
      {
        "id": "ATP",
        "input_path": "/data/jobs/job_001/raw/ATP.sdf",
        "pdb_path": "/data/jobs/job_001/prepared/ATP_prepared.pdb",
        "pdbqt_path": "/data/jobs/job_001/prepared/ATP.pdbqt",
        "checksum": "a1b2c3d4e5f6...",
        "rotatable_bonds": 8,
        "conversion_method": "meeko",
        "preparation_params": {
          "ph": 7.4,
          "add_hydrogens": true,
          "generate_3d": true,
          "optimize": true
        }
      },
      {
        "id": "HEM",
        "input_path": "/data/jobs/job_001/raw/HEM.sdf",
        "pdb_path": "/data/jobs/job_001/prepared/HEM_prepared.pdb",
        "pdbqt_path": "/data/jobs/job_001/prepared/HEM.pdbqt",
        "checksum": "g7h8i9j0k1l2...",
        "rotatable_bonds": 12,
        "conversion_method": "meeko",
        "preparation_params": {
          "ph": 7.4,
          "add_hydrogens": true,
          "generate_3d": true,
          "optimize": true
        }
      }
    ],
    "total": 2,
    "total_ligands": 2,
    "successful": 2,
    "failed": 0,
    "errors": [],
    "status": "ok"
  }
}
```

`total` counts input files; `total_ligands` counts prepared molecules (a
multi-molecule SDF file yields several).

**Error Response Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_2",
  "result": {
    "prepared_ligands": [
      {
        "id": "ATP",
        "pdbqt_path": "/data/jobs/job_001/prepared/ATP.pdbqt",
        "rotatable_bonds": 8
      }
    ],
    "total": 2,
    "successful": 1,
    "failed": 1,
    "errors": [
      {
        "ligand": "/data/jobs/job_001/raw/INVALID.sdf",
        "error": "Failed to read molecule from file"
      }
    ],
    "status": "ok"
  }
}
```

**Notes:**
- Requires RDKit (install via pip: `pip install rdkit`)
- Optionally uses Meeko for PDBQT conversion (recommended)
- Falls back to OpenBabel or manual conversion if Meeko unavailable
- PDBQT files include TORSDOF (torsional degrees of freedom) for Moldina/Vina
- See [DOCKING_PARAMETERS.md § Stage 2](DOCKING_PARAMETERS.md#stage-2-ligand-preparation) for tuning guidance on `ph`/`add_hydrogens`/`optimize`

### Tool: convert_docked_poses

Converts each ligand's docked-pose PDBQT (every Vina/Moldina mode, as one
multi-model file) into one multi-pose file per requested format, for the
GUI's in-browser 3Dmol.js viewer and its per-format download picker. This is
the tool behind the `visualize` workflow step — see [GUI_GUIDE.md](GUI_GUIDE.md).

**Conversion:**
1. Parse the docked-pose PDBQT with Meeko (`PDBQTMolecule` + `RDKitMolCreate`), restoring proper bond orders/aromaticity from the `REMARK SMILES` Meeko embedded when the ligand was originally prepared — a single parse is reused for every requested format.
2. Write SDF/PDB directly from that RDKit molecule (one record/MODEL per pose, tagged in the SDF with that pose's `mode_index`/`affinity`/`rmsd_lb`/`rmsd_ub`).
3. Derive MOL2 from the SDF just written via OpenBabel (so it inherits the restored bond orders instead of OpenBabel re-guessing them from raw PDBQT geometry).
4. If the Meeko round trip fails for a ligand (e.g. it wasn't originally prepared by Meeko), fall back to converting each requested format independently straight from the PDBQT via OpenBabel — still usable, but without per-pose affinity/RMSD metadata.

PDBQT itself is never produced by this tool — it's already the native,
unconverted file `run_docking` wrote.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `pose_results` | array | Yes | Per-ligand docking result dicts as produced by `run_docking` (`ligand_id`, `output_path` pointing at the multi-model docked-pose PDBQT, `modes`) |
| `output_dir` | string | Yes | Directory to write `<ligand_id>_poses.<format>` files to |
| `formats` | array | No | Which formats to write: any of `"sdf"`, `"pdb"`, `"mol2"` (default: all three) |

**Request Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_5",
  "method": "tool_call",
  "params": {
    "name": "convert_docked_poses",
    "arguments": {
      "pose_results": [
        {
          "ligand_id": "ATP",
          "output_path": "/data/jobs/job_001/docking_results/ATP_out.pdbqt",
          "modes": [
            {"mode": 1, "affinity": -8.5, "rmsd_lb": 0.0, "rmsd_ub": 0.0},
            {"mode": 2, "affinity": -7.9, "rmsd_lb": 1.8, "rmsd_ub": 2.6}
          ]
        }
      ],
      "output_dir": "/data/jobs/job_001/visualization"
    }
  }
}
```

**Response Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_5",
  "result": {
    "converted": [
      {
        "ligand_id": "ATP",
        "num_poses": 2,
        "conversion_method": "meeko",
        "sdf_path": "/data/jobs/job_001/visualization/ATP_poses.sdf",
        "pdb_path": "/data/jobs/job_001/visualization/ATP_poses.pdb",
        "mol2_path": "/data/jobs/job_001/visualization/ATP_poses.mol2"
      }
    ],
    "errors": [],
    "status": "ok"
  }
}
```

**Notes:**
- Requires RDKit + Meeko for the primary path; OpenBabel for the MOL2 derivation step and the fallback path
- A ligand's entry only carries `<format>_path` keys for formats that were actually written — check for a key's presence rather than assuming all three exist
- `conversion_method` is `"meeko"` (bond orders restored, per-pose metadata included) or `"openbabel"` (fallback, no per-pose metadata)

---

## 3. Protein Preparer Service

**Port:** 8003  
**Base URL:** `http://localhost:8003`

### Tool: prepare_proteins

Prepares protein structures for docking by cleaning, adding hydrogens, and converting to PDBQT format.

**Preparation Steps:**
1. Read protein from a PDB or mmCIF file (`.cif`/`.mmcif` is converted to
   PDB with gemmi; a multi-model file keeps its first model)
2. Remove water molecules (optional)
3. Remove non-protein heteroatoms (optional)
4. Add explicit hydrogens at specified pH
5. Convert to a rigid-receptor PDBQT (AutoDock atom types; charges written as
   0.000, which Vina-style scoring doesn't use)

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `protein_paths` | array | Yes | List of paths to protein files: `.pdb`, `.cif` or `.mmcif` |
| `output_dir` | string | Yes | Directory to save prepared proteins |
| `ph` | number | No | pH for protonation state (default: 7.4) |
| `remove_waters` | boolean | No | Remove water molecules (default: true) |
| `remove_heteroatoms` | boolean | No | Remove all heteroatoms/ligands (default: false) |
| `keep_residues` | array | No | Residue names to keep when removing heteroatoms (e.g., ['HEM', 'ZN']) |
| `add_hydrogens` | boolean | No | Add explicit hydrogens (default: true) |

**Request Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_2b",
  "method": "tool_call",
  "params": {
    "name": "prepare_proteins",
    "arguments": {
      "protein_paths": ["/data/jobs/job_001/raw/6lu7.pdb"],
      "output_dir": "/data/jobs/job_001/prepared",
      "ph": 7.4,
      "remove_waters": true,
      "remove_heteroatoms": false,
      "add_hydrogens": true
    }
  }
}
```

**Response Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_2b",
  "result": {
    "prepared_proteins": [
      {
        "id": "6lu7",
        "input_path": "/data/jobs/job_001/raw/6lu7.pdb",
        "pdb_path": "/data/jobs/job_001/prepared/6lu7_prepared.pdb",
        "pdbqt_path": "/data/jobs/job_001/prepared/6lu7.pdbqt",
        "initial_atoms": 2367,
        "final_atoms": 4897,
        "atoms_removed": -2530,
        "conversion_method": "manual_rigid",
        "checksum": "x1y2z3a4b5c6...",
        "preparation_params": {
          "ph": 7.4,
          "add_hydrogens": true,
          "remove_waters": true,
          "remove_heteroatoms": false,
          "keep_residues": []
        }
      }
    ],
    "total": 1,
    "successful": 1,
    "failed": 0,
    "errors": [],
    "status": "ok"
  }
}
```

**Notes:**
- See [DOCKING_PARAMETERS.md § Stage 3](DOCKING_PARAMETERS.md#stage-3-protein-preparation) for tuning guidance on `ph`/`remove_waters`/`remove_heteroatoms`/`keep_residues`
- See [PROTEIN_PREPARATION.md](PROTEIN_PREPARATION.md) for the preparation pipeline this tool wraps

---

## 4. Moldina Runner Service

**Port:** 8004  
**Base URL:** `http://localhost:8004`

### Tool: run_docking

Performs molecular docking with the Moldina binary via WSL on Windows or natively on Linux, using either Moldina's PSO search (`docking_algorithm="moldina"`, default) or the binary's AutoDock Vina Monte Carlo search (`docking_algorithm="vina"`) — see [MOLDINA_DOCKING.md § Vina mode](MOLDINA_DOCKING.md#vina-mode).

**Docking Process:**
1. Write the search box to `center.txt` and the run's settings to `parameters.txt`
2. Run one Moldina process per ligand (PSO mode adds `--population N --search_alg_PSO`; Vina mode omits both)
3. Parse docking results and affinities
4. Return sorted results (best affinity first)

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `receptor_path` | string | Yes | Path to receptor PDBQT file |
| `ligand_paths` | array | Yes | List of paths to ligand PDBQT files |
| `output_dir` | string | Yes | Directory for output files |
| `center` | array | Yes | Search box center coordinates `[x, y, z]` in Angstroms |
| `box_size` | array | Yes | Search box dimensions `[x, y, z]` in Angstroms |
| `docking_algorithm` | string | No | `"moldina"` (PSO, default) or `"vina"` (Monte Carlo); aliases such as `"pso"`/`"autodock vina"` are normalised, anything else is an error |
| `cpu` | integer | No | Number of CPUs per ligand (default: 1; Vina mode only — PSO ignores it) |
| `population` | integer | No | PSO population size (default: 500, min: 5; Moldina mode only) |
| `exhaustiveness` | integer | No | Search exhaustiveness (default: 8; Vina mode only — PSO ignores it) |
| `num_modes` | integer | No | Maximum binding modes to output (default: 15) |
| `energy_range` | number | No | Max energy difference for modes in kcal/mol (default: 100.0) |
| `parallel` | boolean | No | Dock up to `max_workers` ligands concurrently (default: false) |
| `max_workers` | integer | No | Concurrent ligands when `parallel` is true (default: 1); clamped to the ligand count and to the container's CPU budget divided by `cpu` in Vina mode (PSO uses 1 CPU per ligand); the count actually used is returned as `workers` |

**Request Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_3",
  "method": "tool_call",
  "params": {
    "name": "run_docking",
    "arguments": {
      "receptor_path": "/data/jobs/job_001/prepared/6lu7.pdbqt",
      "ligand_paths": [
        "/data/jobs/job_001/prepared/ATP.pdbqt",
        "/data/jobs/job_001/prepared/HEM.pdbqt"
      ],
      "output_dir": "/data/jobs/job_001/docking",
      "center": [10.5, 20.3, -5.2],
      "box_size": [25.0, 30.0, 35.0],
      "docking_algorithm": "moldina",
      "cpu": 1,
      "population": 500,
      "exhaustiveness": 16,
      "num_modes": 15,
      "energy_range": 100.0
    }
  }
}
```

**Response Example:**
```json
{
  "jsonrpc": "2.0",
  "id": "job_001_step_3",
  "result": {
    "status": "ok",
    "stopped": false,
    "docking_algorithm": "moldina",
    "workers": 1,
    "skipped": 0,
    "receptor": "/data/jobs/job_001/prepared/6lu7.pdbqt",
    "total": 2,
    "successful": 2,
    "failed": 0,
    "errors": [],
    "config_file": "/data/jobs/job_001/docking/center.txt",
    "parameters_file": "/data/jobs/job_001/docking/parameters.txt",
    "output_dir": "/data/jobs/job_001/docking",
    "results": [
      {
        "ligand_id": "ATP",
        "ligand_path": "/data/jobs/job_001/prepared/ATP.pdbqt",
        "output_path": "/data/jobs/job_001/docking/ATP_out.pdbqt",
        "log_path": null,
        "best_affinity": -8.5,
        "num_modes_found": 2,
        "checksum": "m3n4o5p6q7r8...",
        "modes": [
          {
            "mode": 1,
            "affinity": -8.5,
            "rmsd_lb": 0.0,
            "rmsd_ub": 0.0
          },
          {
            "mode": 2,
            "affinity": -8.2,
            "rmsd_lb": 1.234,
            "rmsd_ub": 3.456
          }
        ]
      },
      {
        "ligand_id": "HEM",
        "ligand_path": "/data/jobs/job_001/prepared/HEM.pdbqt",
        "output_path": "/data/jobs/job_001/docking/HEM_out.pdbqt",
        "log_path": null,
        "best_affinity": -7.2,
        "num_modes_found": 1,
        "checksum": "s9t0u1v2w3x4...",
        "modes": [
          {
            "mode": 1,
            "affinity": -7.2,
            "rmsd_lb": 0.0,
            "rmsd_ub": 0.0
          }
        ]
      }
    ]
  }
}
```

**Notes:**
- Moldina is a Linux binary. In Docker nothing more is needed; running the service natively on Windows needs WSL
- `docking_algorithm` picks the search: Particle Swarm Optimization (`moldina`, default) or the classic Vina Monte Carlo search (`vina`), both in the same binary
- Results are sorted by best_affinity (most negative = best binding)
- Each ligand's Moldina stdout/stderr is in `<ligand_id>_moldina_output.log`
  in `output_dir`. `log_path` names a `<ligand_id>_out.log` only if one
  exists, and the runner doesn't ask Moldina to write one, so it is `null`
- `errors` holds one message per ligand whose Moldina run failed
- A run can be stopped mid-batch by creating `STOP_REQUESTED` in `output_dir`:
  no further ligands start, running Moldina processes are killed, and the
  response has `"status": "stopped"`, `"stopped": true` and `skipped` =
  ligands never docked (see `docs/MOLDINA_DOCKING.md` → "Stopping a run")
- `workers` is how many ligands were docked at a time after the server's
  clamp (added in Review Stage R1, Phase 15); `benchmark_docking.py` labels
  its rows with it. Older servers don't return it.
- Population size should be ≥ 5; recommended: 100 × rotatable_bonds
- Higher exhaustiveness and population = more thorough but slower
- See `docs/MOLDINA_DOCKING.md` for the engine guide, and
  [DOCKING_PARAMETERS.md § Stage 4](DOCKING_PARAMETERS.md#stage-4-molecular-docking)
  for tuning guidance and ranges for every parameter above

---

## Error Codes

The codes `common/mcp_base.py` returns:

| Code | Message | When |
|------|---------|------|
| -32700 | Parse error | The body isn't valid JSON |
| -32600 | Invalid Request | `jsonrpc` isn't `"2.0"` |
| -32601 | Method not found / Tool not found | Unknown method, or unknown tool name |
| -32602 | Invalid params | No tool `name`, or a missing required argument |
| -32603 | Internal error | Unexpected server error |
| -32000 | Tool execution failed | The tool raised (e.g. an invalid PDB ID); the message carries the error |
| -32003 | Timeout | The tool ran past `MAX_JOB_RUNTIME_SECONDS` (default 3600 s); it keeps running in the background |

`JSONRPCErrorCodes` also defines -32001 (validation) and -32002 (resource
not found), but no service returns them.

---

## Client Usage Example (Python)

```python
import requests

def call_mcp_tool(service_url, tool_name, arguments, request_id="1"):
    """Call an MCP tool via JSON-RPC."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tool_call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    
    response = requests.post(f"{service_url}/mcp", json=payload)
    response.raise_for_status()
    
    data = response.json()
    if "error" in data:
        raise Exception(f"MCP Error: {data['error']['message']}")
    
    return data["result"]


# Download protein and ligands
result = call_mcp_tool(
    "http://localhost:8001",
    "download_structures",
    {
        "protein_id": "1ABC",
        "ligand_ids": ["ATP"],
        "save_dir": "./data/test"
    }
)

print(f"Downloaded protein: {result['protein_path']}")
```
