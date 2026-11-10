"""
Unit tests for RCSB Downloader service.
"""
import pytest
import os
import json
import requests
from unittest.mock import patch, Mock
from services.rcsb_downloader import RCSBDownloader


@pytest.fixture
def downloader(tmp_path):
    """Create RCSBDownloader instance with temp cache."""
    cache_dir = str(tmp_path / "cache")
    return RCSBDownloader(cache_dir=cache_dir)


@pytest.fixture
def sample_pdb_content():
    """Sample PDB file content."""
    return b"HEADER    TEST PROTEIN\nATOM      1  CA  ALA A   1"


@pytest.fixture
def sample_sdf_content():
    """Sample SDF file content."""
    return b"""
  Mrv0541 02231512123D          

  6  5  0  0  0  0            999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
M  END
$$$$
"""


def test_download_protein_success(downloader, tmp_path, sample_pdb_content):
    """Test successful protein download."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = sample_pdb_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        save_dir = str(tmp_path / "downloads")
        result = downloader.download_protein("1ABC", save_dir, "pdb")
        
        assert result["file_path"].endswith("1ABC.pdb")
        assert os.path.exists(result["file_path"])
        assert "checksum" in result
        assert "source_url" in result
        assert "1ABC.pdb" in result["source_url"]


def test_download_protein_invalid_id(downloader, tmp_path):
    """Test protein download with invalid PDB ID."""
    with pytest.raises(ValueError, match="Invalid PDB ID"):
        downloader.download_protein("INVALID", str(tmp_path))


def test_download_protein_unsupported_format(downloader, tmp_path):
    """Test protein download with unsupported format."""
    with pytest.raises(ValueError, match="Unsupported format"):
        downloader.download_protein("1ABC", str(tmp_path), "xyz")


def test_download_protein_network_error(downloader, tmp_path):
    """Test protein download with network error."""
    import requests
    with patch('services.rcsb_downloader.requests.get') as mock_get:
        mock_get.side_effect = requests.RequestException("Network error")
        
        with pytest.raises(RuntimeError, match="Failed to download"):
            downloader.download_protein("1ABC", str(tmp_path))


def test_download_ligand_success(downloader, tmp_path, sample_sdf_content):
    """Test successful ligand download."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = sample_sdf_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        save_dir = str(tmp_path / "ligands")
        result = downloader.download_ligand("ATP", save_dir, "sdf")
        
        assert result["file_path"].endswith("ATP.sdf")
        assert os.path.exists(result["file_path"])
        assert "checksum" in result
        assert "ATP_ideal.sdf" in result["source_url"]


def test_download_ligand_unsupported_format(downloader, tmp_path):
    """Test ligand download with unsupported format."""
    with pytest.raises(ValueError, match="Unsupported ligand format"):
        downloader.download_ligand("ATP", str(tmp_path), "xyz")


@pytest.mark.parametrize(
    "bad_ligand_id",
    ["", "TOOLONG", "has space", "../../etc/passwd", "a/b"],
)
def test_download_ligand_rejects_invalid_id_before_building_url(downloader, tmp_path, bad_ligand_id):
    """ligand_id is spliced directly into the outbound RCSB URL -- unlike
    protein_id (validated via validate_pdb_id), it wasn't validated before
    this, so any value reached files.rcsb.org unchecked. Must now be
    rejected before any network call, same as an unsupported format."""
    with patch('requests.get') as mock_get:
        with pytest.raises(ValueError, match="Invalid ligand ID"):
            downloader.download_ligand(bad_ligand_id, str(tmp_path), "sdf")
        mock_get.assert_not_called()


