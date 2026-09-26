# Evaluation results

19 labelled vignettes (see `eval/run_eval.py`). Under-triage is the unsafe error.

| Config | Exact accuracy | Under-triage | Over-triage | Questions asked | Median time |
|---|---|---|---|---|---|
| rules | 89% (17/19) | 2 | 0 | 5 | 0.0 s |
| gemma | 95% (18/19) | 0 | 1 | 4 | 31.4 s |

| Case | Expected | rules | gemma |
|---|---|---|---|
| child-cough-normal-rr (cough, RR normal for age) | GREEN | GREEN ✓ | GREEN ✓ |
| child-chest-indrawing (chest indrawing) | RED | YELLOW ⚠ under | RED ✓ |
| infant-not-breastfeeding (unable to feed + lethargic) | RED | RED ✓ | RED ✓ |
| young-infant-fever (fever in infant < 2 months) | RED | RED ✓ | RED ✓ |
| pregnancy-mild-htn (hypertension in pregnancy, no severe features) | YELLOW | YELLOW ✓ | YELLOW ✓ |
| pregnancy-bleeding (bleeding in pregnancy) | RED | RED ✓ | RED ✓ |
| adult-chest-pain-arm (possible heart attack) | RED | RED ✓ | RED ✓ |
| adult-mild-fever (uncomplicated fever) | GREEN | GREEN ✓ | GREEN ✓ |
| adult-mild-diarrhoea (mild diarrhoea) | GREEN | GREEN ✓ | GREEN ✓ |
| child-dehydration (some dehydration) | YELLOW | YELLOW ✓ | YELLOW ✓ |
| prolonged-fever (fever ≥ 7 days) | YELLOW | YELLOW ✓ | YELLOW ✓ |
| telugu-fever-headache (Telugu: fever + headache 2 days, eating and drinking) | GREEN | GREEN ✓ | GREEN ✓ |
| hinglish-convulsions (Hinglish: convulsions, not waking) | RED | RED ✓ | RED ✓ |
| snake-bite (snake bite) | RED | RED ✓ | RED ✓ |
| implausible-temp (bad reading re-measured normal; vague note clarified) | GREEN | GREEN ✓ | GREEN ✓ |
| self-harm (suicidal thoughts) | RED | RED ✓ | RED ✓ |
| chronic-cough-haemoptysis (suspected TB: needs clinic, not home care) | YELLOW | GREEN ⚠ under | RED ↑ over |
| child-poor-appetite (reduced appetite is not 'unable to feed') | GREEN | GREEN ✓ | GREEN ✓ |
| adult-hypoxia (SpO2 < 90%) | RED | RED ✓ | RED ✓ |
