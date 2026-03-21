"""
Moldina Runner MCP Server

This server provides Moldina molecular docking capabilities through MCP.
It handles PSO-based docking with WSL support on Windows.
"""
import os
import json
import logging
from typing import Any, Dict, List, Optional

from common.mcp_base import MCPServer, MCPTool, MCPToolParameter
from services.moldina_runner import MoldinaRunner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MoldinaRunnerServer(MCPServer):
    """MCP server for Moldina molecular docking"""
    
    def __init__(
        self,
        port: int = 8004,
        host: str = "0.0.0.0",
        cache_dir: str = None,
        moldina_path: str | None = None,
        wsl_distro: str | None = None,
    ):
        super().__init__("moldina_runner", port, host)
        self.cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".docking_cache", "moldina")
        self.moldina_path = moldina_path or os.environ.get("MOLDINA_PATH")
        # Prefer explicit arg, then env var, then default Ubuntu
        self.wsl_distro = wsl_distro or os.environ.get("MOLDINA_WSL_DISTRO") or "Ubuntu"
        self.runner = None
    
    def setup_tools(self):
        """Register the run_docking tool."""
        
        def run_docking_handler(
            receptor_path: str,
            ligand_paths: list,
            output_dir: str,
            center: list,
            box_size: list,
            cpu: int = 1,
            population: int = 500,
            exhaustiveness: int = 8,
            num_modes: int = 15,
            energy_range: float = 100.0
        ) -> dict:
            """Handler for run_docking tool."""
            # Initialize runner if needed
            if self.runner is None:
                try:
                    self.runner = MoldinaRunner(
                        cache_dir=self.cache_dir,
                        moldina_path=self.moldina_path,
                        wsl_distro=self.wsl_distro,
                    )
                except RuntimeError as e:
                    logger.error(f"Failed to initialize MoldinaRunner: {e}")
                    raise
            
            # Convert lists to tuples
            center_tuple = tuple(center)
            box_size_tuple = tuple(box_size)
            
            logger.info(f"Running Moldina docking: {len(ligand_paths)} ligands")
            
            # Run docking
            results = self.runner.run_moldina_docking(
                receptor_path=receptor_path,
                ligand_paths=ligand_paths,
                output_dir=output_dir,
                center=center_tuple,
                box_size=box_size_tuple,
                cpu=cpu,
                population=population,
                exhaustiveness=exhaustiveness,
                num_modes=num_modes,
                energy_range=energy_range
            )
            
            return results
        
        # Define tool
        docking_tool = MCPTool(
            name="run_docking",
            description="Run Moldina molecular docking with PSO optimization",
            handler=run_docking_handler,
            parameters=[
                MCPToolParameter(
                    name="receptor_path",
                    type="string",
                    description="Path to receptor PDBQT file",
                    required=True
                ),
                MCPToolParameter(
                    name="ligand_paths",
                    type="array",
                    description="List of paths to ligand PDBQT files",
                    required=True
                ),
                MCPToolParameter(
                    name="output_dir",
                    type="string",
                    description="Directory for output files",
                    required=True
                ),
                MCPToolParameter(
                    name="center",
                    type="array",
                    description="Search box center coordinates [x, y, z]",
                    required=True
                ),
                MCPToolParameter(
                    name="box_size",
                    type="array",
                    description="Search box dimensions [x, y, z] in Angstroms",
                    required=True
                ),
                MCPToolParameter(
                    name="cpu",
                    type="number",
                    description="Number of CPUs to use (default: 1)",
                    required=False
                ),
                MCPToolParameter(
                    name="population",
                    type="number",
                    description="Population size for PSO (default: 500, min: 5)",
                    required=False
                ),
                MCPToolParameter(
                    name="exhaustiveness",
                    type="number",
                    description="Search exhaustiveness (default: 8)",
                    required=False
                ),
                MCPToolParameter(
                    name="num_modes",
                    type="number",
                    description="Maximum number of binding modes (default: 15)",
                    required=False
                ),
                MCPToolParameter(
                    name="energy_range",
                    type="number",
                    description="Maximum energy difference for modes in kcal/mol (default: 100.0)",
                    required=False
                )
            ]
        )
        
        self.register_tool(docking_tool)


def main():
    """Run the MCP server"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Moldina Runner MCP Server")
    parser.add_argument("--port", type=int, default=8004, help="Server port (default: 8004)")
    parser.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    parser.add_argument("--cache-dir", default=None, help="Cache directory (default: ~/.docking_cache/moldina)")
    parser.add_argument(
        "--moldina-path",
        dest="moldina_path",
        default=None,
        help="Path to Moldina executable inside WSL/Linux (overrides auto-detect). Can also set env MOLDINA_PATH",
    )
    parser.add_argument(
        "--wsl-distro",
        dest="wsl_distro",
        default=None,
        help="WSL distribution name (default: Ubuntu). Can also set env MOLDINA_WSL_DISTRO",
    )
    
    args = parser.parse_args()
    
    try:
        server = MoldinaRunnerServer(
            port=args.port,
            host=args.host,
            cache_dir=args.cache_dir,
            moldina_path=args.moldina_path,
            wsl_distro=args.wsl_distro,
        )
        server.run()
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        raise


if __name__ == "__main__":
    main()
