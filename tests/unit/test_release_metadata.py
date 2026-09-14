import tomllib
from pathlib import Path

from app.config import settings


def test_package_metadata_matches_runtime_version():
    root = Path(__file__).parents[2]
    with (root / 'pyproject.toml').open('rb') as source:
        package_version = tomllib.load(source)['project']['version']
    assert package_version == settings.app_version
