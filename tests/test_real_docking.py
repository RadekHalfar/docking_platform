"""A real dock with the real Moldina binary, then its real poses converted (Stage 19.3).

The hermetic tests mock the binary, so only this one proves it still runs with
the flags the runner builds, and that its output parses. It needs no network or
running service, only the binary, which the moldina-runner image has. As CI runs
it (the `integration` marker keeps it out of the hermetic job):

    docker run --rm -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace \
        mcp-moldina-runner:latest python -m pytest tests/test_real_docking.py -p no:cacheprovider

Self-skips only with no binary to run. MOLDINA_PATH is set in that image, so
a binary missing there fails instead of skipping.
"""
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from common.pdb_geometry import compute_protein_center_and_box
from services.moldina_runner import MoldinaRunner

pytestmark = pytest.mark.integration

MOLDINA = os.getenv("MOLDINA_PATH") or shutil.which("moldina")
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    if not MOLDINA:
        pytest.skip("no Moldina binary (set MOLDINA_PATH, or use the moldina-runner image)")
    from services.ligand_preparer import LigandPreparer
    from services.protein_preparer import ProteinPreparer

    work = tmp_path_factory.mktemp("prepared")
    ligands = LigandPreparer(cache_dir=str(work)).prepare_ligands([str(FIXTURES / "benzene.sdf")], str(work))
    proteins = ProteinPreparer(cache_dir=str(work)).prepare_proteins([str(FIXTURES / "mini_protein.pdb")], str(work))
    center, _ = compute_protein_center_and_box((FIXTURES / "mini_protein.pdb").read_text())
    return proteins["prepared_proteins"][0]["pdbqt_path"], ligands["prepared_ligands"][0]["pdbqt_path"], center


@pytest.mark.parametrize("algorithm", ["moldina", "vina"])
def test_real_dock_through_to_converted_poses(prepared, tmp_path, algorithm):
    from services.ligand_preparer import LigandPreparer

    receptor, ligand, center = prepared
    runner = MoldinaRunner(cache_dir=str(tmp_path), moldina_path=MOLDINA)
    # A low population and exhaustiveness: this checks the plumbing in seconds.
    with patch.object(runner, "_parse_vina_log") as log_fallback:
        docked = runner.run_moldina_docking(
            receptor_path=receptor,
            ligand_paths=[ligand],
            output_dir=str(tmp_path / "docking"),
            center=tuple(center),
            box_size=(20.0, 20.0, 20.0),
            cpu=2,
            population=20,
            exhaustiveness=1,
            num_modes=3,
            docking_algorithm=algorithm,
        )

    assert docked["status"] == "ok"
    assert docked["docking_algorithm"] == algorithm
    assert docked["successful"] == 1
    result = docked["results"][0]

    # Scores came from the real stdout table, not the log file fallback.
    log_fallback.assert_not_called()
    modes = result["modes"]
    assert 1 <= len(modes) <= 3
    assert result["num_modes_found"] == len(modes)
    assert [m["mode"] for m in modes] == list(range(1, len(modes) + 1))
    assert result["best_affinity"] == modes[0]["affinity"]
    assert modes[0]["rmsd_lb"] == 0.0

    # The parsed table and the pose file are the same result (PSO's table prints one decimal).
    pose_text = Path(result["output_path"]).read_text()
    pose_scores = [float(l.split()[3]) for l in pose_text.splitlines() if l.startswith("REMARK VINA RESULT")]
    assert len(pose_scores) == len(modes)
    assert pose_scores == pytest.approx([m["affinity"] for m in modes], abs=0.1)

    poses = LigandPreparer(cache_dir=str(tmp_path)).convert_docked_poses(
        pose_results=docked["results"], output_dir=str(tmp_path / "poses")
    )

    assert poses["status"] == "ok"
    assert poses["errors"] == []
    assert Path(poses["converted"][0]["sdf_path"]).stat().st_size > 0
