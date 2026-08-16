"""Discovery, validation, and execution of local instance setup scripts."""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TextIO

PROGRAM_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))) / "ec2instance_cmd"
PROGRAM_CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))) / "ec2instance_cmd"
PROVISION_SCRIPTS_DIR = PROGRAM_CONFIG_DIR / "provision_scripts"
USER_DATA_SCRIPTS_DIR = PROGRAM_CONFIG_DIR / "user_data_scripts"

_SECTION_RE = re.compile(r"^\s*####\s*(.*?)\s*$")
_GETOPTS_RE = re.compile(r"\bgetopts\s+([\"'])(.*?)\1")
_POSITIONAL_RE = re.compile(r"\$(?:\{)?(?:1|@|\*)\b")
_USAGE_RE = re.compile(
    r"\[-i\s+<[^>]+>\].*\[-p\s+<[^>]+>\].*\[user@hostname\]",
    re.DOTALL,
)


def script_sections(path: Path) -> list[str]:
    sections = []
    for line in path.read_text(errors="replace").splitlines():
        match = _SECTION_RE.match(line)
        if match and match.group(1):
            sections.append(match.group(1))
    return sections


def validate_script(path: Path, *, provisioning: bool = False) -> list[str]:
    """Return validation errors for a Bash script, or an empty list if valid."""
    errors = []
    bash = shutil.which("bash")
    if bash is None:
        return ["cannot validate Bash syntax because `bash` is not installed"]

    syntax = subprocess.run(
        [bash, "-n", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if syntax.returncode:
        detail = syntax.stderr.strip() or "invalid Bash syntax"
        errors.append(detail)

    if provisioning:
        content = path.read_text(errors="replace")
        getopts = _GETOPTS_RE.search(content)
        option_spec = getopts.group(2) if getopts else ""
        if "i:" not in option_spec or "p:" not in option_spec:
            errors.append("must parse both `-i <ssh_keyfile>` and `-p <port>` using getopts")
        if not _POSITIONAL_RE.search(content):
            errors.append("must accept a positional `user@hostname` argument")
        if not _USAGE_RE.search(content):
            errors.append("must document `[-i <ssh_keyfile>] [-p <port>] [user@hostname]` in its usage text")
    return errors


def _scripts_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.sh"), key=lambda path: path.name.casefold())


def _bold_filename(filename: str, output: TextIO) -> str:
    is_terminal = getattr(output, "isatty", lambda: False)()
    return "\033[1m%s\033[0m" % filename if is_terminal else filename


def _script_heading(heading: str, directory: Path, output: TextIO) -> tuple[str, int]:
    path_label = "(%s)" % directory
    is_terminal = getattr(output, "isatty", lambda: False)()
    rendered_path = "\033[2m%s\033[0m" % path_label if is_terminal else path_label
    return "%s %s" % (heading, rendered_path), len(heading) + 1 + len(path_label)


def list_and_validate_scripts(
    config_dir: Path = PROGRAM_CONFIG_DIR,
    *,
    output: TextIO | None = None,
) -> bool:
    """List both script libraries and return whether every script is valid."""
    output = output or sys.stdout
    all_valid = True
    libraries = (
        ("Provisioning Scripts", config_dir / "provision_scripts", True),
        ("User Data Scripts", config_dir / "user_data_scripts", False),
    )
    for heading, directory, provisioning in libraries:
        rendered_heading, heading_width = _script_heading(heading, directory, output)
        print(rendered_heading, file=output)
        print("=" * heading_width, file=output)
        scripts = _scripts_in(directory)
        if not scripts:
            print("(none)", file=output)
        for path in scripts:
            errors = validate_script(path, provisioning=provisioning)
            status = " [INVALID]" if errors else ""
            print("- %s%s" % (_bold_filename(path.name, output), status), file=output)
            if provisioning:
                for section in script_sections(path):
                    print("  - %s" % section, file=output)
            for error in errors:
                print("  ! %s" % error, file=output)
            all_valid = all_valid and not errors
        print(file=output)
    return all_valid


def resolve_provisioning_script(
    name: str,
    directory: Path = PROVISION_SCRIPTS_DIR,
) -> Path:
    """Resolve and validate a provisioning script by library name."""
    if Path(name).name != name:
        raise ValueError("Provisioning script must be a filename, not a path")
    filename = name if name.endswith(".sh") else "%s.sh" % name
    path = directory / filename
    if not path.is_file():
        raise ValueError("Provisioning script '%s' was not found in %s" % (filename, directory))
    errors = validate_script(path, provisioning=True)
    if errors:
        raise ValueError("Invalid provisioning script '%s': %s" % (filename, "; ".join(errors)))
    return path


def run_provisioning_script(
    path: Path,
    *,
    ssh_keyfile: str,
    port: int,
    target: str,
) -> None:
    """Run a validated provisioning script in the foreground on the client."""
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError("Cannot run provisioning script because `bash` is not installed")
    print("running %s provisioning script..." % path.name, flush=True)
    subprocess.run(
        [bash, str(path), "-i", ssh_keyfile, "-p", str(port), target],
        check=True,
    )
    logging.info("Provisioning script %s completed.", path.name)
