import sys

import pytest

from hetznerinstance import main


class TestListTypesSubcommand:
    def test_list_types_no_arg_calls_all(self, monkeypatch, capsys):
        called = {}

        def fake_list_server_types(loc_filter):
            called["loc_filter"] = loc_filter

        monkeypatch.setattr(main, "list_server_types", fake_list_server_types)
        monkeypatch.setattr(sys, "argv", ["hetznerinstance", "list-types"])
        with pytest.raises(SystemExit) as exc:
            main.main()
        assert exc.value.code == 0
        assert called == {"loc_filter": None}

    def test_list_types_with_location(self, monkeypatch):
        called = {}

        def fake_list_server_types(loc_filter):
            called["loc_filter"] = loc_filter

        monkeypatch.setattr(main, "list_server_types", fake_list_server_types)
        monkeypatch.setattr(sys, "argv", ["hetznerinstance", "list-types", "ash"])
        with pytest.raises(SystemExit) as exc:
            main.main()
        assert exc.value.code == 0
        assert called == {"loc_filter": "ash"}

    def test_list_types_unknown_location_errors(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["hetznerinstance", "list-types", "zzz"])
        with pytest.raises(SystemExit) as exc:
            main.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Unknown location 'zzz'" in err
        assert "fsn1" in err

    def test_list_types_flag_removed(self, monkeypatch):
        # --list-types is no longer a flag; passing it must error (exit 2)
        monkeypatch.setattr(sys, "argv", ["hetznerinstance", "--list-types"])
        with pytest.raises(SystemExit) as exc:
            main.main()
        assert exc.value.code == 2

    def test_launch_flow_still_requires_nothing_for_help(self, monkeypatch):
        # Ensure the subparser addition didn't break default help
        monkeypatch.setattr(sys, "argv", ["hetznerinstance", "--help"])
        with pytest.raises(SystemExit) as exc:
            main.main()
        assert exc.value.code == 0
