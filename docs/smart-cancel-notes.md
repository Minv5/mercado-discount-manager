# SMART started cancel notes

Date: 2026-07-05

Scope: only SMART started cancel research and tool support. This note does not release SMART bulk cancel by default.

## Official evidence

Mercado official PIX/BANK co-funded documentation exposes the offer-id pattern:

- Campaign item details include `offer_id`.
- Removing a pending or active offer uses:

```text
DELETE /seller-promotions/items/{ITEM_ID}?promotion_type=BANK&promotion_id={PROMOTION_ID}&offer_id={OFFER_ID}&app_version=v2
```

This is official evidence for the offer-id cancel shape, but it is BANK/PIX documentation, not SMART-specific official documentation.

## Local SMART field evidence

Read-only AppData cache for account `2651442567` shows all remaining SMART started rows have started `OFFER-...` offer IDs:

| promotion_id | status | count | rows with offer_id |
| --- | --- | ---: | ---: |
| P-MLM17755468 | started | 390 | 390 |
| P-MLB17755282 | started | 6 | 6 |
| P-MLB17757148 | started | 2 | 2 |
| P-MLM17765040 | started | 1 | 1 |
| P-MLM17765042 | started | 1 | 1 |
| P-MLM17767046 | started | 1 | 1 |

Example started row fields: `id`, `status`, `price`, `original_price`, `offer_id`, `seller_percentage`, `meli_percentage`, `start_date`, `end_date`.

## Program behavior

- Default SMART cancel behavior remains blocked/skipped and does not send Mercado write requests.
- `/api/smart-cancel/remaining` supports GET and POST. It defaults to `live=true` and reads the current platform `started` state. Use `source=local` or `live=false` only for cache diagnostics.
- `/api/smart-cancel/preview` builds a single-item DELETE request preview from cached started `offer_id`.
- `/api/smart-cancel/detail` defaults to live read-only checks, returns local field evidence, request path hypotheses, `local_cache_stale`, and live `started_contains_item`.
- When live SMART `started` fetch succeeds, the app refreshes the local `started` cache for that promotion, so stale cached items are not counted as final remaining.
- Execution can only send SMART cancel when the caller explicitly passes `allowSmartCancel=true`.
- The default SMART cancel sample limit is 1 item. Use `smartCancelMaxItems` for controlled small-sample validation.
- SMART cancel recheck is read-only and does not auto-retry remaining started items.

## 2026-07-05 sample outcome

Real test thread ran the first 1-item sample:

- Account: `2651442567`
- Promotion: `P-MLB17757148`
- Item: `MLB6927588934`
- Offer: `OFFER-MLB6927588934-13231665526`
- Adapter path sent DELETE and local audit showed `sent_to_api=true`.
- Read-only recheck after 20 seconds and 60 seconds still found the item in `started`; SMART total remained 401 and `P-MLB17757148` remained 2.

Conclusion: the current offer-id DELETE adapter is not sufficient evidence for bulk SMART cancel. It may be an accepted no-op, an endpoint/path mismatch, or missing SMART-specific context. Do not bulk-clear the remaining 401 based on this path.

## Next validation

Use `/api/smart-cancel/detail` and the stored DELETE response summary to identify the next 1-item hypothesis. Any further SMART write validation must remain a single-item real test until a read-only recheck proves that the item leaves `started`.

For live remaining checks, test threads should call:

```text
POST /api/smart-cancel/remaining
{
  "accountId": "2651442567",
  "promotionId": "P-MLB17757148",
  "itemId": "MLB6927588934",
  "live": true
}
```

The response includes `source`, `total_started`, `total_local_started`, `total_stale_removed`, `groups[].target_item_remaining`, and `groups[].cache_updated_from_live`.
