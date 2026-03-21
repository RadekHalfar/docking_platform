"""
Ligand Preparer MCP Server

Exposes prepare_ligands tool via JSON-RPC.
"""
import os
import logging
from common.mcp_base import MCPServer, MCPTool, MCPToolParameter
from services.ligand_preparer import LigandPreparer

logging.basicConfig(level=logging.INFO)
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
        
        # Define tool
        prepare_tool = MCPTool(
            name="prepare_ligands",
            description="Prepare ligand molecules for AutoDock Vina docking (convert to PDBQT with proper charges and hydrogens)",
            parameters=[
                MCPToolParameter(
                    name="ligand_paths",
                    type="array",
                    description="List of paths to ligand files (SDF, MOL, MOL2)",
                    required=True
                ),
                MCPToolParameter(
                    name="output_dir",
                    type="string",
                    description="Directory to save prepared ligands",
                    required=False
                ),
                MCPToolParameter(
                    name="ph",
                    type="number",
                    description="pH for protonation state (default: 7.4)",
                    required=False
                ),
                MCPToolParameter(
                    name="add_hydrogens",
                    type="boolean",
                    description="Add explicit hydrogens (default: true)",
                    required=False
                ),
                MCPToolParameter(
                    name="generate_3d",
                    type="boolean",
                    description="Generate 3D coordinates if missing (default: true)",
                    required=False
                ),
                MCPToolParameter(
                    name="optimize",
                    type="boolean",
                    description="Optimize 3D structure with force field (default: true)",
                    required=False
                )
            ],
            handler=prepare_ligands_handler,
            returns={
                "prepared_ligands": "array",
                "total": "number",
                "successful": "number",
                "failed": "number",
                "errors": "array",
                "status": "string"
            }
        )
        
        self.register_tool(prepare_tool)


def main():
    """Run the Ligand Preparer MCP server."""
    port = int(os.getenv("LIGAND_PREPARER_PORT", "8002"))
    cache_dir = os.getenv("CACHE_DIR", "./cache")
    
    server = LigandPreparerServer(port=port, cache_dir=cache_dir)
    server.run()


if __name__ == "__main__":
    main()
