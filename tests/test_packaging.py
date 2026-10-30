import json

from support import ROOT


def test_the_marketplace_lists_the_plugin_its_manifest_names_from_its_folder() -> None:
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())
    (listed,) = marketplace["plugins"]
    assert (listed["name"], listed["source"]) == ("pgnify", "./plugin")
    assert plugin["name"] == "pgnify"
