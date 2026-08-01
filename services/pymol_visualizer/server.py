"""
PyMOL Visualizer MCP Server

Provides PyMOL visualization capabilities as an MCP service.
"""
from common.mcp_base import MCPServer
from common.mcp_bootstrap import build_service_arg_parser, resolve_port
from common.mcp_tooling import build_tool_parameters, register_mcp_tool
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
        
        register_mcp_tool(
            self,
            name="visualize_docking",
            description="Create PyMOL visualization of docking results (generates .pml script and optionally renders image)",
            handler=visualize_docking_handler,
            parameters=build_tool_parameters([
                ("receptor_path", "string", "Path to receptor file (PDBQT or PDB)", True),
                ("ligand_paths", "array", "List of paths to docked ligand files (PDBQT)", True),
                ("output_dir", "string", "Directory for output files", True),
                ("affinities", "array", "Optional list of binding affinities for color coding (best=green, worst=red)"),
                ("show_surface", "boolean", "Show protein surface (default: true)"),
                ("show_binding_site", "boolean", "Highlight binding site residues (default: true)"),
                ("binding_site_radius", "number", "Radius around ligand for binding site in Angstroms (default: 5.0)"),
                ("render_image", "boolean", "Render PNG image if PyMOL is available (default: true)"),
                ("image_width", "number", "Image width in pixels (default: 1920)"),
                ("image_height", "number", "Image height in pixels (default: 1080)"),
                ("image_dpi", "number", "Image DPI for publication quality (default: 300)"),
                ("ray_trace", "boolean", "Use ray tracing for high-quality rendering (default: true)"),
            ]),
        )


def main():
    """Main entry point for the PyMOL Visualizer server."""
    parser = build_service_arg_parser(
        "pymol_visualizer", "PyMOL Visualizer MCP Server", include_cache_dir=False,
    )
    parser.add_argument('--pymol-path', help='Path to PyMOL executable for rendering')

    args = parser.parse_args()

    server = PyMOLVisualizerServer(
        host=args.host,
        port=resolve_port("pymol_visualizer", args.port),
        pymol_path=args.pymol_path
    )
    server.run()


if __name__ == '__main__':
    main()
