"""
PyMOL Visualizer Service

This service creates PyMOL visualizations of docking results.

Features:
- Generate PyMOL scripts (.pml) for automated visualization
- Load receptor and docked ligand poses
- Apply visual styling (cartoons, sticks, surfaces)
- Color by binding affinity or pose rank
- Create publication-quality images
- Support for batch visualization

Tool: visualize_docking
- Creates PyMOL visualization scripts for docking results
- Outputs .pml scripts that can be run with PyMOL
- Optionally generates PNG images if PyMOL is available
"""
from __future__ import annotations
import os
import logging
import subprocess
import platform
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any

from common.utils import ensure_directory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PyMOLVisualizer:
    """
    Creates PyMOL visualizations of molecular docking results.
    
    Features:
    - Generates PyMOL scripts for automated visualization
    - Loads receptor structure (PDBQT or PDB)
    - Loads docked ligand poses (PDBQT)
    - Applies styling (cartoons, sticks, surfaces, transparency)
    - Colors by affinity or pose rank
    - Exports images (PNG) if PyMOL is available
    """
    
    def __init__(self, pymol_path: Optional[str] = None):
        """
        Initialize PyMOL visualizer.
        
        Args:
            pymol_path: Optional path to PyMOL executable for rendering
        """
        self.pymol_path = pymol_path
        self.pymol_available = self._check_pymol_available()
    
    def _check_pymol_available(self) -> bool:
        """
        Check if PyMOL is available for rendering.
        
        Returns:
            True if PyMOL executable is found
        """
        # If custom path provided and exists, use it
        if self.pymol_path and os.path.exists(self.pymol_path):
            return True
        
        # If custom path provided but doesn't exist, don't search for alternatives
        # This preserves the user's custom path even if it's invalid
        if self.pymol_path:
            return False
        
        # Check common Linux/Docker conda installation path
        conda_pymol = "/opt/conda/bin/pymol"
        if os.path.exists(conda_pymol):
            self.pymol_path = conda_pymol
            logger.info(f"Found PyMOL at: {conda_pymol}")
            return True
        
        # Try to find pymol in PATH
        try:
            result = subprocess.run(
                ["pymol", "-c", "-Q"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                self.pymol_path = "pymol"
                logger.info("Found PyMOL in PATH")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        # Try common Windows installation paths
        common_paths = [
            r"C:\Program Files\PyMOL\PyMOL\PyMOLWin.exe",
            r"C:\Program Files (x86)\PyMOL\PyMOL\PyMOLWin.exe",
        ]
        
        for path in common_paths:
            if os.path.exists(path):
                self.pymol_path = path
                logger.info(f"Found PyMOL at: {path}")
                return True
        
        logger.info("PyMOL not found - will generate scripts only")
        return False
    
    def generate_pymol_script(
        self,
        receptor_path: str,
        ligand_paths: List[str],
        output_script: str,
        affinities: Optional[List[float]] = None,
        show_surface: bool = True,
        show_binding_site: bool = True,
        binding_site_radius: float = 5.0,
        image_output: Optional[str] = None,
        image_width: int = 1920,
        image_height: int = 1080,
        image_dpi: int = 300,
        ray_trace: bool = True
    ) -> Dict[str, Any]:
        """
        Generate PyMOL script for visualizing docking results.
        
        Args:
            receptor_path: Path to receptor file (PDBQT or PDB)
            ligand_paths: List of paths to docked ligand files (PDBQT)
            output_script: Path for output .pml script
            affinities: Optional list of binding affinities for color coding
            show_surface: Show protein surface
            show_binding_site: Highlight binding site residues
            binding_site_radius: Radius around ligand for binding site (Angstroms)
            image_output: Optional path to save rendered image (PNG)
            image_width: Image width in pixels
            image_height: Image height in pixels
            image_dpi: Image DPI for publication quality
            ray_trace: Use ray tracing for high-quality rendering
            
        Returns:
            Dictionary with script generation results
        """
        ensure_directory(os.path.dirname(output_script))
        
        script_lines = []
        
        # Header
        script_lines.append("# PyMOL Visualization Script")
        script_lines.append("# Generated by MCP Docking System")
        script_lines.append("")
        
        # Initialize
        script_lines.append("# Initialize")
        script_lines.append("reinitialize")
        script_lines.append("bg_color white")
        script_lines.append("set ray_opaque_background, on")
        script_lines.append("set orthoscopic, on")
        script_lines.append("")
        
        # Load receptor
        script_lines.append("# Load receptor")
        # Use relative path from the script location for cross-platform compatibility
        script_dir = os.path.dirname(output_script)
        receptor_rel = os.path.relpath(receptor_path, script_dir)
        # Convert to forward slashes for PyMOL compatibility (works on Windows too)
        receptor_path_pymol = receptor_rel.replace('\\', '/')
        script_lines.append(f'load {receptor_path_pymol}, receptor')
        script_lines.append("")
        
        # Style receptor
        script_lines.append("# Style receptor")
        script_lines.append("hide everything, receptor")
        script_lines.append("show cartoon, receptor")
        script_lines.append("color gray70, receptor")
        script_lines.append("set cartoon_transparency, 0.3, receptor")
        
        if show_surface:
            script_lines.append("show surface, receptor")
            script_lines.append("set surface_color, gray80, receptor")
            script_lines.append("set transparency, 0.7, receptor")
        
        script_lines.append("")
        
        # Load ligands
        script_lines.append("# Load docked ligands")
        for i, ligand_path in enumerate(ligand_paths):
            ligand_name = f"ligand_{i+1}"
            # Use relative path from the script location for cross-platform compatibility
            ligand_rel = os.path.relpath(ligand_path, script_dir)
            # Convert to forward slashes for PyMOL compatibility (works on Windows too)
            ligand_path_pymol = ligand_rel.replace('\\', '/')
            script_lines.append(f'load {ligand_path_pymol}, {ligand_name}')
        
        script_lines.append("")
        
        # Style ligands with color gradient based on affinity
        script_lines.append("# Style ligands")
        script_lines.append("hide everything, ligand_*")
        script_lines.append("show sticks, ligand_*")
        script_lines.append("set stick_radius, 0.2, ligand_*")
        script_lines.append("")
        
        # Color ligands by affinity (if provided)
        if affinities:
            script_lines.append("# Color by binding affinity")
            script_lines.append("# Best affinity = green, worst = red")
            
            # Normalize affinities for color gradient
            min_aff = min(affinities)
            max_aff = max(affinities)
            
            for i, affinity in enumerate(affinities):
                ligand_name = f"ligand_{i+1}"
                
                # Calculate color: green (best) to red (worst)
                if max_aff != min_aff:
                    ratio = (affinity - min_aff) / (max_aff - min_aff)
                else:
                    ratio = 0.5
                
                # RGB gradient: green (0,1,0) to yellow (1,1,0) to red (1,0,0)
                if ratio < 0.5:
                    r = ratio * 2
                    g = 1.0
                else:
                    r = 1.0
                    g = 2.0 * (1.0 - ratio)
                b = 0.0
                
                script_lines.append(f"set_color affinity_color_{i+1}, [{r:.3f}, {g:.3f}, {b:.3f}]")
                script_lines.append(f"color affinity_color_{i+1}, {ligand_name}")
                script_lines.append(f"# {ligand_name}: {affinity:.2f} kcal/mol")
        else:
            # Default color scheme
            colors = ["green", "cyan", "yellow", "orange", "magenta", "pink"]
            for i, ligand_path in enumerate(ligand_paths):
                ligand_name = f"ligand_{i+1}"
                color = colors[i % len(colors)]
                script_lines.append(f"color {color}, {ligand_name}")
        
        script_lines.append("")
        
        # Highlight binding site
        if show_binding_site and ligand_paths:
            script_lines.append("# Highlight binding site")
            script_lines.append(f"select binding_site, receptor within {binding_site_radius} of ligand_1")
            script_lines.append("show sticks, binding_site")
            script_lines.append("color gray40, binding_site")
            script_lines.append("set stick_radius, 0.15, binding_site")
            script_lines.append("")
        
        # Center view
        script_lines.append("# Center view")
        if ligand_paths:
            script_lines.append("center ligand_1")
            script_lines.append("zoom ligand_1, 8")
        else:
            script_lines.append("center receptor")
            script_lines.append("zoom receptor")
        script_lines.append("")
        
        # Orient for best view
        script_lines.append("# Orient for best view")
        script_lines.append("orient")
        script_lines.append("")
        
        # Render image if requested
        if image_output:
            script_lines.append("# Render image")
            script_lines.append(f"set ray_trace_mode, 1")
            script_lines.append(f"set ray_shadows, 1")
            script_lines.append(f"viewport {image_width}, {image_height}")
            
            if ray_trace:
                script_lines.append(f"ray {image_width}, {image_height}")
            
            # Use relative path from the script location for cross-platform compatibility
            image_rel = os.path.relpath(image_output, script_dir)
            # Convert to forward slashes for PyMOL compatibility (works on Windows too)
            image_output_pymol = image_rel.replace('\\', '/')
            script_lines.append(f"png {image_output_pymol}, dpi={image_dpi}")
            script_lines.append("")
        
        # Save session
        session_path = output_script.replace('.pml', '.pse')
        script_lines.append("# Save PyMOL session")
        # Use relative path from the script location for cross-platform compatibility
        session_rel = os.path.relpath(session_path, script_dir)
        # Convert to forward slashes for PyMOL compatibility (works on Windows too)
        session_path_pymol = session_rel.replace('\\', '/')
        script_lines.append(f"save {session_path_pymol}")
        script_lines.append("")
        
        # Write script
        with open(output_script, 'w') as f:
            f.write('\n'.join(script_lines))
        
        logger.info(f"Generated PyMOL script: {output_script}")
        
        result = {
            "script_path": output_script,
            "session_path": session_path,
            "receptor": receptor_path,
            "num_ligands": len(ligand_paths),
            "ligands": ligand_paths,
            "affinities": affinities,
            "settings": {
                "show_surface": show_surface,
                "show_binding_site": show_binding_site,
                "binding_site_radius": binding_site_radius
            }
        }
        
        # Optionally render image
        if image_output and self.pymol_available:
            try:
                image_path = self._render_image(output_script, image_output)
                result["image_path"] = image_path
                result["rendered"] = True
            except Exception as e:
                logger.warning(f"Image rendering failed: {e}")
                result["rendered"] = False
                result["render_error"] = str(e)
        else:
            result["rendered"] = False
            if image_output and not self.pymol_available:
                result["render_error"] = "PyMOL not available for rendering"
        
        return result
    
    def _render_image(self, script_path: str, image_path: str) -> str:
        """
        Render image using PyMOL in headless mode.
        
        Args:
            script_path: Path to PyMOL script
            image_path: Path for output image
            
        Returns:
            Path to rendered image
        """
        if not self.pymol_available:
            raise RuntimeError("PyMOL not available for rendering")
        
        ensure_directory(os.path.dirname(image_path))
        
        # Check if we're on Linux (Docker) - use xvfb-run for headless rendering
        use_xvfb = platform.system() == "Linux" and shutil.which("xvfb-run")
        
        # Build PyMOL command
        pymol_cmd = [
            self.pymol_path if self.pymol_path else "pymol",
            "-c",  # Command-line mode (no GUI)
            "-Q",  # Quiet mode
            script_path
        ]
        
        if use_xvfb:
            # Wrap with xvfb-run for headless rendering on Linux
            cmd = ["xvfb-run", "-a"] + pymol_cmd
            logger.info("Using xvfb-run for headless rendering")
        else:
            cmd = pymol_cmd
        
        logger.info(f"Rendering image with PyMOL: {image_path}")
        logger.info(f"Command: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=os.path.dirname(script_path)  # Run from script directory for relative paths
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("PyMOL rendering timed out after 120 seconds")
        
        if result.returncode != 0:
            logger.error(f"PyMOL stderr: {result.stderr}")
            logger.error(f"PyMOL stdout: {result.stdout}")
            raise RuntimeError(f"PyMOL rendering failed with return code {result.returncode}: {result.stderr}")
        
        if not os.path.exists(image_path):
            # Try to find the image with relative path
            script_dir = os.path.dirname(script_path)
            image_name = os.path.basename(image_path)
            alt_image_path = os.path.join(script_dir, image_name)
            if os.path.exists(alt_image_path):
                logger.info(f"Image created at alternate location: {alt_image_path}")
                return alt_image_path
            raise RuntimeError(f"Image not created: {image_path}")
        
        logger.info(f"Image rendered successfully: {image_path}")
        return image_path
    
    def visualize_docking_results(
        self,
        receptor_path: str,
        docking_results: List[Dict[str, Any]],
        output_dir: str,
        top_n: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create visualization for docking results.
        
        Args:
            receptor_path: Path to receptor file
            docking_results: List of docking result dictionaries with 'output_file' and optional 'affinity'
            output_dir: Directory for output files
            top_n: Visualize only top N poses (by affinity)
            **kwargs: Additional arguments passed to generate_pymol_script
            
        Returns:
            Dictionary with visualization results
        """
        ensure_directory(output_dir)
        
        # Extract ligand paths and affinities
        ligand_paths = []
        affinities = []
        
        for result in docking_results:
            if 'output_file' in result and os.path.exists(result['output_file']):
                ligand_paths.append(result['output_file'])
                if 'best_affinity' in result:
                    affinities.append(result['best_affinity'])
                elif 'affinity' in result:
                    affinities.append(result['affinity'])
        
        if not ligand_paths:
            raise ValueError("No valid docking result files found")
        
        # Sort by affinity if available
        if affinities and len(affinities) == len(ligand_paths):
            sorted_pairs = sorted(zip(affinities, ligand_paths))
            affinities, ligand_paths = zip(*sorted_pairs)
            affinities = list(affinities)
            ligand_paths = list(ligand_paths)
        else:
            affinities = None
        
        # Limit to top N
        if top_n and top_n < len(ligand_paths):
            ligand_paths = ligand_paths[:top_n]
            if affinities:
                affinities = affinities[:top_n]
        
        # Generate script
        output_script = os.path.join(output_dir, "visualization.pml")
        image_output = os.path.join(output_dir, "docking_visualization.png")
        
        result = self.generate_pymol_script(
            receptor_path=receptor_path,
            ligand_paths=ligand_paths,
            output_script=output_script,
            affinities=affinities,
            image_output=image_output,
            **kwargs
        )
        
        result["top_n"] = top_n
        result["total_results"] = len(docking_results)
        result["visualized_results"] = len(ligand_paths)
        
        return result
