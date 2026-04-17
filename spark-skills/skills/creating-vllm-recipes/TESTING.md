# Testing `creating-vllm-recipes`

## Automated template validation

Every YAML in `templates/` and `examples/` parses as a valid `Recipe` via
`RecipeStore.validate_text`. CI re-runs this check on every push.

## Manual acceptance

1. Launch `spark-mcp` on a reachable head node.
2. Register the MCP server in Claude Code:
   ```bash
   claude mcp add spark-mcp --transport http \
     http://spark-head.local:8765/mcp \
     --header "Authorization: Bearer $TOKEN"
   ```
3. Install the skills: `./spark-skills/install.sh`.
4. Prompt Claude Code: "Create a recipe for Qwen3-VL-235B with tool calling
   enabled and tensor_parallel=4."
5. Verify that Claude:
   - Calls `list_recipes` to sample the baseline.
   - Calls `get_cluster_info` to size correctly.
   - Produces YAML that passes `validate_recipe` before calling
     `create_recipe`.
6. Save the transcript to `testing-evidence/qwen3-vl.txt` for the release PR.

## Regression coverage

If any template changes, re-run step 4 and confirm the output still passes
`validate_recipe`.
