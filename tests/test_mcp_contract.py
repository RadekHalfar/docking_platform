"""Orchestrator -> MCP tool -> service method, with only the services' work faked (Stage 19.3).

MCP calls cross a JSON-RPC boundary, so a renamed parameter is a runtime break,
not a type error, and the orchestrator's other tests stub call_mcp_tool. Here the
real orchestrator posts through the real client to each real MCPServer's route,
and each service class is autospecced: anything its real method doesn't take
fails the call. Replies are canned, so this checks what is sent, not what a
service answers (tests/test_real_docking.py and test_docker_integration.py do).
"""
import inspect
import types
from unittest.mock import create_autospec

import pytest
import requests

pytest.importorskip("rdkit")

import nlp_orchestrator as cli
from common import mcp_client
from services.ligand_preparer import LigandPreparer
from services.ligand_preparer.server import LigandPreparerServer
from services.llm_orchestrator import WorkflowOrchestrator
from services.moldina_runner import MoldinaRunner
from services.moldina_runner.server import MoldinaRunnerServer
from services.protein_preparer import ProteinPreparer
from services.protein_preparer.server import ProteinPreparerServer
from services.rcsb_downloader import RCSBDownloader
from services.rcsb_downloader.server import RCSBDownloaderServer

# (server key, tool, attribute holding the service object, its method)
TOOLS = [
    ("rcsb_downloader", "search_ligands", "downloader", "search_ligands"),
    ("rcsb_downloader", "download_structures", "downloader", "download_structures"),
    ("ligand_preparer", "prepare_ligands", "preparer", "prepare_ligands"),
    ("ligand_preparer", "convert_docked_poses", "preparer", "convert_docked_poses"),
    ("protein_preparer", "prepare_proteins", "preparer", "prepare_proteins"),
    ("moldina_runner", "run_docking", "runner", "run_moldina_docking"),
]

SAMPLE_BY_TYPE = {"string": "x", "array": ["sdf"], "number": 1, "boolean": True, "object": {}}

WORKFLOW = {
    "services": ["get_input_files", "prepare_ligands", "prepare_protein", "dock", "visualize"],
    "parameters": {
        "protein_id": "6lu7",
        "ligand_ids": ["ATP"],
        "formats": {"protein": "cif", "ligand": "mol"},
        "ph": 6.5,
        "add_hydrogens": False,
        "generate_3d": False,
        "optimize": False,
        "remove_waters": False,
        "center": [10.0, 20.0, 30.0],
        "box_size": [25.0, 26.0, 27.0],
        "population": 100,
        "exhaustiveness": 16,
        "num_modes": 5,
        "energy_range": 3.0,
        "docking_algorithm": "vina",
    },
}


class _Reply:
    """What the client reads of a requests.Response, from a Flask one."""

    def __init__(self, response):
        self.status_code = response.status_code
        self._response = response

    def json(self):
        return self._response.get_json()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def stack(tmp_path, monkeypatch):
    servers = {
        "rcsb_downloader": RCSBDownloaderServer(port=0, cache_dir=str(tmp_path / "cache")),
        "ligand_preparer": LigandPreparerServer(port=0, cache_dir=str(tmp_path / "cache")),
        "protein_preparer": ProteinPreparerServer(port=0, cache_dir=str(tmp_path / "cache")),
        "moldina_runner": MoldinaRunnerServer(port=0, cache_dir=str(tmp_path / "cache")),
    }
    for server in servers.values():
        server.setup_tools()

    downloader = create_autospec(RCSBDownloader, instance=True)
    downloader.search_ligands.return_value = []
    downloader.download_structures.return_value = {"protein_path": "/app/6lu7.pdb", "ligand_paths": ["/app/ATP.sdf"]}
    ligand_preparer = create_autospec(LigandPreparer, instance=True)
    ligand_preparer.prepare_ligands.return_value = {"prepared_ligands": [{"pdbqt_path": "/app/ATP.pdbqt"}]}
    ligand_preparer.convert_docked_poses.return_value = {"converted": [], "errors": [], "status": "ok"}
    protein_preparer = create_autospec(ProteinPreparer, instance=True)
    protein_preparer.prepare_proteins.return_value = {
        "prepared_proteins": [{"pdbqt_path": "/app/6lu7.pdbqt", "pdb_path": "/app/6lu7_prepared.pdb"}],
        "status": "ok",
    }
    runner = create_autospec(MoldinaRunner, instance=True)
    runner.run_moldina_docking.return_value = {
        "results": [{"ligand_id": "ATP", "output_path": "/app/ATP_out.pdbqt", "best_affinity": -8.5, "modes": []}],
        "successful": 1,
    }
    servers["rcsb_downloader"].downloader = downloader
    servers["ligand_preparer"].preparer = ligand_preparer
    servers["protein_preparer"].preparer = protein_preparer
    servers["moldina_runner"].runner = runner

    orchestrator = WorkflowOrchestrator(output_base=str(tmp_path / "output"))
    routes = {f"{orchestrator.services[name]}/mcp": server for name, server in servers.items()}

    def post(url, json, timeout):
        return _Reply(routes[url].app.test_client().post("/mcp", json=json))

    monkeypatch.setattr(mcp_client._session, "post", post)
    return types.SimpleNamespace(
        servers=servers,
        orchestrator=orchestrator,
        downloader=downloader,
        ligand_preparer=ligand_preparer,
        protein_preparer=protein_preparer,
        runner=runner,
    )


