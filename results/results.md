20 seeds; each run has 2000 customers and 10000 orders, with 25 rows of each fault kind injected.

| fault kind | injected | rejected | rejected by exactly the expected rules |
|---|---:|---:|---:|
| null_key | 500 | 500 | 500 |
| duplicate_key | 500 | 500 | 500 |
| orphan_customer | 500 | 500 | 500 |
| bad_status | 500 | 500 | 500 |
| negative_amount | 500 | 500 | 500 |
| text_amount | 500 | 500 | 500 |
| ship_before_order | 500 | 500 | 500 |
| customer_null_region | 500 | 500 | 500 |
| customer_bad_signup | 500 | 500 | 500 |
| customer_duplicate | 500 | 500 | 500 |

Orders whose customer was rejected: 5001 in all, 251.0 per seed (range 214-288); 5001 were rejected by the foreign-key rule.
Expected rows not rejected: 0. Rejected by the wrong rules: 0. Rejected but not expected: 0.
Rows quarantined per seed: median 496.5 (range 463-533); bad orders in the input per seed: median 421.5 (range 388-458).
With the rules removed, orders loaded equalled orders staged in every seed: True.
Load and check time per seed: median 1.593 s (range 1.28-1.894 s).
