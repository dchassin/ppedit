"""Check the PYPOWER cases in `examples/pypower/` (fast, no network)"""

import glob
import os

import pytest

from ppedit import Case

EXAMPLES = os.path.join(os.path.dirname(__file__),"..","..","examples","pypower")
CASES = sorted(os.path.splitext(os.path.basename(x))[0]
    for x in glob.glob(os.path.join(EXAMPLES,"case*.py")))

def test_examples_present():
    """Every case has both forms, and the license is kept with them"""
    if not os.path.isdir(EXAMPLES):
        pytest.skip("examples folder not available (installed package only)")
    assert len(CASES) >= 14
    for name in CASES:
        assert os.path.exists(os.path.join(EXAMPLES,f"{name}.glm")),name
    assert os.path.exists(os.path.join(EXAMPLES,"LICENSE"))

@pytest.mark.parametrize("name",CASES)
def test_glm_matches_py(name):
    """The GLM form of each example holds the same case data as its .py file"""
    py = Case.load(os.path.join(EXAMPLES,f"{name}.py"))
    glm = Case.load(os.path.join(EXAMPLES,f"{name}.glm"))
    # module pypower has no areas objects: rebuilt from the bus areas on reading
    assert [r[0] for r in glm.tables["areas"]] == [r[0] for r in py.tables["areas"]]
    assert {k:v for k,v in glm.tables.items() if k != "areas"} \
        == {k:v for k,v in py.tables.items() if k != "areas"}
    assert glm.baseMVA == py.baseMVA
    assert glm.notes == []
