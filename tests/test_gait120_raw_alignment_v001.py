import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "raw_alignment", Path(__file__).parents[1] / "scripts/paper/check_gait120_raw_alignment_v001.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def test_translation_of_public_inclusive_rule():
    assert M.sample_interval([468, 773], [478, 590], 2000, 100, 6120) == (200, 2460)
    assert M.sample_interval([468, 773], [468, 773], 2000, 100, 6120) == (0, 6120)


@pytest.mark.parametrize("total,target,fs,marker,n", [
    ([468, 773], [467, 590], 2000, 100, 6120),
    ([468, 773], [478, 774], 2000, 100, 6120),
    ([468, 773], [590, 478], 2000, 100, 6120),
    ([468, 773], [478.5, 590], 2000, 100, 6120),
    ([468, 773], [478, 590], 2000, 100, 6119),
    ([468, 773], [478, 590], 2001, 100, 6120),
])
def test_uncertain_or_inconsistent_mapping_fails(total, target, fs, marker, n):
    with pytest.raises(ValueError):
        M.sample_interval(total, target, fs, marker, n)
