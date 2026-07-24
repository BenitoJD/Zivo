# Calibration Engine

**Status:** wired (v1). Online Elo owns ability + item difficulty updates from binary outcomes; optional dynamic K for cold-start; Fisher-based SE(θ) for evidence-stop consumers.  
**Owns the question:** given a graded answer, how do learner ability and item difficulty move, and how uncertain is θ̂?  
**Product home:** Question Better / Zivo grade path (`answer_signal`), Selection reads, Mastery SE stop, birth-difficulty priors.

Related: [ENGINES.md](ENGINES.md), [ADAPTIVE_SELECTION_ENGINE.md](ADAPTIVE_SELECTION_ENGINE.md), [ADR 0004](adr/0004-swappable-policy-seam.md), [DATA_MODEL.md](DATA_MODEL.md).

---

## 0. Relation to sibling engines

| Engine | Question | Output |
|--------|----------|--------|
| **Budget** | How many items? | `N_page` (unchanged by calibration) |
| **Quality** | Which drafts survive? | pass/fail (unchanged) |
| **Calibration** | How do ratings update from outcomes? | ability, difficulty, n, SE(θ) |
| **Selection** | Which item next? | consumes ability/difficulty |
| **Mastery / Evidence-Stop** | Enough precision / mastery? | consumes SE + concept ability |

```
grade: measurement insert → Calibration.update → progress mirror → Selection next turn
cook:  optional birth difficulty prior (cold-start) → same difficulty scale
```

---

## 1. Problem definition

### Input

| Field | Role |
|-------|------|
| `ability`, `difficulty` | Current ratings on shared logit scale |
| `correct` | Binary outcome |
| `ability_n`, `difficulty_n` | Observation counts (for dynamic K / SE) |
| Optional prior | Birth difficulty from item features |

### Output

```
CalibrationVerdict {
  ability: float
  difficulty: float
  expected: float          # P(correct | pre-update ratings)
  ability_n: int
  difficulty_n: int
  ability_se: float        # approx SE(θ) after update
  k_learner: float
  k_item: float
  policy: str
  policy_version: "qb.calibration.v1"
}
```

Reads Selection needs:

```
get_ability(entity) -> (rating, n, se)
get_difficulty(assertion) -> (rating, n)
```

---

## 2. What research says

### 2.1 Elo for educational ability + difficulty

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Pelánek, Elo in educational systems | 2016 | [CAE PDF](https://www.fi.muni.cz/~xpelanek/publications/CAE-elo.pdf) | Answer = learner–item match; simple, online, good enough for adaptive practice. |
| Pelánek et al., Elo adaptive practice | 2017 | [UMUAI PDF](https://www.fi.muni.cz/~xpelanek/publications/umuai-adaptive-practice.pdf) | Modular prior + current knowledge; Elo updates skill and difficulty. |
| Pankiewicz, Elo task difficulty | 2019 | [e-mentor](https://www.e-mentor.edu.pl/eng/article/index/number/82/id/1444) | Elo stable vs proportion-correct in learning environments with feedback. |

**Implication:** Keep symmetric Elo on a shared logit; learners move faster than items (`K_learner > K_item`).

### 2.2 Dynamic K / uncertainty (cold-start)

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| Klinkenberg / Brinkhuis line; dynamic K | 2011–2025 | [PMC12682724](https://pmc.ncbi.nlm.nih.gov/articles/PMC12682724/) | Larger K early, smaller later balances flexibility vs stability. |
| Pelánek uncertainty function | 2014+ | [decay-elo PDF](https://www.fi.muni.cz/~xpelanek/publications/decay-elo.pdf) | Replace fixed K with uncertainty that shrinks with evidence. |
| Cold-start difficulty priors | 2019–2021 | [Springer CBB](https://link.springer.com/article/10.1007/s42113-021-00101-6) | Data-driven / feature priors beat blank starts; outcomes remain authority. |

**Implication (v1):** `K(n) = K_base * (1 + boost/(1+n))` with boost for n&lt;10. Birth prior via `item_difficulty.estimate_birth_difficulty` stays a prior only (`n=0`).

### 2.3 SE of ability (IRT information)

| Source | Year | Link | Takeaway |
|--------|------|------|----------|
| IRT / CAT information identity | classic | Cogn-IQ / textbooks | `SE(θ) ≈ 1/√I(θ)`; 1PL item info = P(1-P). |
| Magis / bootstrap SE notes | 2014+ | [OSF](https://doi.org/10.31234/osf.io/697yj_v2) | Fisher SE is asymptotic; small-n is optimistic. |
| Weiss SE termination | 1984+ | JCAT practice | Stop when SE hits target (Mastery engine consumes this). |

**Implication:** Track running sum of Fisher info across outcomes for the learner; expose `ability_se`. Do not pretend small-n SE is exact.

---

## 3. Opinionated defaults (locked for v1)

1. Policy `elo_online_v1` (default). Future: `irt_2pl_batch` behind same seam.
2. `DEFAULT_RATING = 0.0`, `ELO_SCALE = 1.0`, `K_LEARNER = 0.20`, `K_ITEM = 0.10`.
3. Dynamic K on by default for both sides: `k = k_base * (1 + 2/(1+n))`.
4. `ability_se = 1/sqrt(max(info, ε))` with info accumulating `P(1-P)` each update.
5. Idempotent grade path: calibration runs only when measurement insert is new.
6. Calibration failure never fails the grade (measurement already written).
7. No LLM in the update math.

---

## 4. Policy seam (ADR 0004)

```
calibration_policy = "elo_online_v1"
```

```
from app.services.calibration_engine import (
    update_from_outcome, record_outcome, get_ability, get_difficulty, seed_item_difficulty
)
```

`calibration.py` re-exports for compatibility.

---

## 5. Algorithm

```
input: ability, difficulty, correct, ability_n, difficulty_n, running_info

1. k_l = dynamic_k(ability_n, K_LEARNER)
2. k_i = dynamic_k(difficulty_n, K_ITEM)
3. p = logistic(ability - difficulty)
4. ability' = ability + k_l * (outcome - p)
5. difficulty' = difficulty + k_i * (p - outcome)
6. info' = running_info + p*(1-p)
7. se = 1/sqrt(info')
8. persist projections (ability n+1, difficulty n+1, info on ability value jsonb)
```

---

## 6. Fit to Zivo

| Piece | Role |
|-------|------|
| `calibration.py` (legacy) | Elo math + projection I/O |
| `item_difficulty.py` | Birth prior features |
| `answer_signal.py` | Idempotent measurement + calibrate flag |
| `question_pool_jobs` | Mirror ability / per-concept Elo into progress |

This engine formalizes the seam, adds dynamic K + SE, and is what Selection / Mastery should read.

---

## 7. Failure modes

| Failure | Behavior |
|---------|----------|
| Unseeded vocab | Best-effort skip; grade succeeds |
| Replay answer | No second update |
| Extreme ratings | Finite floats; logistic saturates |
| Tiny n | Large SE; Mastery refuses early stop |

---

## 8. Implementation

| Module | Role |
|--------|------|
| `calibration_engine.py` | Pure update + SE + I/O facade |
| `calibration.py` | Compat re-exports |
| `answer_signal.py` | Calls engine `record_outcome` |
| Tests | `test_calibration_engine.py` + existing Elo tests |

Durable centerpiece: **online Elo on a shared logit with cold-start-aware K and Fisher SE; outcomes own truth; priors only start the clock.**
