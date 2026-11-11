import json

from app.config import settings
from app.main import app_manifest


def base_manifest():
    return json.loads((settings.frontend_dir / "manifest.webmanifest").read_text(encoding="utf-8"))


def test_prod_manifest_is_served_unchanged():
    assert app_manifest(base_manifest(), "prod") == base_manifest()


def test_dev_manifest_is_rebranded_but_keeps_link_handling():
    dev = app_manifest(base_manifest(), "dev")
    assert dev["short_name"] == "Trainspotter DEV"
    assert all(icon["src"].startswith("icons/dev/") for icon in dev["icons"])
    assert all((settings.frontend_dir / icon["src"]).is_file() for icon in dev["icons"])
    assert dev["scope"] == base_manifest()["scope"]
    assert dev["launch_handler"] == base_manifest()["launch_handler"]
