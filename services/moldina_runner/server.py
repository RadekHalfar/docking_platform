"""
Moldina Runner MCP Server

This server provides Moldina molecular docking capabilities through MCP.
It runs Moldina's PSO search (default) or the binary's classic Vina Monte
Carlo search, with WSL support on Windows.
"""
import os
import logging

from common.docking_algorithm import DEFAULT_DOCKING_ALGORITHM
from common.mcp_base import MCPServer
from common.mcp_bootstrap import build_service_arg_parser, resolve_port
from common.mcp_tooling import build_tool_parameters, register_mcp_tool
from services.moldina_runner import MoldinaRunner
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
            energy_range: float = 100.0,
            parallel: bool = False,
            max_workers: int = 1,
            docking_algorithm: str = DEFAULT_DOCKING_ALGORITHM,
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
            
            logger.info(f"Running {docking_algorithm} docking: {len(ligand_paths)} ligands")
            
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
                energy_range=energy_range,
                parallel=parallel,
                max_workers=max_workers,
                docking_algorithm=docking_algorithm,
            )
            
            return results
        
        register_mcp_tool(
            self,
            name="run_docking",
            description=(
                "Run molecular docking with the Moldina binary, using Moldina's PSO search "
                "(docking_algorithm='moldina', default) or classic Vina Monte Carlo search "
                "(docking_algorithm='vina'). Ligands are docked "
                "one at a time by default; set parallel=true to dock up to max_workers "
                "ligands concurrently instead."
            ),
            handler=run_docking_handler,
            parameters=build_tool_parameters([
                ("receptor_path", "string", "Path to receptor PDBQT file", True),
                ("ligand_paths", "array", "List of paths to ligand PDBQT files", True),
                ("output_dir", "string", "Directory for output files", True),
                ("center", "array", "Search box center coordinates [x, y, z]", True),
                ("box_size", "array", "Search box dimensions [x, y, z] in Angstroms", True),
                ("cpu", "number", "CPUs per ligand; Vina mode only, PSO ignores it (default: 1)"),
                ("population", "number", "Population size for PSO, ignored in Vina mode (default: 500, min: 5)"),
                ("exhaustiveness", "number", "Search exhaustiveness (default: 8)"),
                ("num_modes", "number", "Maximum number of binding modes (default: 15)"),
                ("energy_range", "number", "Maximum energy difference for modes in kcal/mol (default: 100.0)"),
                ("parallel", "boolean", "Dock ligands concurrently, up to max_workers at a time, instead of one at a time (default: false)"),
                ("max_workers", "number", "Maximum number of ligands to dock concurrently when parallel is true; clamped to the container's CPU quota divided by cpu in Vina mode (PSO uses 1 CPU per ligand) (default: 1)"),
                ("docking_algorithm", "string", "Search algorithm: 'moldina' (PSO, default) or 'vina' (classic Monte Carlo search)"),
            ]),
        )


def main():
    """Run the MCP server"""
    parser = build_service_arg_parser(
        "moldina_runner", "Moldina Runner MCP Server",
        cache_dir_help="Cache directory (default: ~/.docking_cache/moldina)",
    )
    # --cache-dir's default is intentionally left as None here (not resolved
    # via common.config.get_cache_dir()) -- MoldinaRunner's own default,
    # ~/.docking_cache/moldina, differs from the other services' ./cache and
    # is applied inside MoldinaRunnerServer/__init__ itself when None.
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

    server = MoldinaRunnerServer(
        host=args.host,
        port=resolve_port("moldina_runner", args.port),
        cache_dir=args.cache_dir,
        moldina_path=args.moldina_path,
        wsl_distro=args.wsl_distro,
    )
    server.run()


if __name__ == "__main__":
    main()
