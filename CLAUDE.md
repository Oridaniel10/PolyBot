# Claude Code Instructions - Weather Trading Bot

## Project Mission
- This is a specialized trading bot for Polymarket that trades based on maximum temperature forecasts.
- Core logic: Predicting and betting on weather outcomes.
- **Key Config Files**:
  - Runtime settings (prices, limits): `data/runtime_config.json`
  - Global constants: `config/constants.py`

## Mode: Autonomous & Efficient
- You have full permission to edit files, run shell commands, and debug.
- **Task Execution**: Work from start to finish. If a command fails, fix it immediately without asking.
- **Silence is Golden**: Do not provide progress updates. Only report back when the task is 100% complete and verified.

## Token Saving & Cost Control (CRITICAL)
- **Do not read irrelevant files**: Before reading a file, check if it's necessary for the specific task.
- **Minimal Context**: Use `/compact` frequently in long sessions to keep the context window small.
- **No unnecessary summaries**: Do not explain your thought process unless specifically asked.
- **Ignore heavy dirs**: Always respect `.claudeignore`.

## Technical Standards
- **Environment**: Always use the local `venv`. Run commands via `source venv/bin/activate`.
- **Debugging**: If the bot crashes, prioritize checking `data/runtime_config.json` for corruption or invalid values.
- **Safety**: Before making major changes to trading logic, create a backup of the original file.

## Final Report Format
Only when finished, provide:
1. Short list of files changed.
2. Result of the verification (e.g., "Bot started successfully").
3. One-sentence summary of the fix.