def test_download_structures_skips_invalid_ligand_id_without_crashing_batch(
    downloader, tmp_path, sample_pdb_content, sample_sdf_content
):
    """An invalid ligand_id reaching download_ligand (e.g. name resolution
    disabled and the token isn't chem_comp-shaped) must be skipped like any
    other per-ligand failure, not raise out of the whole batch call."""
    def mock_response_factory(url, timeout=None):
        mock_resp = Mock()
        if url.endswith(".pdb"):
            mock_resp.content = sample_pdb_content
        else:
            mock_resp.content = sample_sdf_content
        mock_resp.raise_for_status = Mock()
        return mock_resp

    with patch('requests.get', side_effect=mock_response_factory):
        result = downloader.download_structures(
            protein_id="1ABC",
            ligand_ids=["ATP", "not-a-valid-chem-comp-id"],
            save_dir=str(tmp_path / "mixed"),
        )

    assert result["status"] == "ok"
    assert len(result["ligand_paths"]) == 1
    assert result["ligand_paths"][0].endswith("ATP.sdf")


def test_download_structures_complete(downloader, tmp_path, sample_pdb_content, sample_sdf_content):
    """Test complete download of protein and ligands."""
    with patch('requests.get') as mock_get:
        # Mock responses for protein and ligands
        def mock_response_factory(url, timeout=None):
            mock_resp = Mock()
            if ".pdb" in url:
                mock_resp.content = sample_pdb_content
            else:
                mock_resp.content = sample_sdf_content
            mock_resp.raise_for_status = Mock()
            return mock_resp
        
        mock_get.side_effect = mock_response_factory
        
        save_dir = str(tmp_path / "complete")
        result = downloader.download_structures(
            protein_id="1ABC",
            ligand_ids=["ATP", "HEM"],
            formats={"protein": "pdb", "ligand": "sdf"},
            save_dir=save_dir
        )
        
        assert result["status"] == "ok"
        assert result["protein_path"].endswith("1ABC.pdb")
        assert len(result["ligand_paths"]) == 2
        assert os.path.exists(result["protein_path"])
        
        # Check metadata was saved
        metadata_file = os.path.join(save_dir, "1ABC_download_meta.json")
        assert os.path.exists(metadata_file)
        
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
            assert metadata["status"] == "ok"


def test_download_structures_protein_only(downloader, tmp_path, sample_pdb_content):
    """Test download with protein only (no ligands)."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = sample_pdb_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        save_dir = str(tmp_path / "protein_only")
        result = downloader.download_structures(
            protein_id="2XYZ",
            ligand_ids=[],
            save_dir=save_dir
        )
        
        assert result["status"] == "ok"
        assert result["protein_path"].endswith("2XYZ.pdb")
        assert len(result["ligand_paths"]) == 0


def test_download_structures_caching(downloader, tmp_path, sample_pdb_content):
    """Test that caching works for repeated downloads."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.content = sample_pdb_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        save_dir1 = str(tmp_path / "download1")
        save_dir2 = str(tmp_path / "download2")
        
        # First download
        result1 = downloader.download_protein("1ABC", save_dir1, "pdb")
        assert result1["cached"] is False
        assert mock_get.call_count == 1
        
        # Second download should use cache
        result2 = downloader.download_protein("1ABC", save_dir2, "pdb")
        assert result2["cached"] is True
        assert mock_get.call_count == 1  # Should not make another request


def test_download_structures_partial_ligand_failure(downloader, tmp_path, sample_pdb_content, sample_sdf_content):
    """Test that protein download succeeds even if some ligands fail."""
    call_count = [0]
    
    def mock_response_factory(url, timeout=None):
        mock_resp = Mock()
        call_count[0] += 1
        
        if ".pdb" in url:
            mock_resp.content = sample_pdb_content
            mock_resp.raise_for_status = Mock()
        elif "ATP" in url:
            mock_resp.content = sample_sdf_content
            mock_resp.raise_for_status = Mock()
        else:
            # Simulate failure for second ligand
            mock_resp.raise_for_status = Mock(side_effect=requests.HTTPError("Not found"))
        
        return mock_resp
    
    with patch('requests.get') as mock_get:
        mock_get.side_effect = mock_response_factory
        
        save_dir = str(tmp_path / "partial")
        result = downloader.download_structures(
            protein_id="1ABC",
            ligand_ids=["ATP", "BADLIG"],
            save_dir=save_dir
        )
        
        # Protein should succeed
        assert result["status"] == "ok"
        assert os.path.exists(result["protein_path"])
        
        # Only one ligand should be downloaded
        assert len(result["ligand_paths"]) == 1


