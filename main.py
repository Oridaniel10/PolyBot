"""
Thin entry: trading logic lives in strategy/, config in config/, state in state/.
"""

from strategy.bot_runner import build_config, main, run_bot

__all__ = ["build_config", "main", "run_bot"]


if __name__ == "__main__":
    main()
