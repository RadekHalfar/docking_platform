"""
PyMOL Visualizer MCP Server

Provides PyMOL visualization capabilities as an MCP service.
"""
import argparse
from common.mcp_base import MCPServer, MCPTool, MCPToolParameter
from services.pymol_visualizer import PyMOLVisualizer


class PyMOLVisualizerServer(MCPServer):
    """MCP Server for PyMOL visualization."""
    
    def __init__(self, host='0.0.0.0', port=8005, pymol_path=None):
        self.visualizer = PyMOLVisualizer(pymol_path=pymol_path)
        super().__init__(name='pymol_visualizer', host=host, port=port)
    
    def setup_tools(self):
        """Register the visualize_docking tool."""
        
        def visualize_docking_handler(
            receptor_path: str,
            ligand_paths: list,
            output_dir: str,
            affinities: list = None,
            show_surface: bool = True,
            show_binding_site: bool = True,
            binding_site_radius: float = 5.0,
            render_image: bool = True,
            image_width: int = 1920,
            image_height: int = 1080,
            image_dpi: int = 300,
            ray_trace: bool = True
        ) -> dict:
            """Handler for visualize_docking tool."""
            
            # Generate visualization
            output_script = f"{output_dir}/visualization.pml"
            image_output = f"{output_dir}/docking_visualization.png" if render_image else None
            
            result = self.visualizer.generate_pymol_script(
                receptor_path=receptor_path,
                ligand_paths=ligand_paths,
                output_script=output_script,
                affinities=affinities,
                show_surface=show_surface,
                show_binding_site=show_binding_site,
                binding_site_radius=binding_site_radius,
                image_output=image_output,
                image_width=image_width,
                image_height=image_height,
                image_dpi=image_dpi,
                ray_trace=ray_trace
            )
            
            return result
        
        # Define tool
        visualize_tool = MCPTool(
            name="visualize_docking",
            description="Create PyMOL visualization of docking results (generates .pml script and optionally renders image)",
            parameters=[
                MCPToolParameter(
                    name="receptor_path",
                    type="string",
                    description="Path to receptor file (PDBQT or PDB)",
                    required=True
                ),
                MCPToolParameter(
                    name="ligand_paths",
                    type="array",
                    description="List of paths to docked ligand files (PDBQT)",
                    required=True
                ),
                MCPToolParameter(
                    name="output_dir",
                    type="string",
                    description="Directory for output files",
                    required=True
                ),
                MCPToolParameter(
                    name="affinities",
                    type="array",
                    description="Optional list of binding affinities for color coding (best=green, worst=red)",
                    required=False
                ),
                MCPToolParameter(
                    name="show_surface",
                    type="boolean",
                    description="Show protein surface (default: true)",
                    required=False
                ),
                MCPToolParameter(
                    name="show_binding_site",
                    type="boolean",
                    description="Highlight binding site residues (default: true)",
                    required=False
                ),
                MCPToolParameter(
                    name="binding_site_radius",
                    type="number",
                    description="Radius around ligand for binding site in Angstroms (default: 5.0)",
                    required=False
                ),
                MCPToolParameter(
                    name="render_image",
                    type="boolean",
                    description="Render PNG image if PyMOL is available (default: true)",
                    required=False
                ),
                MCPToolParameter(
                    name="image_width",
                    type="number",
                    description="Image width in pixels (default: 1920)",
                    required=False
                ),
                MCPToolParameter(
                    name="image_height",
                    type="number",
                    description="Image height in pixels (default: 1080)",
                    required=False
                ),
                MCPToolParameter(
                    name="image_dpi",
                    type="number",
                    description="Image DPI for publication quality (default: 300)",
                    required=False
                ),
                MCPToolParameter(
                    name="ray_trace",
                    type="boolean",
                    description="Use ray tracing for high-quality rendering (default: true)",
                    required=False
                )
            ],
            handler=visualize_docking_handler
        )
        
        self.register_tool(visualize_tool)


def main():
    """Main entry point for the PyMOL Visualizer server."""
    parser = argparse.ArgumentParser(description='PyMOL Visualizer MCP Server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8005, help='Port to bind to')
    parser.add_argument('--pymol-path', help='Path to PyMOL executable for rendering')
    
    args = parser.parse_args()
    
    server = PyMOLVisualizerServer(
        host=args.host,
        port=args.port,
        pymol_path=args.pymol_path
    )
    server.run()


if __name__ == '__main__':
    main()
