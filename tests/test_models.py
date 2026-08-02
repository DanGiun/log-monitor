import pytest
from pydantic import ValidationError

from log_viewer.models import AppConfig, AppSettings, SourceConfig, SourceKind, SSHConfig, Workspace


@pytest.mark.unit
@pytest.mark.parametrize("offset", [-23, -1, 0, 1, 23])
def test_timezone_offset_boundary_accepts_valid_values(offset):
    assert SourceConfig(name="x", path="/tmp/x", timezone_offset_hours=offset).timezone_offset_hours == offset


@pytest.mark.unit
@pytest.mark.parametrize("offset", [-24, 24, 100])
def test_timezone_offset_rejects_outside_partition(offset):
    with pytest.raises(ValidationError):
        SourceConfig(name="x", path="/tmp/x", timezone_offset_hours=offset)


@pytest.mark.unit
def test_ssh_source_requires_ssh_configuration():
    with pytest.raises(ValidationError):
        SourceConfig(name="remote", kind=SourceKind.SSH, path="/log")


@pytest.mark.unit
def test_ssh_source_accepts_key_configuration():
    source = SourceConfig(
        name="remote", kind=SourceKind.SSH, path="/log", ssh=SSHConfig(host="host", username="qa")
    )
    assert source.ssh.port == 22


@pytest.mark.unit
@pytest.mark.parametrize("color", ["red", "#abcd", "#gg0000", "123456"])
def test_invalid_colors_are_rejected(color):
    with pytest.raises((ValidationError, ValueError)):
        SourceConfig(name="x", path="/tmp/x", color=color)


@pytest.mark.unit
def test_config_restores_default_workspace_and_active_id():
    config = AppConfig(workspaces=[Workspace(id="other", name="Other")], active_workspace_id="missing")
    assert config.workspaces[0].id == "default"
    assert config.active_workspace_id == "default"


@pytest.mark.unit
def test_disk_buffer_default_is_five_gibibytes():
    assert AppSettings().disk_buffer_bytes == 5 * 1024**3


@pytest.mark.unit
@pytest.mark.parametrize("value", [99 * 1024**2, 21 * 1024**3])
def test_disk_buffer_rejects_outside_configured_limits(value):
    with pytest.raises(ValidationError):
        AppSettings(disk_buffer_bytes=value)
