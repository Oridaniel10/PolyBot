Fees

Copy page

Understanding trading fees on Polymarket

Polymarket charges a small taker fee on certain markets. These fees fund the Maker Rebates Program, which redistributes fees daily to market makers to incentivize deeper liquidity and tighter spreads.
Geopolitical and world events markets are fee-free. Polymarket does not charge fees or profit from trading activity on these markets. There are also no Polymarket fees to deposit or withdraw USDC (though intermediaries like Coinbase or MoonPay may charge their own fees).
Fees apply only to markets deployed on or after the activation date. Pre-existing markets are unaffected. Markets with fees enabled have feesEnabled set to true on the market object.
​
Fee Structure
Fees are calculated using the following formula:
fee = C × feeRate × p × (1 - p)
Where C = number of shares traded and p = price of the shares.
Makers are never charged fees. Only takers pay fees. The fee parameters differ by market category:
Category	Taker Fee Rate	Maker Fee Rate	Maker Rebate
Crypto	0.072	0	20%
Sports	0.03	0	25%
Finance	0.04	0	25%
Politics	0.04	0	25%
Economics	0.05	0	25%
Culture	0.05	0	25%
Weather	0.05	0	25%
Other / General	0.05	0	25%
Mentions	0.04	0	25%
Tech	0.04	0	25%
Geopolitics	0	0	—
Taker fees are calculated in USDC and vary based on the share price. However, fees are collected in shares on buy orders and USDC on sell orders. The fee amount in USDC is symmetric around 50% probability — a trade at 30¢ incurs the same dollar fee as a trade at 70¢.

​
Fee Tables (100 Shares)
Crypto
Sports
Finance / Politics / Mentions / Tech
Economics / Culture / Weather / Other
Price	Trade Value	Taker Fee (USDC)
$0.01	$1	$0.07
$0.05	$5	$0.34
$0.10	$10	$0.65
$0.15	$15	$0.92
$0.20	$20	$1.15
$0.25	$25	$1.35
$0.30	$30	$1.51
$0.35	$35	$1.64
$0.40	$40	$1.73
$0.45	$45	$1.78
$0.50	$50	$1.80
$0.55	$55	$1.78
$0.60	$60	$1.73
$0.65	$65	$1.64
$0.70	$70	$1.51
$0.75	$75	$1.35
$0.80	$80	$1.15
$0.85	$85	$0.92
$0.90	$90	$0.65
$0.95	$95	$0.34
$0.99	$99	$0.07
The fee in USDC peaks at 50% probability ($1.80) and decreases symmetrically toward both extremes.
​
Fee Precision
Fees are rounded to 5 decimal places. The smallest fee charged is 0.00001 USDC. Anything smaller rounds to zero, so very small trades near the extremes may incur no fee at all.
​
Identifying Fee-Enabled Markets
Markets with fees have feesEnabled set to true on the market object. You can also query the fee-rate endpoint to check any specific market. See the API Reference for full endpoint documentation.
GET https://clob.polymarket.com/fee-rate?token_id={token_id}
​
Fee Handling for API Users
​
Using the SDK
The official CLOB clients automatically handle fees for you — they fetch the fee rate and include it in the signed order payload.
TypeScript
npm install @polymarket/clob-client@latest
Python
pip install —upgrade py-clob-client
Rust
cargo add polymarket-client-sdk
What the client does automatically:
Fetches the fee rate for the market’s token ID
Includes feeRateBps in the order structure
Signs the order with the fee rate included
You don’t need to do anything extra. Your orders will work on fee-enabled markets.
​
Using the REST API
If you’re calling the REST API directly or building your own order signing, you must manually include the fee rate in your signed order payload.
Step 1: Fetch the fee rate for the token ID before creating your order:
GET https://clob.polymarket.com/fee-rate?token_id={token_id}
See the fee-rate API Reference for full response details. Fee-enabled markets return a non-zero value; fee-free markets return 0.
Step 2: Add the feeRateBps field to your order object. This value is part of the signed payload — the CLOB validates your signature against it.
{
  "salt": "12345",
  "maker": "0x...",
  "signer": "0x...",
  "taker": "0x...",
  "tokenId": "71321045679252212594626385532706912750332728571942532289631379312455583992563",
  "makerAmount": "50000000",
  "takerAmount": "100000000",
  "expiration": "0",
  "nonce": "0",
  "feeRateBps": "1000",
  "side": "0",
  "signatureType": 2,
  "signature": "0x..."
}
Step 3: Sign and submit:
Include feeRateBps in the order object before signing
Sign the complete order
POST to the order endpoint
Always fetch fee_rate_bps dynamically — do not hardcode. The fee rate varies by market type and may change over time. You only need to pass feeRateBps.
​
Next Steps
Maker Rebates Program
Learn how taker fees fund daily USDC rebates for liquidity providers.
Place Orders
Start placing orders on Polymarket.
Was this page helpful?


Yes

No
Builder Methods
Previous