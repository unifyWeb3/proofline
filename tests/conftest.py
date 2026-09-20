from __future__ import annotations

import pytest

from proofline.fixtures import FIXTURE_NOW, all_fixtures


@pytest.fixture(scope="session")
def fixtures():
    return all_fixtures()


@pytest.fixture
def fixture_now():
    return FIXTURE_NOW
