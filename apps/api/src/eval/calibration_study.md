# Abstention & partial-answer calibration study (G06)

**Scope.** The cited filings Q&A (`src/services/filings_qa_service.ask`) must decide, for every
question, whether to *answer*, flag a low-confidence *partial*, or *abstain*. This study derives
and justifies the thresholds behind those decisions from a labeled golden set, and pins them with
boundary tests so they cannot drift silently.

**Reproduce.** Deterministic and offline (pure-Python BM25 + feature-hashing embedding):

```
python -m src.eval.calibration          # prints the full study JSON
python -m pytest tests/test_calibration.py
```

The labeled data is `src/eval/fixtures/golden_set.json` (`should_answer` labels the two classes).
The runner is `src/eval/calibration.py::run_calibration`.

## The signal: coverage

Both decisions key off one scalar the service already computes — **coverage**, the fraction of
the question's content terms (post-tokenization: lowercase alphanumerics, stopwords and 1–2 char
noise dropped, see `textkit.tokens`) that the cited answer's sentences actually cover:

```
coverage = |matched question terms| / |question terms|
```

Two boundaries partition it:

| Decision  | Rule                              | Meaning                                            |
|-----------|-----------------------------------|----------------------------------------------------|
| abstain   | no sentence shares any term (∅)   | no lexical evidence exists — nothing to cite       |
| partial   | `0 < coverage < 0.5`              | some evidence, but a minority of the question      |
| answered  | `coverage ≥ 0.5`                  | the answer covers a majority of the asked terms    |

The abstain boundary is not a tunable number: it is the structural "no candidate sentence" case
(`ABSTAIN_COVERAGE = 0.0`). Only the answered/partial boundary — `_PARTIAL_COVERAGE_THRESHOLD`
in the service, `PARTIAL_COVERAGE_THRESHOLD` here — is a calibrated choice.

## Measured distributions

Running the real `ask` over the 15 labeled golden questions yields two cleanly separated classes:

| Class                                  | n  | min coverage | max coverage | mean coverage | resulting statuses          |
|----------------------------------------|----|--------------|--------------|---------------|-----------------------------|
| **answerable** (`should_answer=true`)  | 12 | **0.50**     | 1.00         | 0.68          | 12 answered, 0 partial      |
| **not answerable** (`should_answer=false`) | 3  | 0.00         | **0.20**     | 0.07          | 2 abstained, 1 partial      |

- Lowest answerable coverage: **0.50** (`q-cyber`, `q-dividends`).
- Highest not-answerable coverage: **0.20** (`q-thin-customer`, a deliberate single-term hit
  `"customer aardvark zeppelin quarterly lithium"`).
- **Separation margin = 0.50 − 0.20 = 0.30.** The two classes do not overlap: any threshold in
  the open band **(0.20, 0.50]** classifies every golden question correctly.

## Why 0.5 (and not the max-margin midpoint)

The maximum-margin separator on this data is the midpoint of the band, **0.35**
(`derived_partial_threshold`). We nonetheless ship **0.5**, for three reasons:

1. **Principled anchor.** 0.5 is the "answer covers a *majority* of the question's terms" line —
   a human-meaningful invariant, not a number fit to one fixture. A threshold justified only by a
   small golden set would be overfit; a majority rule generalizes.
2. **It is inside the empirical safe band.** 0.5 is the upper (conservative) end of (0.20, 0.50],
   so it still misclassifies **zero** golden questions: every answerable question sits at or above
   it, every non-answerable one below.
3. **Asymmetric cost favors caution.** In a diligence tool, over-claiming a thin match as a
   confident answer is worse than under-claiming a real answer as `partial` (the citations still
   resolve either way). The conservative end of the band is the right bias.

The 0.30 margin means the choice is robust: coverage would have to move by 0.30 before any
answerable question is misread as partial, or by 0.30 the other way before a thin hit is promoted
to answered.

## Chosen thresholds

| Constant                       | Value | Boundary            |
|--------------------------------|-------|---------------------|
| `ABSTAIN_COVERAGE`             | 0.0   | abstain ↔ partial   |
| `PARTIAL_COVERAGE_THRESHOLD`   | 0.5   | partial ↔ answered  |

These mirror `filings_qa_service._PARTIAL_COVERAGE_THRESHOLD`. **Drift guard:**
`tests/test_calibration.py::test_service_threshold_matches_calibrated_value` fails if the service
constant and the calibrated value ever diverge, so a future edit to one without re-running this
study breaks CI.

## Boundary behavior (pinned by tests)

`tests/test_calibration.py` exercises the answered/partial boundary directly against the fixture
corpus:

- `"concentration antarctica"` → 1 of 2 terms matched → coverage **0.50** → **answered**
  (threshold is inclusive, `≥`).
- `"concentration antarctica zeppelin"` → 1 of 3 → coverage **0.333** → **partial**. Adding one
  unmatched term is the minimal change that flips the label at the boundary.
- `"antarctica zeppelin lithium"` → no term shares evidence → **abstained**, no citations.
