"""spark-mcp command-line interface.

Security-critical amendments inlined:
- B3: `spark-mcp ssh-trust <worker>` validates hostname against a strict regex
  and invokes ssh-keyscan with argv-list (no shell).
- B10: On init, parent dir is chmod 0o700; config.toml 0o644; env file 0o600;
  the systemd unit is written to ~/.config/spark-mcp/ (not /tmp, symlink-attack
  surface on shared systems).
- B12: Auth token is not printed to stdout by default; --print-token requires
  --force when run under CI=true.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import secrets
from asyncio import create_subprocess_exec as _spawn_subprocess
from pathlib import Path

from rich.console import Console

from . import __version__
from .config import (
    default_env_template_path,
    default_template_path,
    load_config,
    resolve_paths,
)
from .server import serve

console = Console()

_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,253}$")
_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")


def _init_files(profile: str | None, print_token: bool, force: bool) -> int:
    toml_path, env_path = resolve_paths(profile=profile)
    toml_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        toml_path.parent.chmod(0o700)

    if toml_path.exists():
        console.print(f"[yellow]Config already exists at {toml_path}; leaving untouched.[/]")
    else:
        toml_path.write_text(default_template_path().read_text())
        toml_path.chmod(0o644)
        console.print(f"[green]Wrote config template to {toml_path}[/]")

    generated_token: str | None = None
    if env_path.exists():
        console.print(f"[yellow]Env already exists at {env_path}; leaving untouched.[/]")
    else:
        template = default_env_template_path().read_text()
        generated_token = "sk-spark-" + secrets.token_urlsafe(32)
        populated = template.replace(
            "SPARK_MCP_AUTH_TOKEN=", f"SPARK_MCP_AUTH_TOKEN={generated_token}"
        )
        env_path.write_text(populated)
        env_path.chmod(0o600)
        console.print(f"[green]Wrote env file to {env_path} (chmod 600)[/]")

    if generated_token is not None:
        if print_token:
            if os.environ.get("CI") and not force:
                console.print(
                    "[red]Refusing --print-token under CI=true. Re-run with --force to override.[/]"
                )
                return 2
            console.print(f"[green]Generated auth token: {generated_token}[/]")
        else:
            console.print(
                f"[yellow]Auth token stored in {env_path}. "
                f"View with: grep SPARK_MCP_AUTH_TOKEN {env_path}[/]"
            )

    systemd_tpl = (Path(__file__).parent / "templates" / "systemd.service.template").read_text()
    user = Path.home().name
    if not _USER_RE.match(user):
        console.print(
            f"[yellow]Username {user!r} contains characters outside systemd's "
            "portable set. Edit the generated unit before installing.[/]"
        )
    unit = systemd_tpl.format(
        user=user,
        exec_path=str(Path.home() / ".local" / "bin" / "spark-mcp"),
        env_file=str(env_path),
        working_dir=str(Path.home()),
    )
    out = toml_path.parent / "spark-mcp.service"
    out.write_text(unit)
    out.chmod(0o644)
    console.print(f"[green]systemd unit template written to {out}[/]")
    console.print("[cyan]Next: edit config.toml with your cluster details, then:[/]")
    console.print(f"  sudo cp {out} /etc/systemd/system/")
    console.print("  sudo systemctl enable --now spark-mcp")
    return 0


async def _ssh_trust(worker: str) -> int:
    """B3/C3: validate hostname against a strict regex before invoking ssh-keyscan."""
    if not _HOSTNAME_RE.match(worker):
        console.print(f"[red]Invalid hostname: {worker!r}[/]")
        return 2
    kh_dir = Path("~/.config/spark-mcp").expanduser()  # noqa: ASYNC240  # one-shot CLI, not a long-running async server
    kh_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    kh_path = kh_dir / "known_hosts"

    proc = await _spawn_subprocess(
        "ssh-keyscan",
        "-H",
        worker,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        console.print(f"[red]ssh-keyscan failed: {stderr_b.decode(errors='replace')}[/]")
        return 1
    console.print(f"[yellow]New host key for {worker}:[/]")
    console.print(stdout_b.decode(errors="replace"))
    console.print(
        "[cyan]Verify this fingerprint out-of-band (physical console on the worker) "
        "before confirming.[/]"
    )
    confirm = input("Trust this key? [yes/NO]: ").strip().lower()  # noqa: ASYNC250  # interactive CLI prompt, intentional
    if confirm != "yes":
        console.print("[yellow]Aborted.[/]")
        return 3
    with kh_path.open("ab") as fh:
        fh.write(stdout_b)
    kh_path.chmod(0o600)
    console.print(f"[green]Appended to {kh_path}[/]")
    return 0


def _check(profile: str | None) -> int:
    cfg = load_config(profile=profile)
    console.print(f"[green]Config OK:[/] {cfg.config_path}")
    console.print(f"  cluster: {cfg.cluster.name} ({len(cfg.cluster.workers)} workers)")
    console.print(f"  transport: {cfg.server.transport} on {cfg.server.host}:{cfg.server.port}")
    return 0


def _run(profile: str | None) -> int:
    cfg = load_config(profile=profile)
    try:
        asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        console.print("[yellow]Shutting down[/]")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spark-mcp", description="MCP server for DGX Spark clusters")
    p.add_argument(
        "--profile",
        default=None,
        help="Profile name (maps to profiles/<name>.{toml,env})",
    )
    sub = p.add_subparsers(dest="command")

    init_p = sub.add_parser("init", help="First-time setup: create config + env + systemd template")
    init_p.add_argument(
        "--print-token",
        action="store_true",
        help="Print the generated token to stdout (not recommended)",
    )
    init_p.add_argument("--force", action="store_true", help="Override CI safety checks")

    trust_p = sub.add_parser(
        "ssh-trust",
        help="Append a worker's host key to known_hosts after confirmation",
    )
    trust_p.add_argument(
        "worker",
        help="Hostname (must match ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,253}$)",
    )

    sub.add_parser("check", help="Load config and print summary")
    sub.add_parser("version", help="Print version")
    sub.add_parser("serve", help="Run the MCP server (default)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)
    cmd = ns.command or "serve"
    if cmd == "init":
        return _init_files(ns.profile, print_token=ns.print_token, force=ns.force)
    if cmd == "ssh-trust":
        return asyncio.run(_ssh_trust(ns.worker))
    if cmd == "version":
        console.print(__version__)
        return 0
    if cmd == "check":
        return _check(ns.profile)
    if cmd == "serve":
        return _run(ns.profile)
    parser.print_help()
    return 1