def _post_tool_call(server, tool, arguments):
    return server.app.test_client().post("/mcp", json={
        "jsonrpc": "2.0", "id": "1", "method": "tool_call", "params": {"name": tool, "arguments": arguments},
    }).get_json()


def _service_method(stack, key, attribute, method):
    return getattr(getattr(stack.servers[key], attribute), method)


def test_full_workflow_reaches_every_service_method_with_the_workflow_parameters(stack):
    results = stack.orchestrator.execute_workflow(WORKFLOW, job_id="contract")

    assert results["errors"] == []
    assert results["status"] == "completed"

    download = stack.downloader.download_structures.call_args.kwargs
    assert download["protein_id"] == "6lu7"
    assert download["ligand_ids"] == ["ATP"]
    assert download["formats"] == {"protein": "cif", "ligand": "mol"}

    ligands = stack.ligand_preparer.prepare_ligands.call_args.kwargs
    assert ligands["ligand_paths"] == ["/app/ATP.sdf"]
    assert (ligands["ph"], ligands["add_hydrogens"], ligands["generate_3d"], ligands["optimize"]) == (
        6.5, False, False, False,
    )

    protein = stack.protein_preparer.prepare_proteins.call_args.kwargs
    assert protein["protein_paths"] == ["/app/6lu7.pdb"]
    assert (protein["ph"], protein["add_hydrogens"], protein["remove_waters"]) == (6.5, False, False)

    dock = stack.runner.run_moldina_docking.call_args.kwargs
    assert dock["receptor_path"] == "/app/6lu7.pdbqt"
    assert dock["ligand_paths"] == ["/app/ATP.pdbqt"]
    assert dock["center"] == (10.0, 20.0, 30.0)
    assert dock["box_size"] == (25.0, 26.0, 27.0)
    assert (dock["population"], dock["exhaustiveness"], dock["num_modes"], dock["energy_range"]) == (100, 16, 5, 3.0)
    assert dock["docking_algorithm"] == "vina"

    poses = stack.ligand_preparer.convert_docked_poses.call_args.kwargs
    assert poses["pose_results"][0]["output_path"] == "/app/ATP_out.pdbqt"


def test_a_local_protein_sends_no_protein_id_to_the_downloader(stack, tmp_path):
    protein_file = tmp_path / "local.pdb"
    protein_file.write_text("ATOM\n")
    workflow = {
        "services": ["get_input_files"],
        "parameters": {"protein_file": str(protein_file), "ligand_ids": ["ATP"]},
    }

    results = stack.orchestrator.execute_workflow(workflow, job_id="contract_local")

    assert results["errors"] == []
    download = stack.downloader.download_structures.call_args.kwargs
    assert download["protein_id"] is None
    assert download["ligand_ids"] == ["ATP"]


def test_cli_ligand_search_reaches_the_downloader(stack):
    assert cli._search_and_select_ligand_id(stack.orchestrator, "aspirin") is None

    stack.downloader.search_ligands.assert_called_once_with(name="aspirin", max_results=10)


def test_every_registered_tool_is_covered_here(stack):
    registered = {(key, tool) for key, server in stack.servers.items() for tool in server.tools}

    assert registered == {(key, tool) for key, tool, _, _ in TOOLS}


@pytest.mark.parametrize("key, tool, attribute, method", TOOLS, ids=[t[1] for t in TOOLS])
class TestEveryTool:
    def test_declared_parameters_are_the_handlers_parameters(self, stack, key, tool, attribute, method):
        registered = stack.servers[key].tools[tool]
        signature = inspect.signature(registered.handler).parameters
        required = {p.name for p in registered.parameters if p.required}
        without_default = {n for n, p in signature.items() if p.default is inspect.Parameter.empty}

        assert {p.name for p in registered.parameters} == set(signature)
        assert without_default <= required, "the tool would crash on a call its schema allows"

    def test_every_declared_parameter_reaches_the_service_method(self, stack, key, tool, attribute, method):
        registered = stack.servers[key].tools[tool]
        arguments = {p.name: SAMPLE_BY_TYPE[p.type] for p in registered.parameters}

        reply = _post_tool_call(stack.servers[key], tool, arguments)

        assert "error" not in reply, reply
        called = _service_method(stack, key, attribute, method).call_args.kwargs
        assert set(called) == set(arguments)

    def test_required_parameters_alone_are_enough(self, stack, key, tool, attribute, method):
        registered = stack.servers[key].tools[tool]
        arguments = {p.name: SAMPLE_BY_TYPE[p.type] for p in registered.parameters if p.required}

        reply = _post_tool_call(stack.servers[key], tool, arguments)

        assert "error" not in reply, reply
        _service_method(stack, key, attribute, method).assert_called_once()

    def test_a_missing_required_parameter_is_refused(self, stack, key, tool, attribute, method):
        registered = stack.servers[key].tools[tool]
        required = [p.name for p in registered.parameters if p.required]
        if not required:
            pytest.skip(f"{tool} has no required parameters")
        arguments = {p.name: SAMPLE_BY_TYPE[p.type] for p in registered.parameters if p.name != required[0]}

        reply = _post_tool_call(stack.servers[key], tool, arguments)

        assert required[0] in reply["error"]["message"]
        _service_method(stack, key, attribute, method).assert_not_called()
