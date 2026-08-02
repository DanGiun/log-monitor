
import pytest

from log_viewer.config import ConfigStore
from log_viewer.models import SourceConfig, Workspace


@pytest.mark.integration
def test_new_store_contains_default_workspace(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    assert store.get().workspaces[0].id == "default"


@pytest.mark.integration
def test_update_is_persisted_and_reloaded(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.update(lambda cfg: cfg.sources.append(SourceConfig(name="app", path="/tmp/app.log")))
    loaded = ConfigStore(path).get()
    assert loaded.sources[0].name == "app"


@pytest.mark.integration
def test_configuration_file_has_private_permissions(tmp_path):
    path = tmp_path / "nested" / "config.json"
    store = ConfigStore(path)
    store.update(lambda cfg: cfg.workspaces.append(Workspace(name="QA")))
    assert oct(path.stat().st_mode & 0o777) == "0o600"


@pytest.mark.integration
def test_invalid_configuration_is_backed_up_and_defaults_used(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("not-json", encoding="utf-8")
    store = ConfigStore(path)
    assert store.get().active_workspace_id == "default"
    assert path.with_suffix(".invalid.json").exists()


@pytest.mark.integration
def test_mutating_returned_copy_does_not_change_store(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    copy = store.get()
    copy.active_workspace_id = "changed"
    assert store.get().active_workspace_id == "default"
