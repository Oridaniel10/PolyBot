import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from polymarket_client import PolymarketClient, PolymarketConfig  # noqa: E402
from py_clob_client_v2 import AssetType, BalanceAllowanceParams  # noqa: E402


TEST_TIMEZONE = "Asia/Jerusalem"


def build_target_day_label() -> str:
    now = (
        datetime.utcnow() if ZoneInfo is None else datetime.now(ZoneInfo(TEST_TIMEZONE))
    )
    return f"{now.strftime('%B')} {now.day}"


def load_env_file(path: Path) -> None:
    load_dotenv(path, override=True)


def validate_private_auth(client: PolymarketClient) -> None:
    config = client.config
    if not (
        config.api_key
        and config.api_secret
        and config.api_passphrase
        and config.private_key
    ):
        print(
            "private auth validation: skipped (missing key/secret/passphrase/private_key)"
        )
        return

    try:
        clob_client = client.make_clob_client()
        print("bot signer address:", clob_client.get_address())
        print("bot proxy funder:", (config.proxy_address or "").strip() or "(not set)")
        print(
            "clob signature_type (0=EOA, 1=POLY_PROXY):",
            client.resolve_clob_signature_type(),
        )
        print("clob collateral token:", clob_client.get_collateral_address())
        print("clob conditional token:", clob_client.get_conditional_address())
        response = clob_client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        print("private auth validation: OK")
        print("clob balance allowance snapshot:", response)
        if isinstance(response, dict):
            raw_balance = int(response.get("balance") or 0)
            print("clob collateral balance raw:", raw_balance)
            print(f"clob collateral balance (USDC ~6dp): {raw_balance / 1_000_000:.6f}")
            print("clob allowances:", response.get("allowances"))
    except Exception as error:
        print("private auth validation: FAILED")
        print("private auth error:", str(error))


def main() -> None:
    load_env_file(PROJECT_ROOT / ".env")
    sig_env = os.getenv("POLY_SIGNATURE_TYPE", "").strip()
    clob_sig = int(sig_env) if sig_env.isdigit() else None
    config = PolymarketConfig(
        api_key=os.getenv("API_KEY", os.getenv("POLY_API_KEY", "")),
        api_secret=os.getenv("API_SECRET", ""),
        api_passphrase=os.getenv("API_PASSPHRASE", ""),
        private_key=os.getenv("POLY_PRIVATE_KEY", os.getenv("Wallet_PRIVATE_KEY", "")),
        proxy_address=os.getenv("POLY_PROXY_ADDRESS", os.getenv("POLY_ADDRESS", "")),
        gamma_base_url=os.getenv(
            "POLY_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"
        ),
        clob_base_url=os.getenv("POLY_CLOB_BASE_URL", "https://clob.polymarket.com"),
        clob_signature_type=clob_sig,
    )
    client = PolymarketClient(config)
    validate_private_auth(client)
    print("private auth configured:", client.has_private_auth())

    all_markets = client.get_markets(limit=500)
    print("all active markets:", len(all_markets))

    markets = client.get_daily_temperature_markets()
    print("daily temperature markets:", len(markets))
    if markets:
        print("sample market id:", markets[0].get("id"))
        print(
            "sample market title:",
            markets[0].get("question") or markets[0].get("title"),
        )
    else:
        print("no temperature markets found in current active list")
        if all_markets:
            print(
                "sample active market:",
                all_markets[0].get("question") or all_markets[0].get("title"),
            )

    target_day_label = build_target_day_label()
    highest_markets = client.get_highest_temperature_markets(
        target_day_label=target_day_label
    )
    print(f"highest temperature markets ({target_day_label}):", len(highest_markets))
    for market in highest_markets[:5]:
        print("-", market.get("id"), market.get("question") or market.get("title"))

    positions = client.get_open_positions()
    print("open positions:", len(positions))
    if not positions:
        print("note: positions may be empty when API auth headers are incomplete")

    balance = client.get_portfolio_balance()
    print("portfolio balance snapshot:", balance)
    if isinstance(balance, dict) and balance.get("auth_warning"):
        print("auth warning:", balance["auth_warning"])


if __name__ == "__main__":
    main()
