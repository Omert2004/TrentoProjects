# Alternating Scroll Direction Check 01

## Outcome

The alternating capture is valid and provides modest evidence that the radar
contains left-versus-right information at near range. The best interpretable
signed-Doppler score produced an AUC of 0.72, compared with 0.50 for chance.
However, 20 events from one recording are not enough to declare direction
recognition reliable.

No further capture is required tonight. Preserve this recording and repeat the
alternating check in later seated sessions before starting the full dataset.

## Integrity

| Check | Result |
|---|---:|
| Captures | 1/1 |
| Alternating events | 20/20 |
| Left events | 10 |
| Right events | 10 |
| Metadata SHA-256 | MATCH |
| Transport validation | PASS |
| CRC/packet/sample/drop errors | 0 |
| Observed receive rate | 1993.69 samples/s |

## Difference scaling

| Q15 shift | Clipped samples | Percent |
|---:|---:|---:|
| 3 | 0 | 0.000% |
| **4** | **0** | **0.000%** |
| 5 | 3,272 | 1.609% |
| 6 | 39,469 | 19.408% |

Shift 4 is confirmed again.

## Motion response

Eighteen of 20 events increased 20–250 Hz energy relative to the stationary
interval immediately before the event. Near-range scrolling motion is therefore
present consistently in this capture.

## Direction response

The positive-versus-negative Doppler gain score had these results:

| Metric | Result |
|---|---:|
| Left median | -0.038 dB |
| Right median | +0.011 dB |
| Orientation-free AUC | 0.720 |
| Events matching the expected score sign | 13/20 |
| Consecutive pairs with the expected right-minus-left ordering | 7/10 |
| Two-sided rank-test p-value | 0.104 |

The median signs are physically consistent with two directions and the AUC is
better than the earlier separate-block pilot. The p-value and pair consistency
show that the evidence is still preliminary rather than conclusive.

## Next collection decision

Keep `directioncheck01` as pilot/quality-control data, not as an independent
train or test session. Before recording the complete seated dataset:

1. repeat the alternating near-range check in at least two later sessions;
2. reverse which direction occurs first in one repeat, preventing odd/even event
   order from becoming a direction cue;
3. keep the dashboard radar position, chair, and operating height fixed;
4. continue using Q15 shift 4 for derived tensors.

If the repeated checks preserve the same signed-Doppler ordering, proceed with
the seated near-range training collection. If they do not, adjust radar position
or gesture geometry before gathering the full dataset.

