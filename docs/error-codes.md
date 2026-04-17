# Error codes

Every `ErrorInfo.code` returned by an MCP tool.

| Code | Cause | Recovery |
|------|-------|----------|
| `RECIPE_NOT_FOUND` | Recipe file does not exist in `{vllm_docker.repo_path}/recipes/`. | List recipes via `list_recipes`. |
| `RECIPE_INVALID` | YAML parse error, schema mismatch, path-traversal attempt, or filename-slug mismatch with the YAML `name:` field (A25). | Run `validate_recipe(content)` locally first. |
| `RECIPE_EXISTS` | `create_recipe` called with a name that already has a file. | Use `update_recipe` or pick a different name. |
| `CLUSTER_BUSY` | `launch_recipe` called while another model is active and `max_concurrent_models=1`. | `stop_cluster` first. |
| `LAUNCH_FAILED` | `run-recipe.py` exited non-zero, or the override key set was rejected. | Read `details.stderr`; check the recipe's `command` template. |
| `SSH_FAILED` | asyncssh returned an error at connection or exec time. | Check `ssh-keyscan` populated `known_hosts`; verify SSH key perms. |
| `SSH_HOST_UNKNOWN` | Missing `known_hosts` entry for a worker. | `spark-mcp ssh-trust <worker>`. |
| `DOWNLOAD_NOT_FOUND` | `cancel_download` / `get_download_progress` with an unknown id. | Use the id returned by `download_model`. |
| `DOWNLOAD_FAILED` | `hf-download.sh` exited non-zero. | Inspect `stderr`; check HuggingFace connectivity and disk space. |

Error payloads always include a `hint` string when the recovery action
is specific. Operators should log `code` + `message` + `details` — those
three fields carry enough structure to drive alerting.
