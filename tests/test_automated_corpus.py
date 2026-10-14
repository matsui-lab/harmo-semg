import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("automated_corpus", Path(__file__).resolve().parents[1] / "scripts/paper/run_automated_corpus.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_frozen_comparison():
    row = dict(mapping_id="one", source_sha256="abc", normalized_sample_sha256="def",
               selected_member="file", outcome="converted")
    assert module.compare_frozen([row], [row])["pass_for_executed_mappings"]
    assert not module.compare_frozen([dict(row, normalized_sample_sha256="changed")], [row])["pass_for_executed_mappings"]
    assert not module.compare_frozen([row], [])["pass_for_executed_mappings"]
    assert not module.compare_frozen([], [row])["pass_for_executed_mappings"]
    assert not module.compare_frozen([row, row], [row])["pass_for_executed_mappings"]
