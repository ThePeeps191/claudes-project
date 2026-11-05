import tomllib
from pathlib import Path

import pytest

from tinyco.config import parse

EXAMPLES = sorted((Path(__file__).resolve().parents[1] / "examples").glob("*/company.toml"))


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.parent.name for p in EXAMPLES])
def test_example_parses(path):
    company = parse(tomllib.loads(path.read_text()), path.parent)
    assert company.top is not None
    assert company.org_chart()


def test_there_are_examples():
    assert len(EXAMPLES) >= 4
