"""Unified command-line dispatcher for the supported compute providers."""

import argparse
import importlib
import sys

PROVIDERS = {
    "aws": ("ec2instance.main", "AWS EC2"),
    "hetzner": ("hetznerinstance.main", "Hetzner Cloud"),
    "vast": ("vastinstance.main", "vast.ai"),
}


def _print_help() -> None:
    parser = argparse.ArgumentParser(
        prog="ec2instance",
        description="Launch a temporary compute instance and connect to it over SSH.",
        epilog="Run `ec2instance PROVIDER --help` for provider-specific options.",
    )
    parser.add_argument(
        "provider",
        nargs="?",
        choices=[*PROVIDERS, "list-scripts"],
        help="compute provider or script-library command",
    )
    parser.print_help()


def dispatch(provider: str, argv: list[str], *, unified_output: bool) -> None:
    module_name, _description = PROVIDERS[provider]
    module = importlib.import_module(module_name)
    previous_argv = sys.argv
    try:
        sys.argv = ["%s %s" % (previous_argv[0], provider), *argv]
        module.main(unified_output=unified_output)
    finally:
        sys.argv = previous_argv


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"-h", "--help"}:
        _print_help()
        return
    if args and args[0] == "list-scripts":
        from ec2instance.core import list_and_validate_scripts

        if not list_and_validate_scripts():
            raise SystemExit(1)
        return
    if args and args[0] in {"providers", "--providers"}:
        _print_help()
        return

    # Backward compatibility: the original command launches AWS when no provider
    # is named. Existing AWS flags and scripts therefore continue to work.
    if not args or args[0].startswith("-"):
        dispatch("aws", args, unified_output=False)
        return

    provider = args.pop(0)
    if provider not in PROVIDERS:
        choices = ", ".join(PROVIDERS)
        raise SystemExit("Unknown provider '%s'. Choose one of: %s" % (provider, choices))
    dispatch(provider, args, unified_output=True)


if __name__ == "__main__":
    main()
