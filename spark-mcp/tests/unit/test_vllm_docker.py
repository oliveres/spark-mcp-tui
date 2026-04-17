"""Tests for spark_mcp.vllm_docker argv construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from spark_mcp.models import LaunchArgs
from spark_mcp.vllm_docker import (
    LaunchArgsError,
    build_hf_download_argv,
    build_launch_cluster_argv,
    build_run_recipe_argv,
)


def test_run_recipe_minimal() -> None:
    args = LaunchArgs(recipe_name="gemma4-26b-a4b")
    argv = build_run_recipe_argv(repo_path=Path("/x"), args=args)
    assert argv == ["/x/run-recipe.py", "gemma4-26b-a4b", "-d"]


def test_run_recipe_with_setup_and_solo() -> None:
    args = LaunchArgs(recipe_name="glm-4.7-flash-awq", setup=True, solo=True)
    argv = build_run_recipe_argv(repo_path=Path("/x"), args=args)
    assert argv == ["/x/run-recipe.py", "glm-4.7-flash-awq", "-d", "--setup", "--solo"]


def test_run_recipe_with_overrides() -> None:
    args = LaunchArgs(
        recipe_name="gemma4-26b-a4b",
        overrides={
            "port": 8123,
            "tensor_parallel": 4,
            "gpu_memory_utilization": 0.9,
        },
    )
    argv = build_run_recipe_argv(repo_path=Path("/x"), args=args)
    assert "--port" in argv
    assert argv[argv.index("--port") + 1] == "8123"
    assert "--tensor-parallel" in argv
    assert "--gpu-memory-utilization" in argv


def test_run_recipe_rejects_unknown_override_keys() -> None:
    """A17/B8: unknown override keys must raise, not silently pass through."""
    args = LaunchArgs(recipe_name="x", overrides={"bogus_flag": "hi"})
    with pytest.raises(LaunchArgsError, match="Unknown override keys"):
        build_run_recipe_argv(repo_path=Path("/x"), args=args)


def test_launch_cluster_argv() -> None:
    argv = build_launch_cluster_argv(repo_path=Path("/x"), workers=["w1", "w2"])
    assert argv[0] == "/x/launch-cluster.sh"
    assert "w1" in argv and "w2" in argv


def test_hf_download_argv_with_copy_to() -> None:
    argv = build_hf_download_argv(
        repo_path=Path("/x"),
        hf_id="Qwen/Qwen3.5-122B-FP8",
        copy_to="10.0.0.2",
        copy_parallel=True,
    )
    assert argv == [
        "/x/hf-download.sh",
        "Qwen/Qwen3.5-122B-FP8",
        "--copy-to",
        "10.0.0.2",
        "--copy-parallel",
    ]


def test_hf_download_argv_no_copy() -> None:
    argv = build_hf_download_argv(repo_path=Path("/x"), hf_id="x/y")
    assert argv == ["/x/hf-download.sh", "x/y"]


def test_hf_download_argv_copy_to_without_parallel() -> None:
    argv = build_hf_download_argv(
        repo_path=Path("/x"),
        hf_id="org/model",
        copy_to="10.0.0.2",
        copy_parallel=False,
    )
    assert argv == ["/x/hf-download.sh", "org/model", "--copy-to", "10.0.0.2"]
