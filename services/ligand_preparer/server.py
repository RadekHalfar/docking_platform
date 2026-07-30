"""
Ligand Preparer MCP Server

Exposes prepare_ligands tool via JSON-RPC.
"""
import logging
from common.config import get_service_port, get_cache_dir
from common.mcp_base import MCPServer
from common.mcp_tooling import build_tool_parameters, register_mcp_tool
from services.ligand_preparer import LigandPreparer
logger = logging.getLogger(__name__)


class LigandPreparerServer(MCPServer):
    """MCP Server for ligand preparation."""
    
    def __init__(self, port: int = 8002, host: str = "0.0.0.0", cache_dir: str = "./cache"):
        super().__init__("ligand_preparer", port, host)
        try:
            self.preparer = LigandPreparer(cache_dir=cache_dir)
        except RuntimeError as e:
            logger.error(f"Failed to initialize LigandPreparer: {e}")
            logger.error("Please install RDKit: pip install rdkit")
            raise
    
    def setup_tools(self):
        """Register the prepare_ligands tool."""
        
        def prepare_ligands_handler(
            ligand_paths: list,
            output_dir: str = "./data/prepared",
            ph: float = 7.4,
            add_hydrogens: bool = True,
            generate_3d: bool = True,
            optimize: bool = True
        ) -> dict:
            """Handler for prepare_ligands tool."""
            return self.preparer.prepare_ligands(
                ligand_paths=ligand_paths,
                output_dir=output_dir,
                ph=ph,
                add_hydrogens=add_hydrogens,
                generate_3d=generate_3d,
                optimize=optimize
            )
        
        register_mcp_tool(
            self,
            name="prepare_ligands",
            description=(
                "Prepare ligand molecules for AutoDock Vina docking (convert to PDBQT with "
                "proper charges and hydrogens). A multi-molecule SDF file (multiple "
                "$$$$-delimited records) expands into one prepared ligand per molecule."
            ),
            handler=prepare_ligands_handler,
            parameters=build_tool_parameters([
                ("ligand_paths", "array", "List of paths to ligand files (SDF, MOL, MOL2). A single SDF path may contain multiple molecules, each of which is prepared separately.", True),
                ("output_dir", "string", "Directory to save prepared ligands"),
                ("ph", "number", "pH for protonation state (default: 7.4)"),
                ("add_hydrogens", "boolean", "Add explicit hydrogens (default: true)"),
                ("generate_3d", "boolean", "Generate 3D coordinates if missing (default: true)"),
                ("optimize", "boolean", "Optimize 3D structure with force field (default: true)"),
            ]),
            returns={
                "prepared_ligands": "array",
                "total": "number",
                "total_ligands": "number",
                "successful": "number",
                "failed": "number",
                "errors": "array",
                "status": "string"
            },
        )


def main():
    """Run the Ligand Preparer MCP server."""
    server = LigandPreparerServer(
        port=get_service_port("ligand_preparer"),
        cache_dir=get_cache_dir(),
    )
    server.run()


if __name__ == "__main__":
    main()
