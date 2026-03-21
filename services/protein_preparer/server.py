"""
Protein Preparer MCP Server

This server provides an MCP tool to prepare proteins for AutoDock Vina docking.
"""
import os
import logging
from typing import Dict, Any, List, Optional

from common.mcp_base import MCPServer, MCPTool, MCPToolParameter
from services.protein_preparer import ProteinPreparer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ProteinPreparerServer(MCPServer):
    """MCP Server for protein preparation."""
    
    def __init__(self, port: int = 8003, host: str = "0.0.0.0", cache_dir: str = "./cache"):
        super().__init__("protein_preparer", port, host)
        try:
            self.preparer = ProteinPreparer(cache_dir=cache_dir)
        except RuntimeError as e:
            logger.error(f"Failed to initialize ProteinPreparer: {e}")
            logger.error("Please install RDKit: pip install rdkit")
            raise
    
    def setup_tools(self):
        """Register the prepare_proteins tool."""
        
        def prepare_proteins_handler(
            protein_paths: list,
            output_dir: str = "./data/prepared",
            ph: float = 7.4,
            add_hydrogens: bool = True,
            remove_waters: bool = True,
            remove_heteroatoms: bool = False,
            keep_residues: Optional[list] = None
        ) -> dict:
            """Handler for prepare_proteins tool."""
            return self.preparer.prepare_proteins(
                protein_paths=protein_paths,
                output_dir=output_dir,
                ph=ph,
                add_hydrogens=add_hydrogens,
                remove_waters=remove_waters,
                remove_heteroatoms=remove_heteroatoms,
                keep_residues=keep_residues
            )
        
        self.register_tool(
            MCPTool(
                name="prepare_proteins",
                description="Prepare protein structures for AutoDock Vina docking",
                handler=prepare_proteins_handler,
                parameters=[
                    MCPToolParameter(
                        name="protein_paths",
                        type="array",
                        description="List of paths to protein PDB files to prepare",
                        required=True
                    ),
                    MCPToolParameter(
                        name="output_dir",
                        type="string",
                        description="Directory where prepared PDBQT files will be saved",
                        required=True
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
                        name="remove_waters",
                        type="boolean",
                        description="Remove water molecules (default: true)",
                        required=False
                    ),
                    MCPToolParameter(
                        name="remove_heteroatoms",
                        type="boolean",
                        description="Remove all heteroatoms/ligands (default: false)",
                        required=False
                    ),
                    MCPToolParameter(
                        name="keep_residues",
                        type="array",
                        description="List of residue names to keep even if heteroatoms (e.g., ['HEM', 'NAD'])",
                        required=False
                    )
                ]
            )
        )


def main():
    """Start the protein preparer MCP server."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Protein Preparer MCP Server")
    parser.add_argument("--port", type=int, default=8003, help="Server port (default: 8003)")
    parser.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    parser.add_argument("--cache-dir", default="./cache", help="Cache directory (default: ./cache)")
    
    args = parser.parse_args()
    
    try:
        server = ProteinPreparerServer(port=args.port, host=args.host, cache_dir=args.cache_dir)
        server.run()
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        raise


if __name__ == "__main__":
    main()