def test_search_ligands_success(downloader):
    """Test ligand name search returns ranked candidates."""
    payload = {
        "result_set": [
            {"identifier": "ASP", "score": 1.0},
            {"identifier": "AIN", "score": 0.8},
        ]
    }
    with patch("services.rcsb_downloader.requests.post") as mock_post:
        mock_resp = Mock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status = Mock()
        mock_post.return_value = mock_resp

        result = downloader.search_ligands("aspirin", max_results=5)

        assert len(result) == 2
        assert result[0]["component_id"] == "ASP"
        assert result[1]["component_id"] == "AIN"


def test_search_ligands_request_failure_raises(downloader):
    """Test search errors are surfaced as RuntimeError."""
    with patch("services.rcsb_downloader.requests.post") as mock_post:
        mock_post.side_effect = requests.RequestException("timeout")
        with pytest.raises(RuntimeError, match="Failed to search ligands"):
            downloader.search_ligands("aspirin")


def test_search_ligands_http_error_includes_response_body(downloader):
    """HTTPError messages should include the response body for debugging (M2 split handler)."""
    with patch("services.rcsb_downloader.requests.post") as mock_post:
        http_error = requests.HTTPError("400 Bad Request")
        mock_body = Mock()
        mock_body.text = "Invalid query syntax from server"
        http_error.response = mock_body

        resp = Mock()
        resp.raise_for_status.side_effect = http_error
        mock_post.return_value = resp

        with pytest.raises(RuntimeError) as exc_info:
            downloader.search_ligands("aspirin")

        assert "Invalid query syntax from server" in str(exc_info.value)
        assert "response=" in str(exc_info.value)


def test_search_ligands_connection_error_excludes_response_prefix(downloader):
    """ConnectionError should not add 'response=' to the message (M2 split handler)."""
    with patch("services.rcsb_downloader.requests.post") as mock_post:
        mock_post.side_effect = requests.ConnectionError("Connection refused: aspirin")

        with pytest.raises(RuntimeError) as exc_info:
            downloader.search_ligands("aspirin")

        error_msg = str(exc_info.value)
        assert "refused" in error_msg
        assert "response=" not in error_msg


def test_download_structures_resolves_name_when_enabled(downloader, tmp_path, sample_pdb_content, sample_sdf_content):
    """Common ligand names should resolve to chem_comp IDs before download."""

    def mock_get(url, timeout=None):
        mock_resp = Mock()
        if url.endswith(".pdb"):
            mock_resp.content = sample_pdb_content
        else:
            mock_resp.content = sample_sdf_content
        mock_resp.raise_for_status = Mock()
        return mock_resp

    with patch("services.rcsb_downloader.requests.get", side_effect=mock_get):
        with patch.object(downloader, "resolve_ligand_name", return_value="AIN") as mock_resolve:
            result = downloader.download_structures(
                protein_id="1ABC",
                ligand_ids=["aspirin"],
                enable_name_resolution=True,
                save_dir=str(tmp_path / "resolved"),
            )

            mock_resolve.assert_called_once_with("aspirin")
            assert len(result["ligand_paths"]) == 1
            assert result["ligand_paths"][0].endswith("AIN.sdf")


# ---------------------------------------------------------------------------
# search_ligands: RCSB answers "no hits" with 204 and an empty body
# ---------------------------------------------------------------------------

def _search_response(status_code, payload=None):
    response = Mock()
    response.status_code = status_code
    response.content = json.dumps(payload).encode() if payload is not None else b""
    response.json = Mock(return_value=payload) if payload is not None else Mock(
        side_effect=ValueError("Expecting value: line 1 column 1 (char 0)")
    )
    response.raise_for_status = Mock()
    return response


def test_search_ligands_with_no_match_returns_no_candidates(downloader):
    with patch("services.rcsb_downloader.requests.post", return_value=_search_response(204)) as post:
        assert downloader.search_ligands("aspirin") == []
        # Cached like a hit: a repeat doesn't ask RCSB again.
        assert downloader.search_ligands("aspirin") == []

    post.assert_called_once()


