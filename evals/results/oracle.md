# Eval: `oracle` extractor

| metric | value |
|---|---|
| invoices | 17 |
| decision accuracy | 100% |
| **unsafe approvals** (problem invoice marked ready) | **0** |
| needless flags (clean invoice sent to review/hold) | 0 |
| extraction field accuracy | 100% |
| avg seconds / invoice | 0.02 |

## Issue detection

| issue | caught | false alarm | missed |
|---|---|---|---|
| DUPLICATE_INVOICE | 1 | 0 | 0 |
| ITEM_NOT_ON_PO | 1 | 0 | 0 |
| MATH_ERROR | 1 | 0 | 0 |
| POSSIBLE_DUPLICATE | 1 | 0 | 0 |
| PO_NOT_FOUND | 1 | 0 | 0 |
| PRICE_VARIANCE | 3 | 0 | 0 |
| QTY_EXCEEDS_RECEIVED | 3 | 0 | 0 |
| UNREADABLE | 1 | 0 | 0 |

## Per invoice

| file | layout | expected | got | issues expected | issues found |
|---|---|---|---|---|---|
| 01_halvorsen_clean.pdf | A | approve | approve | — | — |
| 02_ridgeline_clean.pdf | B | approve | approve | — | — |
| 03_halvorsen_price_up.pdf | A | review | review | PRICE_VARIANCE | PRICE_VARIANCE |
| 04_halvorsen_short_delivery.pdf | A | review | review | QTY_EXCEEDS_RECEIVED | QTY_EXCEEDS_RECEIVED |
| 05_ridgeline_surcharge.pdf | B | review | review | ITEM_NOT_ON_PO | ITEM_NOT_ON_PO |
| 06_ridgeline_math_error.pdf | B | review | review | MATH_ERROR | MATH_ERROR |
| 07_halvorsen_resent_duplicate.pdf | A | hold | hold | DUPLICATE_INVOICE | DUPLICATE_INVOICE |
| 08_kowalski_no_po.pdf | C | hold | hold | PO_NOT_FOUND | PO_NOT_FOUND |
| 09_kowalski_clean.pdf | C | approve | approve | — | — |
| 10_kowalski_price_up.pdf | C | review | review | PRICE_VARIANCE | PRICE_VARIANCE |
| 11_halvorsen_partial_billing.pdf | A | approve | approve | — | — |
| 12_ridgeline_name_variant.pdf | B | approve | approve | — | — |
| 13_halvorsen_missing_sku.pdf | A | approve | approve | — | — |
| 14_halvorsen_two_problems.pdf | A | review | review | PRICE_VARIANCE, QTY_EXCEEDS_RECEIVED | PRICE_VARIANCE, QTY_EXCEEDS_RECEIVED |
| 15_ridgeline_price_down.pdf | B | approve | approve | — | — |
| 16_halvorsen_reissued.pdf | A | hold | hold | POSSIBLE_DUPLICATE, QTY_EXCEEDS_RECEIVED | POSSIBLE_DUPLICATE, QTY_EXCEEDS_RECEIVED |
| 17_scanned_fax.pdf | SCAN | review | review | UNREADABLE | UNREADABLE |
