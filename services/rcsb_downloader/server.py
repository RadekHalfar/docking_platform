"""
RCSB Downloader MCP Server

Exposes download_structures tool via JSON-RPC.
"""
import os
import logging
from common.mcp_base import MCPServer, MCPTool, MCPToolParameter
from services.rcsb_downloader import RCSBDownloader

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RCSBDownloaderServer(MCPServer):
    """MCP Server for RCSB PDB downloads."""
    
    def __init__(self, port: int = 8001, host: str = "0.0.0.0", cache_dir: str = "./cache"):
        super().__init__("rcsb_downloader", port, host)
        self.downloader = RCSBDownloader(cache_dir=cache_dir)
    
    def setup_tools(self):
        """Register the download_structures tool."""
        
        def download_structures_handler(
            protein_id: str,
            ligand_ids: list = None,
            formats: dict = None,
            save_dir: str = "./data"
        ) -> dict:
            """Handler for download_structures tool."""
            return self.downloader.download_structures(
                protein_id=protein_id,
                ligand_ids=ligand_ids,
                formats=formats,
                save_dir=save_dir
            )
        
        # Define tool
        download_tool = MCPTool(
            name="download_structures",
            description="Download protein structures and ligands from RCSB PDB",
            parameters=[
                MCPToolParameter(
                    name="protein_id",
                    type="string",
                    description="PDB identifier (4 characters, e.g., '1ABC')",
                    required=True
                ),
                MCPToolParameter(
                    name="ligand_ids",
                    type="array",
                    description="List of ligand identifiers (e.g., ['ATP', 'HEM'])",
                    required=False
                ),
                MCPToolParameter(
                    name="formats",
                    type="object",
                    description="File formats: {'protein': 'pdb'|'cif', 'ligand': 'sdf'|'mol'}",
                    required=False
                ),
                MCPToolParameter(
                    name="save_dir",
                    type="string",
                    description="Directory to save downloaded files",
                    required=False
                )
            ],
            handler=download_structures_handler,
            returns={
                "protein_path": "string",
                "ligand_paths": "array",
                "sources": "object",
                "checksums": "object",
                "status": "string"
            }
        )
        
        self.register_tool(download_tool)


def main():
    """Run the RCSB Downloader MCP server."""
    port = int(os.getenv("RCSB_DOWNLOADER_PORT", "8001"))
    cache_dir = os.getenv("CACHE_DIR", "./cache")
    
    server = RCSBDownloaderServer(port=port, cache_dir=cache_dir)
    server.run()


if __name__ == "__main__":
    main()
