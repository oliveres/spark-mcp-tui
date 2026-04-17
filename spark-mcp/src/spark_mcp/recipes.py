"""Recipe parsing and CRUD over vllm-docker YAML files.

Security-sensitive boundaries (audit trail: amendments B2, B11, B17, A16, A25, M1):
- `_path_for(name)` validates the name against a strict regex + resolves+checks
  the candidate is still inside `recipe_dir`, preventing path traversal.
- `validate_text` enforces a 1 MiB cap before invoking yaml.safe_load (YAML
  bomb / DoS mitigation).
- `create_recipe` uses exclusive-create (`open("x")`) to avoid TOCTOU between
  the exists-check and the write.
- `delete_recipe` is idempotent per PRD acceptance criterion (line 530).
- `create_recipe` cross-checks the YAML `name:` against the filename argument
  (slugified) so listings cannot mislabel the stored file.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import (
    ErrorInfo,
    OperationResult,
    Recipe,
    RecipeSummary,
    ValidationResult,
)

MAX_RECIPE_BYTES = 1 * 1024 * 1024  # 1 MiB
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


def validate_recipe_name(name: str) -> None:
    """Accept only filesystem-safe slugs. Rejects path-traversal payloads."""
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid recipe name {name!r}. "
            "Allowed: lowercase alphanum + dot/dash/underscore; 1-63 chars; starts with alphanum."
        )


def _format_errors(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "-", s.lower()).strip("-")[:63]


class RecipeStore:
    """CRUD wrapper over `{repo_path}/recipes/*.yaml`."""

    def __init__(self, recipe_dir: Path) -> None:
        self.recipe_dir = recipe_dir

    def _path_for(self, name: str) -> Path:
        """Resolve path for a recipe name; guard against traversal + absolute escapes."""
        validate_recipe_name(name)
        root = self.recipe_dir.resolve()
        candidate = (self.recipe_dir / f"{name}.yaml").resolve()
        if candidate.parent != root:
            raise ValueError(f"Recipe path escapes recipe_dir: {candidate}")
        return candidate

    def _read_recipe(self, path: Path) -> Recipe:
        raw = yaml.safe_load(path.read_text())
        return Recipe.model_validate(raw)

    async def list_recipes(self) -> list[RecipeSummary]:
        paths = sorted(self.recipe_dir.glob("*.yaml"))
        results: list[RecipeSummary] = []
        for p in paths:
            try:
                recipe = await asyncio.to_thread(self._read_recipe, p)
            except (ValidationError, yaml.YAMLError):
                continue
            results.append(
                RecipeSummary(
                    name=recipe.name,
                    description=recipe.description,
                    model=recipe.model,
                    supports_cluster=not recipe.solo_only,
                    supports_solo=not recipe.cluster_only,
                    is_model_cached={},
                    is_active=False,
                    path=p,
                )
            )
        return results

    async def load_recipe(self, name: str) -> Recipe:
        path = self._path_for(name)
        if not path.exists():
            raise FileNotFoundError(f"Recipe {name!r} not found in {self.recipe_dir}")
        return await asyncio.to_thread(self._read_recipe, path)

    async def validate_text(self, text: str) -> ValidationResult:
        if len(text.encode("utf-8")) > MAX_RECIPE_BYTES:
            return ValidationResult(
                valid=False,
                errors=[f"Recipe exceeds {MAX_RECIPE_BYTES} bytes"],
            )
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return ValidationResult(valid=False, errors=[f"YAML parse error: {exc}"])
        if not isinstance(raw, dict):
            return ValidationResult(valid=False, errors=["Recipe root must be a mapping"])
        try:
            recipe = Recipe.model_validate(raw)
        except ValidationError as exc:
            return ValidationResult(valid=False, errors=_format_errors(exc))
        return ValidationResult(valid=True, parsed=recipe)

    async def create_recipe(self, name: str, content: str) -> OperationResult:
        try:
            path = self._path_for(name)
        except ValueError as exc:
            return OperationResult(
                success=False,
                error=ErrorInfo(code="RECIPE_INVALID", message=str(exc)),
            )
        validation = await self.validate_text(content)
        if not validation.valid:
            return OperationResult(
                success=False,
                error=ErrorInfo(
                    code="RECIPE_INVALID",
                    message="Recipe YAML failed validation",
                    details={"errors": validation.errors},
                ),
            )
        # A25: cross-check YAML name slug against filename argument.
        if validation.parsed and _slugify(validation.parsed.name) != name:
            return OperationResult(
                success=False,
                error=ErrorInfo(
                    code="RECIPE_INVALID",
                    message="YAML `name:` does not match filename argument",
                    hint=(
                        f"Either rename the recipe to "
                        f"{_slugify(validation.parsed.name)!r} or change the YAML name."
                    ),
                ),
            )

        def _exclusive_write(p: Path, data: str) -> None:
            # M1: exclusive-create avoids TOCTOU between exists-check and write.
            with p.open("x") as fh:
                fh.write(data)

        try:
            await asyncio.to_thread(_exclusive_write, path, content)
        except FileExistsError:
            return OperationResult(
                success=False,
                error=ErrorInfo(
                    code="RECIPE_EXISTS",
                    message=f"Recipe {name!r} already exists",
                    hint="Use update_recipe to change an existing recipe.",
                ),
            )
        return OperationResult(success=True, data={"path": str(path)})

    async def update_recipe(self, name: str, content: str) -> OperationResult:
        try:
            path = self._path_for(name)
        except ValueError as exc:
            return OperationResult(
                success=False,
                error=ErrorInfo(code="RECIPE_INVALID", message=str(exc)),
            )
        validation = await self.validate_text(content)
        if not validation.valid:
            return OperationResult(
                success=False,
                error=ErrorInfo(
                    code="RECIPE_INVALID",
                    message="Recipe YAML failed validation",
                    details={"errors": validation.errors},
                ),
            )
        if not path.exists():
            return OperationResult(
                success=False,
                error=ErrorInfo(code="RECIPE_NOT_FOUND", message=f"Recipe {name!r} not found"),
            )
        # Atomic replace — write temp + rename.
        tmp = path.parent / (path.name + ".tmp")
        await asyncio.to_thread(tmp.write_text, content)
        await asyncio.to_thread(os.replace, tmp, path)
        return OperationResult(success=True, data={"path": str(path)})

    async def delete_recipe(self, name: str) -> OperationResult:
        """B17: idempotent — success regardless of whether the file existed."""
        try:
            path = self._path_for(name)
        except ValueError as exc:
            return OperationResult(
                success=False,
                error=ErrorInfo(code="RECIPE_INVALID", message=str(exc)),
            )
        was_present = path.exists()
        if was_present:
            await asyncio.to_thread(path.unlink)
        return OperationResult(
            success=True,
            data={"path": str(path), "was_present": was_present},
        )
