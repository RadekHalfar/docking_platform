"""
RCSB Downloader MCP Server

Exposes download_structures tool via JSON-RPC.
"""
from typing import Optional
from common.config import get_service_port, get_cache_dir
from common.mcp_base import MCPServer
from common.mcp_tooling import build_tool_parameters, register_mcp_tool
from services.rcsb_downloader import RCSBDownloader


class RCSBDownloaderServer(MCPServer):
    """MCP Server for RCSB PDB downloads."""
    
    def __init__(self, port: int = 8001, host: str = "0.0.0.0", cache_dir: str = "./cache"):
        super().__init__("rcsb_downloader", port, host)
        self.downloader = RCSBDownloader(cache_dir=cache_dir)
    
    def setup_tools(self):
        """Register the download_structures tool."""

        def search_ligands_handler(name: str, max_results: int = 10) -> dict:
            """Handler for search_ligands tool."""
            return {
                "query": name,
                "candidates": self.downloader.search_ligands(name=name, max_results=max_results),
            }
        
        def download_structures_handler(
            protein_id: Optional[str] = None,
            ligand_ids: Optional[list] = None,
            enable_name_resolution: bool = False,
            formats: Optional[dict] = None,
            save_dir: str = "./data"
        ) -> dict:
            """Handler for download_structures tool."""
            return self.downloader.download_structures(
                protein_id=protein_id,
                ligand_ids=ligand_ids,
                enable_name_resolution=enable_name_resolution,
                formats=formats,
                save_dir=save_dir
            )

        register_mcp_tool(
            self,
            name="search_ligands",
            description="Search RCSB ligands by common name and return ranked component ID candidates",
            handler=search_ligands_handler,
            parameters=build_tool_parameters([
                ("name", "string", "Ligand search text (e.g., 'aspirin')", True),
                ("max_results", "number", "Maximum number of candidates to return (default: 10)"),
            ]),
            returns={
                "query": "string",
                "candidates": "array",
            },
        )
        
        register_mcp_tool(
            self,
            name="download_structures",
            description="Download protein structures and ligands from RCSB PDB",
            handler=download_structures_handler,
            parameters=build_tool_parameters([
                ("protein_id", "string", "PDB identifier (4 characters, e.g., '1ABC'). Omit to skip protein download (e.g. when supplying a local protein file)."),
                ("ligand_ids", "array", "List of ligand identifiers or names (e.g., ['ATP', 'HEM', 'aspirin'])"),
                ("enable_name_resolution", "boolean", "Resolve ligand names to chem_comp IDs via RCSB search (default: false; use search_ligands + user selection for interactive flow)"),
                ("formats", "object", "File formats: {'protein': 'pdb'|'cif', 'ligand': 'sdf'|'mol'}"),
                ("save_dir", "string", "Directory to save downloaded files"),
            ]),
            returns={
                "protein_path": "string",
                "ligand_paths": "array",
                "sources": "object",
                "checksums": "object",
                "status": "string"
            },
        )


def main():
    """Run the RCSB Downloader MCP server."""
    server = RCSBDownloaderServer(
        port=get_service_port("rcsb_downloader"),
        cache_dir=get_cache_dir(),
    )
    server.run()


if __name__ == "__main__":
    main()
