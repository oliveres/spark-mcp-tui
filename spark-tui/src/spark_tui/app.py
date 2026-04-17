"""Textual TUI for spark-mcp.

Combines all screens, widgets, and modals per PRD §Component 3 guidance to
co-locate view components. Designed around four regions: header, node-status
panels, recipes table, and logs panel.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Log, Static

from .config import TuiConfig, load_tui_config
from .mcp_client import McpClient, OfflineError

log = logging.getLogger(__name__)

THEMES = ["textual-dark", "textual-light", "dracula", "nord", "solarized-light"]
_FRIENDLY_THEME_MAP = {
    "dark": "textual-dark",
    "light": "textual-light",
    "dracula": "dracula",
    "nord": "nord",
    "solarized": "solarized-light",
    "solarized-light": "solarized-light",
    "textual-dark": "textual-dark",
    "textual-light": "textual-light",
}


def _resolve_theme(name: str) -> str:
    return _FRIENDLY_THEME_MAP.get(name, "textual-dark")


class NodeBox(Static):
    """Card showing GPU stats + container state for a single node."""

    def __init__(self, node_name: str) -> None:
        super().__init__("", id=f"node-{node_name}")
        self.node_name = node_name

    def update_from(self, node: dict[str, Any]) -> None:
        gpu = node.get("gpu") or {}
        mem_used = gpu.get("memory_used_mb", 0) / 1024
        mem_total = gpu.get("memory_total_mb", 0) / 1024
        util = gpu.get("utilization_pct", 0)
        temp = gpu.get("temperature_c", 0)
        pwr = gpu.get("power_watts", 0)
        running = ",".join(node.get("docker_running_containers") or []) or "-"
        self.update(
            f"[b]{self.node_name}[/]\n"
            f"GPU {util}%  {mem_used:.0f}/{mem_total:.0f} GB\n"
            f"Temp {temp}C  Pwr {pwr}W\n"
            f"Cont: {running}"
        )


class HelpModal(ModalScreen[None]):
    """Keybinding cheatsheet."""

    BINDINGS: ClassVar[list[Any]] = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(
            "[b]spark-tui keybindings[/]\n\n"
            "Enter  Start selected recipe\n"
            "S      Stop active model\n"
            "R      Restart active\n"
            "D      Download model for selected\n"
            "N      New recipe (wizard)\n"
            "E      Edit selected recipe\n"
            "X      Delete selected recipe\n"
            "L      Toggle log panel\n"
            "F      Filter recipes\n"
            "P      Profile selector\n"
            "/      Search\n"
            "T      Cycle theme\n"
            "?      This help\n"
            "Q      Quit\n"
        )


class SparkTui(App[None]):
    """Main TUI application."""

    CSS = """
    Screen { layout: vertical; }
    #status-row { height: 8; }
    #main-row { height: 1fr; }
    #recipes-row { width: 2fr; border: tall $primary-darken-2; }
    #logs-row { width: 3fr; border: tall $primary-darken-2; }
    #recipes-row.hidden { display: none; }
    #logs-row.hidden { display: none; }
    NodeBox { border: tall $primary; padding: 0 1; width: 1fr; }
    """

    BINDINGS: ClassVar[list[Any]] = [
        # DataTable consumes `enter` for row-select; `space` is our visible
        # Start binding. Enter still works via the RowSelected handler below.
        Binding("space", "start_recipe", "Start"),
        Binding("s", "stop_cluster", "Stop"),
        Binding("r", "restart_cluster", "Restart"),
        Binding("d", "download_model", "Download"),
        Binding("n", "new_recipe", "New"),
        Binding("e", "edit_recipe", "Edit"),
        Binding("x", "delete_recipe", "Delete"),
        Binding("l", "toggle_logs", "Logs"),
        Binding("f", "filter_recipes", "Filter"),
        Binding("p", "select_profile", "Profile"),
        Binding("slash", "search", "Search"),
        Binding("t", "cycle_theme", "Theme"),
        Binding("question_mark", "show_help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        tui_cfg: TuiConfig,
        profile: str,
        url: str,
        token: str,
    ) -> None:
        super().__init__()
        self._tui_cfg = tui_cfg
        self._profile = profile
        self._client = McpClient(url, token)
        self._offline = False
        self._selected_recipe: str | None = None
        self._slugs_by_row: list[str] = []
        # download_id -> hf_id that is still in progress, for status column + log polling.
        self._active_downloads: dict[str, str] = {}
        # Rotates recipes/logs visibility via `l`: both -> logs only -> recipes only -> both.
        self._pane_mode: int = 0
        self.theme = _resolve_theme(tui_cfg.ui.theme)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Vertical(
            Horizontal(id="status-row"),
            Horizontal(
                DataTable(id="recipes-row"),
                Log(id="logs-row", auto_scroll=True),
                id="main-row",
            ),
        )
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#recipes-row", DataTable)
        table.cursor_type = "row"
        # 1-char status column followed by name + model.
        # Status legend: ● active, ⬇ downloading, blank otherwise.
        table.add_columns(" ", "name", "model")

        # First call also acts as the connectivity probe; OfflineError is caught
        # inside _safe_call and schedules the backoff reconnect.
        await self._populate_nodes()
        await self._refresh_status()
        await self._refresh_recipes()
        self.set_interval(self._tui_cfg.ui.refresh_interval_ms / 1000, self._refresh_status)
        self.set_interval(5.0, self._refresh_logs)
        self.set_interval(3.0, self._poll_downloads)

    async def on_unmount(self) -> None:
        # Per-call client holds no persistent resources; nothing to close.
        return None

    def _log_line(self, line: str) -> None:
        logs = self.query_one("#logs-row", Log)
        logs.write_line(line)

    async def _populate_nodes(self) -> None:
        row = self.query_one("#status-row", Horizontal)
        info = await self._safe_call("get_cluster_info")
        if info is None:
            return
        nodes = info.get("nodes") or []
        for node_name in nodes:
            await row.mount(NodeBox(node_name))

    async def _safe_call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        try:
            result = await self._client.call(tool, arguments)
            self._offline = False
            return result
        except OfflineError as exc:
            self._offline = True
            self._log_line(f"[offline] {tool}: {exc}")
            self.set_timer(2.0, self._schedule_reconnect)
            return None

    async def _refresh_status(self) -> None:
        if self._offline:
            return
        status = await self._safe_call("get_cluster_status")
        if status is None:
            return
        nodes = [status.get("head_node")] + (status.get("workers") or [])
        for box in self.query(NodeBox):
            match = next((n for n in nodes if n and n.get("name") == box.node_name), None)
            if match:
                box.update_from(match)

    async def _refresh_recipes(self) -> None:
        if self._offline:
            return
        recipes = await self._safe_call("list_recipes")
        if recipes is None:
            return
        table = self.query_one("#recipes-row", DataTable)
        table.clear()
        self._slugs_by_row.clear()
        downloading_hf_ids = set(self._active_downloads.values())
        for r in recipes:
            # Status glyph per row:
            #   ● active (running)
            #   ⬇ downloading model referenced by this recipe
            #   blank otherwise
            if r.get("is_active"):
                status = "●"
            elif r.get("model") in downloading_hf_ids:
                status = "⬇"
            else:
                status = " "
            slug = r.get("slug") or r["name"]
            self._slugs_by_row.append(slug)
            table.add_row(status, r["name"], r["model"])

    async def _refresh_logs(self) -> None:
        if self._offline or not self._selected_recipe:
            return
        # Fetch head-node logs as a snapshot (streaming deferred to v0.2).
        logs = await self._safe_call(
            "tail_logs",
            {"node": "localhost", "lines": self._tui_cfg.ui.log_tail_lines},
        )
        if isinstance(logs, str):
            panel = self.query_one("#logs-row", Log)
            panel.clear()
            for line in logs.splitlines()[-self._tui_cfg.ui.log_tail_lines :]:
                panel.write_line(line)

    def _schedule_reconnect(self) -> None:
        async def _reconnect(delay: float = 2.0) -> None:
            while self._offline and delay <= 30.0:
                await asyncio.sleep(delay)
                # Per-call client: a successful health_check is the probe.
                try:
                    await self._client.call("health_check")
                    self._offline = False
                    self._log_line("[online] reconnected")
                    await self._refresh_status()
                    await self._refresh_recipes()
                    return
                except OfflineError as exc:
                    self._log_line(f"[offline] retry in {delay}s: {exc}")
                    delay = min(delay * 2, 30.0)

        self._reconnect_task = asyncio.create_task(_reconnect())

    # ---- Actions ----

    def _current_recipe_slug(self) -> str | None:
        """Return the filesystem-safe slug of the selected recipe, suitable
        for every MCP tool argument (see RecipeSummary.slug)."""
        table = self.query_one("#recipes-row", DataTable)
        row_index = table.cursor_row
        if row_index < 0 or row_index >= len(getattr(self, "_slugs_by_row", [])):
            return None
        return self._slugs_by_row[row_index]

    async def action_start_recipe(self) -> None:
        slug = self._current_recipe_slug()
        if not slug:
            return
        self._selected_recipe = slug
        result = await self._safe_call("launch_recipe", {"recipe_name": slug})
        self._log_line(f"[launch] {slug}: {result}")

    async def on_data_table_row_selected(self, event: Any) -> None:
        """DataTable consumes `enter`; route row-select into start_recipe."""
        await self.action_start_recipe()

    async def action_stop_cluster(self) -> None:
        result = await self._safe_call("stop_cluster")
        self._log_line(f"[stop] {result}")
        await self._refresh_recipes()

    async def action_restart_cluster(self) -> None:
        result = await self._safe_call("restart_cluster")
        self._log_line(f"[restart] {result}")

    async def action_download_model(self) -> None:
        slug = self._current_recipe_slug()
        if not slug:
            return
        recipe = await self._safe_call("get_recipe", {"name": slug})
        if not isinstance(recipe, dict):
            self._log_line(f"[download] get_recipe failed: {recipe}")
            return
        hf_id = recipe.get("model")
        result = await self._safe_call("download_model", {"hf_id": hf_id})
        if isinstance(result, dict) and result.get("download_id"):
            self._active_downloads[result["download_id"]] = hf_id or ""
            self._log_line(f"[download] started {hf_id} (id={result['download_id']})")
            await self._refresh_recipes()
        else:
            self._log_line(f"[download] {hf_id}: {result}")

    async def _poll_downloads(self) -> None:
        """Periodically check every active download for a progress update; log
        percentage + byte counts; purge completed ones from the in-flight map."""
        if self._offline or not self._active_downloads:
            return
        finished: list[str] = []
        for download_id, hf_id in list(self._active_downloads.items()):
            progress = await self._safe_call(
                "get_download_progress", {"download_id": download_id}
            )
            if not isinstance(progress, dict):
                continue
            status = progress.get("status")
            pct = progress.get("percent")
            text = progress.get("progress_text")
            bytes_done = progress.get("bytes_transferred") or 0
            if status == "in_progress":
                if pct is not None:
                    gb = bytes_done / 1e9
                    self._log_line(f"[download] {hf_id}: {pct:.1f}% ({gb:.2f} GB)")
                elif text:
                    self._log_line(f"[download] {hf_id}: {text[:140]}")
            else:
                # completed / failed / cancelled / not found
                err = progress.get("error")
                suffix = f" ({err})" if err else ""
                self._log_line(f"[download] {hf_id}: {status}{suffix}")
                finished.append(download_id)
        for download_id in finished:
            self._active_downloads.pop(download_id, None)
        if finished:
            await self._refresh_recipes()

    async def action_delete_recipe(self) -> None:
        slug = self._current_recipe_slug()
        if not slug:
            return
        result = await self._safe_call("delete_recipe", {"name": slug})
        self._log_line(f"[delete] {slug}: {result}")
        await self._refresh_recipes()

    def action_toggle_logs(self) -> None:
        """Cycle: recipes + logs (default) -> logs only -> recipes only -> both."""
        self._pane_mode = (self._pane_mode + 1) % 3
        recipes = self.query_one("#recipes-row", DataTable)
        logs = self.query_one("#logs-row", Log)
        if self._pane_mode == 0:  # both visible
            recipes.set_class(False, "hidden")
            logs.set_class(False, "hidden")
        elif self._pane_mode == 1:  # logs only (full width)
            recipes.set_class(True, "hidden")
            logs.set_class(False, "hidden")
        else:  # recipes only
            recipes.set_class(False, "hidden")
            logs.set_class(True, "hidden")

    def action_cycle_theme(self) -> None:
        current = self.theme if self.theme in THEMES else THEMES[0]
        idx = THEMES.index(current)
        self.theme = THEMES[(idx + 1) % len(THEMES)]

    async def action_show_help(self) -> None:
        await self.push_screen(HelpModal())

    # Stubs for bindings we accept but defer full UI to v0.2.
    async def action_new_recipe(self) -> None:
        self._log_line("[info] new-recipe wizard deferred to v0.2")

    async def action_edit_recipe(self) -> None:
        self._log_line("[info] edit-recipe modal deferred to v0.2")

    async def action_filter_recipes(self) -> None:
        self._log_line("[info] filter-recipes modal deferred to v0.2")

    async def action_select_profile(self) -> None:
        self._log_line("[info] profile-selector modal deferred to v0.2")

    async def action_search(self) -> None:
        self._log_line("[info] search deferred to v0.2")


def run() -> int:
    parser = argparse.ArgumentParser(prog="spark-tui")
    parser.add_argument("--profile", default=None)
    ns = parser.parse_args()
    try:
        cfg, profile, token = load_tui_config(profile=ns.profile)
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    url = cfg.profiles[profile].mcp_url
    SparkTui(tui_cfg=cfg, profile=profile, url=url, token=token).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
