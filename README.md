# VigiCausality: A Pharmacovigilance Eval for Multi-Drug ADR Causality Assessment

**Live application:** https://vigicausality-who-umc-naranjo-pv-causality-assess.ai.studio

## Run it (one line)

```bash
bash run_eval.sh
```

Requires only `python3` (3.9+). Fully offline: the runner creates `venv/`, installs `pydantic>=2.0`, runs `src/grader.py` over `dataset/tasks.json`, and writes `results/report.json`. No API keys, no network calls at evaluation time.

## Domain

Pharmacovigilance (PV) is the discipline of detecting, assessing and preventing adverse drug reactions. Every individual case safety report must receive a **causality assessment**: how likely is it that the drug caused the event? Two standards dominate practice:

- **WHO-UMC** categories: Certain, Probable/Likely, Possible, Unlikely, Conditional/Unclassified, Unassessable/Unclassifiable.
- **Naranjo algorithm**: a 10-question score mapped to Definite (9+), Probable (5-8), Possible (1-4), Doubtful (0 or less).

As a PV data engineer, I treat these as structured reasoning tasks: map the event to a MedDRA Preferred Term (PT), reconcile the full regimen, check temporal plausibility and dechallenge, and test for pharmacokinetic interactions before assigning a category.

## The performance cliff

Frontier models summarise case narratives fluently, but on every supplied case the system under test collapsed to the same fallback: an unspecified event term, a "Conditional / Unclassified" WHO-UMC label and a default Naranjo score of 1. The collapse is most dangerous where the regimen has several drugs and the cause is an **interaction**, because the model also fails to name the clearance or additive-risk path:

| Case | Regimen | What the model must find |
|---|---|---|
| FIC-2024-1001 | Warfarin + Amiodarone | CYP2C9 inhibition raises S-warfarin exposure; INR 7.8 with haematuria |
| FIC-2024-1002 | Metformin + Enalapril + Furosemide | Renal impairment and ACE inhibitor raise lactic acidosis risk |
| FIC-2024-1003 | Amoxicillin (single drug) | Anaphylaxis with tryptase 28 ug/L; mapped to a concrete PT, not "Unspecified" |
| FIC-2024-1004 | Sertraline + Aspirin | Additive SSRI and antiplatelet bleeding risk; GI haemorrhage with Hb 8.2 |

Case 1003 is a single-drug case and failed identically, so the failure is not confined to polypharmacy: it is a uniform loss of specificity that is hardest to detect, and most harmful, when an interaction is the real cause. The same pattern is expected for regimens such as Simvastatin + Amlodipine (CYP3A4 inhibition), which are not in the dataset.

## Results

On the four supplied cases the system under test scored 0 of 4 (0% accuracy, 100% failure rate): 4 Discordant labels, 4 Unspecified Adverse Event terms, 4 lazy Conditional / Unclassified labels, 4 WHO-UMC mismatches and 2 Naranjo category mismatches. Three of the four are multi-drug regimens, and all three failed. `results/report.json` also lists six authored single-drug controls separately under `by_provenance`; they show that the grader passes correct assessments and are not part of the system's measured accuracy.

## Limitations

- The benchmark has four supplied cases, so rates are descriptive, not statistically powered.
- The four model outputs are identical, which is consistent with a templated fallback, not case-by-case reasoning.
- The six controls are authored, not captured from a model run.

Observed failure modes:

1. **Lazy classification**: defaulting to "Conditional/Unclassified" when the data support Probable/Likely.
2. **Unspecified Adverse Event**: failing to map a concrete PT (e.g. *International normalised ratio increased*, *Rhabdomyolysis*).
3. **Skewed Naranjo metrics**: collapsing specific clinical evidence into a vague numeric "Possible".
4. **Missed interaction paths**: no reasoning about clearance inhibition from the concomitant drug.

These errors look safe but are not: they mask life-threatening data deficiencies and under-prioritise serious cases in triage.

## How grading works

`src/grader.py` validates the dataset with pydantic, then flags a case as **failed** if the model output is labelled `Discordant` or its event term is exactly `Unspecified Adverse Event`. Accuracy and failure rates are computed over all cases. Secondary diagnostics are logged per case: lazy unclassified label, WHO-UMC and Naranjo category mismatch against `ground_truth_causality`, and multi-drug regimen failures. The report is written atomically (temp file, fsync, `os.replace`) to `results/report.json`.

## Layout

```
README.md
requirements.txt
run_eval.sh
dataset/tasks.json   # clinical_profile (with ground truth) + ai_predicted_output per case
src/grader.py        # standalone grader
```

`results/` is generated on first run.

## Dataset note

`dataset/tasks.json` stores narratives verbatim, including line breaks. Each case holds `clinical_profile` (patient metrics, regimen, reactions, labs, narrative, ground-truth causality) and the frontier model's `ai_predicted_output`, merged without modification from the source records.

Cases `FIC-2024-1001` to `1004` are the supplied records. Cases `FIC-2024-1006` to `1011` carry `"provenance": "synthetic_control"`: they are authored single-drug cases with concordant outputs, included to show the grader passes correct assessments. They were not captured from a live model run, and `results/report.json` reports them separately under `by_provenance`.
