import io
import subprocess
from pathlib import Path

import pytest

from ec2instance.core import scripts

VALID_PROVISIONING_SCRIPT = r"""#!/usr/bin/env bash
usage() {
    echo "Usage: $0 [-i <ssh_keyfile>] [-p <port>] [user@hostname]"
}
while getopts "i:p:" opt; do
    case "$opt" in
        i) key="$OPTARG" ;;
        p) port="$OPTARG" ;;
        *) usage; exit 1 ;;
    esac
done
shift $((OPTIND - 1))
target="${1:-root}"
#### Copy project files to the host.
echo "$key $port $target"
#### Configure the application.
true
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_validates_provisioning_contract_and_extracts_sections(tmp_path):
    path = _write(tmp_path / "provision.sh", VALID_PROVISIONING_SCRIPT)
    assert scripts.validate_script(path, provisioning=True) == []
    assert scripts.script_sections(path) == [
        "Copy project files to the host.",
        "Configure the application.",
    ]


def test_rejects_invalid_bash(tmp_path):
    path = _write(tmp_path / "broken.sh", "if true; then\n")
    errors = scripts.validate_script(path)
    assert errors
    assert "syntax error" in errors[0]


def test_rejects_missing_provisioning_contract(tmp_path):
    path = _write(tmp_path / "incomplete.sh", "#!/bin/bash\necho hello\n")
    errors = scripts.validate_script(path, provisioning=True)
    assert any("-i <ssh_keyfile>" in error for error in errors)
    assert any("user@hostname" in error for error in errors)


def test_list_scripts_prints_sections_and_validation_errors(tmp_path):
    _write(tmp_path / "provision_scripts" / "good.sh", VALID_PROVISIONING_SCRIPT)
    _write(tmp_path / "provision_scripts" / "bad.sh", "#!/bin/bash\necho bad\n")
    _write(tmp_path / "user_data_scripts" / "boot.sh", "#!/bin/bash\necho boot\n")
    output = io.StringIO()

    assert scripts.list_and_validate_scripts(tmp_path, output=output) is False
    rendered = output.getvalue()
    assert "Provisioning Scripts (%s/provision_scripts)" % tmp_path in rendered
    assert "- good.sh" in rendered
    assert "  - Copy project files to the host." in rendered
    assert "- bad.sh [INVALID]" in rendered
    assert "User Data Scripts (%s/user_data_scripts)" % tmp_path in rendered
    assert "- boot.sh" in rendered


def test_list_scripts_bolds_filenames_in_terminal_output(tmp_path):
    _write(tmp_path / "user_data_scripts" / "boot.sh", "#!/bin/bash\necho boot\n")

    class TerminalOutput(io.StringIO):
        def isatty(self):
            return True

    output = TerminalOutput()
    assert scripts.list_and_validate_scripts(tmp_path, output=output) is True
    assert "User Data Scripts \033[2m(%s/user_data_scripts)\033[0m" % tmp_path in output.getvalue()
    assert "- \033[1mboot.sh\033[0m" in output.getvalue()


def test_resolve_provisioning_script_accepts_stem(tmp_path):
    path = _write(tmp_path / "example.sh", VALID_PROVISIONING_SCRIPT)
    assert scripts.resolve_provisioning_script("example", tmp_path) == path


def test_resolve_provisioning_script_rejects_paths(tmp_path):
    with pytest.raises(ValueError, match="filename"):
        scripts.resolve_provisioning_script("../example.sh", tmp_path)


def test_run_provisioning_script_uses_required_arguments(tmp_path, monkeypatch, capsys):
    path = tmp_path / "example.sh"
    calls = []
    monkeypatch.setattr(scripts.shutil, "which", lambda name: "/bin/bash")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(scripts.subprocess, "run", fake_run)
    scripts.run_provisioning_script(
        path,
        ssh_keyfile="/tmp/key.pem",
        port=22022,
        target="ubuntu@example.com",
    )

    assert calls == [
        (
            [
                "/bin/bash",
                str(path),
                "-i",
                "/tmp/key.pem",
                "-p",
                "22022",
                "ubuntu@example.com",
            ],
            {"check": True},
        )
    ]
    assert capsys.readouterr().out == "running example.sh provisioning script...\n"
