# Validation of initial implementation

Dataset: four institution folders at repository base `6d5b987d`.
The generated manifest records exact source, code and artifact hashes for each run.

## Data preparation

- 4,000 case bundles; 6,467 alerts; 209,212 transactions.
- 16,402 documents; original bytes verified against every source file.
- 242,209 original JSONL records verified against their exported `raw_json` values.
- 79 feature columns plus `case_key`; 21 output artifacts.
- Train 2,800 / validation 600 / test 600: 700/150/150 per institution.
- Known customer/account grouping verified; current data's largest component is one case.
  Synthetic tests exercise multi-case, transitive and cross-institution groups.
- 141 date warnings: opening timestamp precedes supplied review-window end. Records retained.
- Two fresh full-dataset runs, including different hash seed/timezone settings: all 21 files
  byte-identical, including the manifest.

Commands (use fresh output directories):

```sh
python3 preprocessing/prepare.py --input hackathon/institutions --output /tmp/prepared-a
PYTHONHASHSEED=42 TZ=Pacific/Honolulu python3 preprocessing/prepare.py \
  --input hackathon/institutions --output /tmp/prepared-b
python3 -m unittest discover -s tests -p test_preprocess.py -v
make gate
```

Fifteen dedicated tests pass. The repository test suite has 22 passing tests.

## Existing inference engine regression comparison

No submission-engine, evaluation or gold-label files are modified. Baseline and branch give:

| Institution | Verdict score, before / after | Wrongful closes, before / after | Hygiene, before / after |
|---|---|---|---|
| bank | 32.2 / 32.2 | 0 / 0 | 0 / 0 |
| emoney | 37.7 / 37.7 | 0 / 0 | 0 / 0 |
| investment | 45.1 / 45.1 | 0 / 0 | 0 / 0 |
| payments_fx | 32.1 / 32.1 | 0 / 0 | 0 / 0 |

These evaluation figures use the repository's provisional gold labels, not established truth.
Mutation trap catch remains 68.1% (633/930), false alarms 0% (0/429).
The existing trap catch rate is below the repository's 100% aspiration; this data-preparation
change does not alter that engine. Runtime determinism checks pass for all four institutions.

## Limits

This is preparation through splitting, not evidence interpretation or training. No current-case
labels are inferred or imported. Higher-level rule measurements remain available as source
`measured` values; complete rule reconstruction is outside this initial feature set.
Known account/customer isolation does not establish independence of synthetic scenario templates.
