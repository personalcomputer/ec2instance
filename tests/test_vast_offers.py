import sys

import pytest

from vastinstance import offers
from vastinstance.main import main


class TestBuildQuery:
    def test_default_query_kept(self):
        q = offers._build_query("rentable=true", False, False, False, False)
        assert "rentable=true" in q
        assert offers.DEFAULT_MIN_SPEC in q

    def test_no_min_spec_removes_min_spec(self):
        q = offers._build_query("rentable=true", False, False, False, True)
        assert "compute_cap" not in q

    def test_vm_only(self):
        q = offers._build_query("rentable=true", True, False, False, True)
        assert "vms_enabled=True" in q
        assert "vms_enabled=False" not in q

    def test_eur_appends_geolocation_in(self):
        q = offers._build_query("rentable=true", False, True, False, True)
        assert "geolocation in [" in q
        assert "DE" in q
        assert "GB" in q

    def test_sec_only(self):
        q = offers._build_query("rentable=true", False, False, True, True)
        assert "datacenter=true" in q

    def test_eur_and_sec_combined(self):
        q = offers._build_query("rentable=true", False, True, True, True)
        assert "geolocation in [" in q
        assert "datacenter=true" in q

    def test_extra_query_terms_preserved(self):
        q = offers._build_query("rentable=true num_gpus=1 gpu_name=RTX_4090", False, False, False, True)
        assert "num_gpus=1" in q
        assert "gpu_name=RTX_4090" in q


class TestTypeTag:
    def test_on_demand(self):
        assert offers._type_tag({"type": "on-demand"}) == "OD"

    def test_interrupt(self):
        assert offers._type_tag({"type": "interrupt"}) == "Int"

    def test_secure_datacenter(self):
        assert offers._type_tag({"type": "on-demand", "hosting_type": 1}) == "OD +Sec"

    def test_vm_capable(self):
        assert offers._type_tag({"type": "interrupt", "vms_enabled": True}) == "Int +VM"

    def test_sec_and_vm(self):
        assert offers._type_tag({"type": "on-demand", "hosting_type": 1, "vms_enabled": True}) == "OD +Sec +VM"


class TestGb:
    def test_mib_to_gb(self):
        assert offers._gb(1024) == 1.0

    def test_none(self):
        assert offers._gb(None) == 0.0

    def test_zero(self):
        assert offers._gb(0) == 0.0


class TestFormatLocation:
    def test_city_and_country(self):
        assert offers._format_location("Beijing, CN") == "Beijing, CN"

    def test_unknown_city_shows_country_without_comma(self):
        assert offers._format_location(", CN") == "CN"

    def test_missing_location(self):
        assert offers._format_location(None) == "?"


class TestProbeLatency:
    def test_no_ping_binary_returns_none(self, monkeypatch):
        monkeypatch.setattr(offers.shutil, "which", lambda b: None)
        assert offers.probe_latency("127.0.0.1") is None


class TestListTypesCli:
    def test_list_types_subcommand_dispatches(self, monkeypatch, capsys):
        # Ensure the list-types subcommand is wired up and calls offers.list_types
        called = {}

        def fake_list_types(vast, args):
            called["called"] = True
            called["n"] = args.n

        class FakeVastAI:
            def __init__(self, *a, **kw):
                pass

        monkeypatch.setattr(offers, "list_types", fake_list_types)
        monkeypatch.setattr("vastinstance.main.VastAI", FakeVastAI)
        monkeypatch.setattr(sys, "argv", ["vastinstance", "list-types", "-n", "5"])
        main()
        assert called == {"called": True, "n": 5}

    def test_list_types_eur_flag_parsed(self, monkeypatch):
        seen = {}

        def fake_list_types(vast, args):
            seen["eur"] = args.eur
            seen["sec_only"] = args.sec_only
            seen["latency"] = args.latency

        class FakeVastAI:
            def __init__(self, *a, **kw):
                pass

        monkeypatch.setattr(offers, "list_types", fake_list_types)
        monkeypatch.setattr("vastinstance.main.VastAI", FakeVastAI)
        monkeypatch.setattr(sys, "argv", ["vastinstance", "list-types", "--eur", "--sec-only", "--latency"])
        main()
        assert seen == {"eur": True, "sec_only": True, "latency": True}

    def test_pricing_mode_flags_parsed(self, monkeypatch):
        seen = {}

        def fake_list_types(vast, args):
            seen.update(od_only=args.od_only, int_only=args.int_only)

        class FakeVastAI:
            def __init__(self, *a, **kw):
                pass

        monkeypatch.setattr(offers, "list_types", fake_list_types)
        monkeypatch.setattr("vastinstance.main.VastAI", FakeVastAI)

        monkeypatch.setattr(sys, "argv", ["vastinstance", "list-types", "--od-only"])
        main()
        assert seen == {"od_only": True, "int_only": False}

        monkeypatch.setattr(sys, "argv", ["vastinstance", "list-types", "-int-only"])
        main()
        assert seen == {"od_only": False, "int_only": True}

    def test_removed_flags_are_rejected(self, monkeypatch):
        for flag in ("--container-only", "--on-demand-only", "--interruptible-only"):
            monkeypatch.setattr(sys, "argv", ["vastinstance", "list-types", flag])
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 2

    def test_query_help_includes_examples(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["vastinstance", "list-types", "--help"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        output = capsys.readouterr().out
        assert "query examples:" in output
        assert "gpu_name=RTX_4090" in output
        assert "vms_enabled=false" in output

    def test_launch_flow_unaffected_by_subparser(self, monkeypatch):
        # The default launch flow must still require -t/--type
        monkeypatch.setattr(sys, "argv", ["vastinstance"])
        with pytest.raises(SystemExit):
            main()
