import os
from pathlib import Path

from dotenv import load_dotenv
from py_clob_client_v2 import ClobClient


def generate_keys():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    private_key = os.getenv("POLY_PRIVATE_KEY", "").strip()
    if not private_key:
        print("שגיאה: POLY_PRIVATE_KEY חסר במשתני הסביבה")
        return

    host = "https://clob.polymarket.com"
    chain_id = 137  # polygon mainnet

    client = ClobClient(host, chain_id=chain_id, key=private_key)

    print("--- מתחיל תהליך יצירת מפתחות CLOB (v2) ---")
    print(f"bot is using address: {client.get_address()}")
    print(
        "tip: deposit wallet (new accounts): POLY_PROXY_ADDRESS=<deposit>, POLY_SIGNATURE_TYPE=3"
    )
    try:
        api_creds = client.create_or_derive_api_key()

        print("\nשמור את הנתונים הבאים בקובץ ה-.env שלך:")
        print(f"API_KEY={api_creds.api_key}")
        print(f"API_SECRET={api_creds.api_secret}")
        print(f"API_PASSPHRASE={api_creds.api_passphrase}")

    except Exception as e:
        print(f"שגיאה: {e}")
        print("טיפ: וודא שיש לך מעט MATIC בארנק לביצוע הפעולה (למרות שלרוב זה בחינם)")


if __name__ == "__main__":
    generate_keys()
