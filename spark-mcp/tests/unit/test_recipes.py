"""Tests for spark_mcp.recipes."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from spark_mcp.recipes import RecipeStore, validate_recipe_name

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sample_recipes"


@pytest.fixture
def store(tmp_path: Path) -> RecipeStore:
    target = tmp_path / "recipes"
    target.mkdir()
    for f in FIXTURES.glob("*.yaml"):
        (target / f.name).write_text(f.read_text())
    return RecipeStore(recipe_dir=target)


async def test_list_recipes(store: RecipeStore) -> None:
    summaries = await store.list_recipes()
    names = sorted(s.name for s in summaries)
    assert "Gemma4-26B-A4B" in names
    assert "Qwen3.5-122B-FP8" in names
    # invalid fixture is silently skipped
    assert "Invalid" not in names


async def test_load_recipe_parses(store: RecipeStore) -> None:
    recipe = await store.load_recipe("gemma4-26b-a4b")
    assert recipe.model == "google/gemma-4-26B-A4B-it"
    assert recipe.defaults.tensor_parallel == 2


async def test_load_recipe_missing_raises(store: RecipeStore) -> None:
    with pytest.raises(FileNotFoundError):
        await store.load_recipe("does-not-exist")


async def test_validate_invalid_yaml_returns_errors(store: RecipeStore) -> None:
    result = await store.validate_text("recipe_version: '1'\nname: x")
    assert result.valid is False
    assert any("model" in e.lower() for e in result.errors)


async def test_cluster_only_supports_flags(store: RecipeStore) -> None:
    summaries = {s.name: s for s in await store.list_recipes()}
    qwen = summaries["Qwen3.5-122B-FP8"]
    assert qwen.supports_cluster is True
    assert qwen.supports_solo is False


@pytest.mark.parametrize(
    "bad_name",
    [
        "../etc/passwd",
        "/absolute/path",
        "../../escape",
        ".",
        "..",
        "A-CAP",  # uppercase rejected
        "has space",
        "",
        "a" * 64,
        "foo\x00.yaml",
    ],
)
def test_validate_recipe_name_rejects_bad(bad_name: str) -> None:
    """B2: name regex must reject every path-traversal / weird input."""
    with pytest.raises(ValueError, match="Invalid recipe name"):
        validate_recipe_name(bad_name)


def test_validate_recipe_name_accepts_good() -> None:
    """Well-formed slugs are accepted."""
    for good in ("a", "gemma4", "qwen-3.5-122b", "minimax_m2-awq"):
        validate_recipe_name(good)  # must not raise


async def test_create_and_delete_recipe_roundtrip(store: RecipeStore, tmp_path: Path) -> None:
    yaml_text = dedent("""
        recipe_version: "1"
        name: Dummy
        description: test
        model: org/dummy
        defaults:
          port: 8000
          host: 0.0.0.0
          tensor_parallel: 1
          gpu_memory_utilization: 0.9
        command: "true"
    """).strip()
    # Filename slug must match _slugify("Dummy") == "dummy"
    created = await store.create_recipe("dummy", yaml_text)
    assert created.success is True
    assert (store.recipe_dir / "dummy.yaml").exists()
    # Second create hits the exclusive-create guard (M1)
    again = await store.create_recipe("dummy", yaml_text)
    assert again.success is False
    assert again.error is not None
    assert again.error.code == "RECIPE_EXISTS"
    # Idempotent delete (B17)
    deleted = await store.delete_recipe("dummy")
    assert deleted.success is True
    assert deleted.data and deleted.data["was_present"] is True
    # Delete again — still success but was_present=False
    again_del = await store.delete_recipe("dummy")
    assert again_del.success is True
    assert again_del.data and again_del.data["was_present"] is False


async def test_create_recipe_rejects_name_mismatch(store: RecipeStore) -> None:
    """A25: YAML `name:` slug must match the filename argument."""
    yaml_text = dedent("""
        recipe_version: "1"
        name: TotallyDifferentName
        description: test
        model: org/x
        defaults:
          port: 8000
          host: 0.0.0.0
          tensor_parallel: 1
          gpu_memory_utilization: 0.9
        command: "true"
    """).strip()
    result = await store.create_recipe("dummy", yaml_text)
    assert result.success is False
    assert result.error is not None
    assert "name" in result.error.message.lower()


async def test_create_recipe_rejects_path_traversal(store: RecipeStore) -> None:
    """B2: the traversal guard must fire before any filesystem work."""
    result = await store.create_recipe("../evil", "recipe_version: '1'\n")
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "RECIPE_INVALID"


async def test_validate_text_rejects_oversized(store: RecipeStore) -> None:
    """B11: a 1 MiB+ payload is rejected without hitting yaml.safe_load."""
    huge = "a: " + "x" * (1024 * 1024 + 10)
    result = await store.validate_text(huge)
    assert result.valid is False
    assert any("bytes" in e for e in result.errors)


async def test_update_recipe_missing(store: RecipeStore) -> None:
    yaml_text = dedent("""
        recipe_version: "1"
        name: notthere
        description: test
        model: org/x
        defaults:
          port: 8000
          host: 0.0.0.0
          tensor_parallel: 1
          gpu_memory_utilization: 0.9
        command: "true"
    """).strip()
    result = await store.update_recipe("notthere", yaml_text)
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "RECIPE_NOT_FOUND"
