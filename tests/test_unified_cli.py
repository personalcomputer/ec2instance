import sys

import pytest

from ec2instance import cli


def test_explicit_provider_dispatch(monkeypatch):
    seen = {}

    def fake_dispatch(provider, argv, *, unified_output):
        seen.update(provider=provider, argv=argv, unified_output=unified_output)

    monkeypatch.setattr(cli, "dispatch", fake_dispatch)
    monkeypatch.setattr(sys, "argv", ["ec2instance", "hetzner", "--help"])
    cli.main()

    assert seen == {"provider": "hetzner", "argv": ["--help"], "unified_output": True}


def test_legacy_aws_flags_dispatch_to_aws(monkeypatch):
    seen = {}

    def fake_dispatch(provider, argv, *, unified_output):
        seen.update(provider=provider, argv=argv, unified_output=unified_output)

    monkeypatch.setattr(cli, "dispatch", fake_dispatch)
    monkeypatch.setattr(sys, "argv", ["ec2instance", "--type", "t3.micro"])
    cli.main()

    assert seen == {
        "provider": "aws",
        "argv": ["--type", "t3.micro"],
        "unified_output": False,
    }


def test_no_arguments_preserves_legacy_aws_launch(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli,
        "dispatch",
        lambda provider, argv, *, unified_output: seen.update(
            provider=provider, argv=argv, unified_output=unified_output
        ),
    )
    monkeypatch.setattr(sys, "argv", ["ec2instance"])
    cli.main()
    assert seen == {"provider": "aws", "argv": [], "unified_output": False}


def test_unknown_provider_errors(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ec2instance", "unknown"])
    with pytest.raises(SystemExit, match="Unknown provider"):
        cli.main()


def test_top_level_help_lists_all_providers(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ec2instance", "--help"])
    cli.main()
    output = capsys.readouterr().out
    assert "{aws,hetzner,vast,list-scripts}" in output
    assert "ec2instance PROVIDER --help" in output


def test_list_scripts_command_succeeds_when_valid(monkeypatch):
    monkeypatch.setattr("ec2instance.core.list_and_validate_scripts", lambda: True)
    monkeypatch.setattr(sys, "argv", ["ec2instance", "list-scripts"])
    cli.main()


def test_list_scripts_command_fails_validation(monkeypatch):
    monkeypatch.setattr("ec2instance.core.list_and_validate_scripts", lambda: False)
    monkeypatch.setattr(sys, "argv", ["ec2instance", "list-scripts"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


@pytest.mark.parametrize(
    ("module_name", "program"),
    [
        ("ec2instance.main", "ec2instance"),
        ("hetznerinstance.main", "hetznerinstance"),
        ("vastinstance.main", "vastinstance"),
    ],
)
def test_provider_compatibility_commands_support_list_scripts(monkeypatch, module_name, program):
    module = __import__(module_name, fromlist=["main"])
    called = {}
    monkeypatch.setattr(module, "list_and_validate_scripts", lambda: called.setdefault("called", True))
    monkeypatch.setattr(sys, "argv", [program, "list-scripts"])
    module.main()
    assert called == {"called": True}
