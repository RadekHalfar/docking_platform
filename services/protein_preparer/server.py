"""
Protein Preparer MCP Server

This server provides an MCP tool to prepare proteins for AutoDock Vina docking.
"""
import os
import logging
from typing import Optional

from common.config import CACHE_DIR_DEFAULT
from common.mcp_base import MCPServer
from common.mcp_tooling import build_tool_parameters, register_mcp_tool
from services.protein_preparer import ProteinPreparer
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
        
        register_mcp_tool(
            self,
            name="prepare_proteins",
            description="Prepare protein structures for AutoDock Vina docking",
            handler=prepare_proteins_handler,
            parameters=build_tool_parameters([
                ("protein_paths", "array", "List of paths to protein PDB files to prepare", True),
                ("output_dir", "string", "Directory where prepared PDBQT files will be saved", True),
                ("ph", "number", "pH for protonation state (default: 7.4)"),
                ("add_hydrogens", "boolean", "Add explicit hydrogens (default: true)"),
                ("remove_waters", "boolean", "Remove water molecules (default: true)"),
                ("remove_heteroatoms", "boolean", "Remove all heteroatoms/ligands (default: false)"),
                ("keep_residues", "array", "List of residue names to keep even if heteroatoms (e.g., ['HEM', 'NAD'])"),
            ]),
        )


def main():
    """Start the protein preparer MCP server."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Protein Preparer MCP Server")
    parser.add_argument("--port", type=int, default=8003, help="Server port (default: 8003)")
    parser.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    parser.add_argument("--cache-dir", default=CACHE_DIR_DEFAULT, help=f"Cache directory (default: {CACHE_DIR_DEFAULT})")
    
    args = parser.parse_args()

    server = ProteinPreparerServer(port=args.port, host=args.host, cache_dir=args.cache_dir)
    server.run()


if __name__ == "__main__":
    main()
