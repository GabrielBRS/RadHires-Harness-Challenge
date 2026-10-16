# Eval: `baseline` extractor

| metric | value |
|---|---|
| invoices | 17 |
| decision accuracy | 53% |
| **unsafe approvals** (problem invoice marked ready) | **0** |
| needless flags (clean invoice sent to review/hold) | 5 |
| extraction field accuracy | 62% |
| avg seconds / invoice | 0.03 |

## Issue detection

| issue | caught | false alarm | missed |
|---|---|---|---|
| DUPLICATE_INVOICE | 1 | 0 | 0 |
| EXTRACTION_FAILED | 0 | 3 | 0 |
| ITEM_NOT_ON_PO | 0 | 0 | 1 |
| MATH_ERROR | 1 | 1 | 0 |
| MISSING_FIELD | 0 | 5 | 0 |
| POSSIBLE_DUPLICATE | 1 | 0 | 0 |
| PO_NOT_FOUND | 0 | 5 | 1 |
| PRICE_VARIANCE | 2 | 0 | 1 |
| QTY_EXCEEDS_RECEIVED | 3 | 0 | 0 |
| UNREADABLE | 1 | 0 | 0 |

## Per invoice

| file | layout | expected | got | issues expected | issues found |
|---|---|---|---|---|---|
| 01_halvorsen_clean.pdf | A | approve | approve | — | — |
| 02_ridgeline_clean.pdf | B | approve | hold ✗ | — | MISSING_FIELD, PO_NOT_FOUND |
| 03_halvorsen_price_up.pdf | A | review | review | PRICE_VARIANCE | PRICE_VARIANCE |
| 04_halvorsen_short_delivery.pdf | A | review | review | QTY_EXCEEDS_RECEIVED | QTY_EXCEEDS_RECEIVED |
| 05_ridgeline_surcharge.pdf | B | review | hold ✗ | ITEM_NOT_ON_PO | MISSING_FIELD, PO_NOT_FOUND |
| 06_ridgeline_math_error.pdf | B | review | hold ✗ | MATH_ERROR | MATH_ERROR, MISSING_FIELD, PO_NOT_FOUND |
| 07_halvorsen_resent_duplicate.pdf | A | hold | hold | DUPLICATE_INVOICE | DUPLICATE_INVOICE |
| 08_kowalski_no_po.pdf | C | hold | review ✗ | PO_NOT_FOUND | EXTRACTION_FAILED |
| 09_kowalski_clean.pdf | C | approve | review ✗ | — | EXTRACTION_FAILED |
| 10_kowalski_price_up.pdf | C | review | review | PRICE_VARIANCE | EXTRACTION_FAILED |
| 11_halvorsen_partial_billing.pdf | A | approve | approve | — | — |
| 12_ridgeline_name_variant.pdf | B | approve | hold ✗ | — | MISSING_FIELD, PO_NOT_FOUND |
| 13_halvorsen_missing_sku.pdf | A | approve | review ✗ | — | MATH_ERROR |
| 14_halvorsen_two_problems.pdf | A | review | review | PRICE_VARIANCE, QTY_EXCEEDS_RECEIVED | PRICE_VARIANCE, QTY_EXCEEDS_RECEIVED |
| 15_ridgeline_price_down.pdf | B | approve | hold ✗ | — | MISSING_FIELD, PO_NOT_FOUND |
| 16_halvorsen_reissued.pdf | A | hold | hold | POSSIBLE_DUPLICATE, QTY_EXCEEDS_RECEIVED | POSSIBLE_DUPLICATE, QTY_EXCEEDS_RECEIVED |
| 17_scanned_fax.pdf | SCAN | review | review | UNREADABLE | UNREADABLE |
