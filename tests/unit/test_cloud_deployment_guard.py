from pathlib import Path


def test_cloud_overlay_forces_authenticated_production() -> None:
    root = Path(__file__).parents[2]
    overlay = (root / 'deploy/cloud/docker-compose.cloud.yml').read_text(encoding='utf-8')

    assert '  app:\n    environment:\n      APP_ENV: "production"\n      AUTH_ENABLED: "true"\n' in overlay
