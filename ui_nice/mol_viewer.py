"""The 3Dmol.js viewer element.

One viewer per element, built once from the constructor's scene, so the
camera is kept. Afterwards only the box
overlay (:meth:`MolViewer.set_box`) and the ligand poses
(:meth:`MolViewer.set_ligands`) are redrawn, so the camera stays where the
user put it. A new receptor means a new element.

The box and the poses travel by ``run_method``, never by ``update()``.
NiceGUI's update resends *all* props, so the receptor PDB (often several MB)
would be resent with every box step. They are stored in ``_props`` too,
without an update, so anything that does resend the element carries the
latest values.

The attributes ``receptor_pdb``, ``ligands``, ``mode``, ``center`` and
``box_size`` hold what was last sent to the browser. The tests assert on
them.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from nicegui import app, ui

VENDOR_DIR = Path(__file__).resolve().parents[1] / "ui" / "components" / "mol_viewer" / "vendor"
VENDOR_URL = "/mol-viewer-vendor"
MODES = ("view", "box_picker")


def serve_vendor_files() -> None:
    """Serve the vendored 3Dmol.js. It is a UMD bundle, not an ES module,
    so the element loads it with a classic script tag."""
    app.add_static_files(VENDOR_URL, str(VENDOR_DIR))


class MolViewer(ui.element, component="mol_viewer.js"):
    """Receptor (cartoon) plus ligand poses coloured by affinity; in
    ``"box_picker"`` mode also the docking box, and an ``atom_click`` event
    with ``{"center": [x, y, z]}`` when the user clicks a receptor atom.

    *ligands*: ``{ligand_id: {"sdf": <one pose>, "affinity": float | None}}``.
    """

    def __init__(
        self,
        receptor_pdb: str,
        ligands: Optional[dict[str, dict[str, Any]]] = None,
        *,
        mode: str = "view",
        center: Optional[list[float]] = None,
        box_size: Optional[list[float]] = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown viewer mode {mode!r}")
        super().__init__()
        self.receptor_pdb = receptor_pdb
        self.ligands = dict(ligands or {})
        self.mode = mode
        self.center = list(center) if center else None
        self.box_size = list(box_size) if box_size else None
        self._props["library-url"] = f"{VENDOR_URL}/3Dmol-min.js"
        self._props["mode"] = mode
        self._props["receptor-pdb"] = receptor_pdb
        self._props["ligands"] = self.ligands
        self._props["center"] = self.center
        self._props["box-size"] = self.box_size

    def set_box(self, center: Optional[list[float]], box_size: Optional[list[float]]) -> None:
        """Redraw the box overlay (camera kept). Sends nothing when the box
        is unchanged, so a redraw of the page costs nothing here."""
        center = list(center) if center else None
        box_size = list(box_size) if box_size else None
        if (center, box_size) == (self.center, self.box_size):
            return
        self.center, self.box_size = center, box_size
        self._props["center"] = center
        self._props["box-size"] = box_size
        self.run_method("setBox", center, box_size)

    def set_ligands(self, ligands: Optional[dict[str, dict[str, Any]]]) -> None:
        """Swap the ligand poses (camera kept). Sends nothing when unchanged."""
        ligands = dict(ligands or {})
        if ligands == self.ligands:
            return
        self.ligands = ligands
        self._props["ligands"] = ligands
        self.run_method("setLigands", ligands)