def test_search_ligands_parses_a_match(downloader):
    payload = {"result_set": [{"identifier": "adn", "score": 0.96}]}
    with patch("services.rcsb_downloader.requests.post", return_value=_search_response(200, payload)):
        assert downloader.search_ligands("adenosine") == [
            {"component_id": "ADN", "name": "ADN", "score": 0.96}
        ]


def test_search_ligands_still_fails_on_an_http_error(downloader):
    response = _search_response(400)
    response.raise_for_status.side_effect = requests.HTTPError("400", response=Mock(text="bad query"))
    with patch("services.rcsb_downloader.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="Failed to search ligands"):
            downloader.search_ligands("adenosine")


def test_resolve_ligand_name_with_no_match_is_none(downloader):
    with patch("services.rcsb_downloader.requests.post", return_value=_search_response(204)):
        assert downloader.resolve_ligand_name("aspirin") is None


def test_search_ligands_tool_answers_an_empty_candidate_list(tmp_path):
    from services.rcsb_downloader.server import RCSBDownloaderServer

    server = RCSBDownloaderServer(port=0, cache_dir=str(tmp_path / "cache"))
    server.setup_tools()
    with patch("services.rcsb_downloader.requests.post", return_value=_search_response(204)):
        result = server.tools["search_ligands"].handler(name="aspirin")

    assert result == {"query": "aspirin", "candidates": []}


def _http_error_response(status):
    response = requests.Response()
    response.status_code = status
    response._content = b"Not Found"
    response.url = "https://files.rcsb.org/ligands/download/ATP_ideal.sdf"
    return response


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param({"return_value": _http_error_response(404)}, id="404"),
        pytest.param({"return_value": _http_error_response(503)}, id="503"),
        pytest.param({"side_effect": requests.ConnectionError("no route")}, id="connection"),
        pytest.param({"side_effect": requests.Timeout("read timed out")}, id="timeout"),
    ],
)
def test_download_ligand_failure_raises_and_writes_nothing(downloader, tmp_path, outcome):
    save_dir = tmp_path / "ligands"
    with patch("services.rcsb_downloader.requests.get", **outcome) as mock_get:
        with pytest.raises(RuntimeError, match="Failed to download ligand ATP"):
            downloader.download_ligand("ATP", str(save_dir), "sdf")

    assert mock_get.call_args.args[0].endswith("/ATP_ideal.sdf")
    assert mock_get.call_args.kwargs["timeout"] == 30
    assert list(save_dir.iterdir()) == []
    cache = tmp_path / "cache"
    assert not cache.exists() or not any(p.name.startswith("ATP") for p in cache.rglob("*"))


def test_a_failed_ligand_download_is_retried_not_served_from_cache(downloader, tmp_path, sample_sdf_content):
    save_dir = str(tmp_path / "ligands")
    ok = Mock(content=sample_sdf_content, raise_for_status=Mock())
    with patch(
        "services.rcsb_downloader.requests.get",
        side_effect=[requests.ConnectionError("flaky"), ok],
    ) as mock_get:
        with pytest.raises(RuntimeError):
            downloader.download_ligand("ATP", save_dir, "sdf")
        result = downloader.download_ligand("ATP", save_dir, "sdf")

    assert mock_get.call_count == 2
    assert result["cached"] is False
    assert open(result["file_path"], "rb").read() == sample_sdf_content


def test_download_structures_with_every_ligand_failing_returns_no_ligands(downloader, tmp_path):
    save_dir = tmp_path / "all_fail"
    with patch(
        "services.rcsb_downloader.requests.get", return_value=_http_error_response(404)
    ):
        result = downloader.download_structures(
            protein_id=None, ligand_ids=["ATP", "HEM"], save_dir=str(save_dir)
        )

    assert result["status"] == "ok"
    assert result["ligand_paths"] == []
    assert result["protein_path"] is None
    assert sorted(p.name for p in save_dir.iterdir()) == ["ligands_download_meta.json"]
