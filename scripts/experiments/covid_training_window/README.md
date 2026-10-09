# Does older training data skew current valuations?

This experiment trains the **same CatBoost quantile model** three times. Only the
first eligible training month changes: January 2017, January 2020, or January
2023. All runs use the same features, earlier-sale comparable prices, validation
sales (October 2024–September 2025), and test sales (October 2025–September
2026). The test set is never used for fitting or early stopping.

Run from the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe .\scripts\experiments\covid_training_window\compare_training_windows.py
```

Results are saved under `reports/experiments/covid_training_window/`:

- `training_window_comparison.csv`: overall validation and test scores.
- `sale_level_predictions.csv`: the three predictions for each evaluation sale.
- `paired_breakdowns.csv`: like-for-like comparisons by month, town, and the
  highest-priced 10% versus other sales. Positive MAE improvement means the
  shorter training window was more accurate. Groups with fewer than 100 sales
  are marked as limited evidence.

Compare **test MAE** first, then bias and validation MAE. Lower MAE is better;
bias near zero means less systematic overpricing or underpricing. The experiment
does not overwrite the released model, database, or dashboard. The January 2020
cutoff probes pre-COVID rows; January 2023 probes the effect of excluding most
COVID-era rows. These are practical cutoffs, not exact pandemic boundaries.

Earlier comparable prices for training rows are calculated once from the full
historical sequence and then held fixed across runs. This isolates the effect of
which rows the ML model learns from. The baseline itself may still use older
sales when recent comparables are scarce. The existing test period has informed
project development, so use later untouched transactions to confirm any winner
before changing the released model.

## Initial run (9 October 2026)

| Training sales | Validation MAE | Test MAE | Test mean bias |
| --- | ---: | ---: | ---: |
| 2017 onward (189,572) | S$30,520 | S$33,467 | +S$7,787 |
| 2020 onward (126,543) | S$30,291 | S$33,183 | +S$7,314 |
| 2023 onward (47,405) | S$29,955 | S$32,512 | +S$5,515 |

The 2023-onward version had S$955 lower test MAE than the full-history version
on the same 24,310 test sales, about 2.9% lower. This supports the concern that
older ML training rows may be reducing current accuracy, but does not prove the
cutoff will win on future sales or in every town. No released model was changed.

## Paired sale-level check

On the October 2025–September 2026 test period, the 2023-onward version had
lower MAE in **all 12 months** and **22 of 26 towns**. Its individual prediction
was closer for 12,763 sales; the full-history version was closer for 11,547.
Thus, the average improvement comes partly from reducing larger misses, not
from winning every sale.

Four towns had higher MAE with 2023-onward training: Jurong East (+S$841),
Marine Parade (+S$210), Woodlands (+S$141), and Choa Chu Kang (+S$122).
These are increases in error per sale, not price changes. Review their rows in
`paired_breakdowns.csv` before adopting the shorter window nationally.

The highest-priced roughly 10% of test sales improved by S$2,568 MAE per sale.
The other roughly 90% also improved, by S$768 per sale, and contributed about
72% of the total reduction in absolute error. The gain is therefore not solely
due to a small group of expensive flats.

These checks strengthen the evidence for a shorter training window **on this
already-inspected test period**. They do not establish performance on future
sales; a later untouched period is needed before changing the released method.
