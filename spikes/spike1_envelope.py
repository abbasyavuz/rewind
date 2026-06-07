#!/usr/bin/env python3
"""Spike-1: divergence-envelope calibration (PROVISIONAL pilot on minimax/minimax-m3).

The hard, defensible question (spec/spikes/spike-1-envelope-calibration.md): for a
CLOSED API with no logprobs, in the discrete agent-action regime, can we separate
"your one edit caused this downstream change" from "this is just sampling noise"?

This harness measures it honestly on a discrete routing decision:
  * A/A baseline   — run each unedited prompt N times -> P0 distribution + noise floor.
  * identifiability — a prompt whose A/A modal frequency is below the gate is too
                      noisy to attribute -> auto-INDETERMINATE (the gate is the
                      pre-registered honesty mechanism, NOT a failure).
  * FPR — semantic-null edits (paraphrases that don't change routing): how often does
          the calibrated labeler wrongly flag the change as edit-caused?
  * TPR — true-effect edits (add a sentence that shifts the routing): how often is it
          correctly flagged? Reported per stratum, esp. the NEAR-BOUNDARY bin.
  * labeler — single post-edit sample o is flagged "edit-caused" if P0(o) <= tau
              (o is rare/absent under the unedited prompt). Sweep tau for the ROC.

DECISION (vs the RATIFIED thresholds in spec/pivot-thresholds.md):
  near-boundary TPR@5%FPR < X(0.70)  OR  auto-INDETERMINATE fraction > Y(0.40)  -> OSS-FIRST.

PROVISIONAL: no bitwise oracle yet, and this is minimax (a pilot proxy for Claude),
so this informs but does not by itself trigger the pivot (spec §4).

    python spikes/spike1_envelope.py            # default N=24 A/A, M=6 per edit
    python spikes/spike1_envelope.py 30 8 1.0    # N M temperature
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

MODEL = os.environ.get("OPENROUTER_MODEL", "minimax/minimax-m3")
BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

N_AA = int(sys.argv[1]) if len(sys.argv) > 1 else 24      # A/A repeats per base
M_EDIT = int(sys.argv[2]) if len(sys.argv) > 2 else 6      # repeats per edit
TEMP = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0    # sampling temperature
CONCURRENCY = 12

# pre-registered, ratified (spec/pivot-thresholds.md)
X_NEAR_TPR = 0.70
Y_MAX_INDET = 0.40
GATE_FLIP = 0.30      # base auto-INDETERMINATE if A/A flip-rate (1-modal_freq) > 0.30
NEAR_FLIP = 0.10      # near-boundary band: flip-rate in (0.10, 0.30]; clear: <= 0.10

CATS = ["billing", "technical", "account", "shipping", "sales", "other"]
SYSTEM = (
    "You are a support-ticket router. Read the ticket and reply with EXACTLY ONE word — the single "
    "best department — chosen from: billing, technical, account, shipping, sales, other. "
    "Reply with only that one lowercase word and nothing else."
)

# Hand-labeled dataset. `nulls` are paraphrases (routing unchanged -> FPR). `effects`
# append a sentence that pushes the routing to a different concern (-> TPR).
DATASET = [
    {
        "id": "charge_lock",
        "base": "I was charged twice this month and now my account is locked.",
        "nulls": [
            "There's a duplicate charge this month and I'm locked out of my account.",
            "I got billed two times this month and now I can't get into my account.",
            "Hi — two charges showed up this month and my account is locked, please help.",
        ],
        "effects": [
            "I was charged twice this month and now my account is locked. Forget the charges — every page of the app returns error 500.",
            "I was charged twice this month and now my account is locked. Please just refund the duplicate $20 charge; the login works fine now.",
            "I was charged twice this month and now my account is locked. I really just need a password reset link emailed to me.",
        ],
    },
    {
        "id": "trial_expired",
        "base": "My payment went through but the app still says my trial expired.",
        "nulls": [
            "I paid but the app keeps telling me my trial has expired.",
            "The charge cleared yet the app shows my trial as expired.",
            "My card was charged but I'm still seeing a 'trial expired' message.",
        ],
        "effects": [
            "My payment went through but the app still says my trial expired. Actually the whole app is blank and unresponsive on every device.",
            "My payment went through but the app still says my trial expired. I want a refund — I no longer want the subscription at all.",
            "My payment went through but the app still says my trial expired. Can you upgrade me to the annual plan while you're at it?",
        ],
    },
    {
        "id": "order_track",
        "base": "My order is delayed and the tracking link gives an error page.",
        "nulls": [
            "My delivery is late and the tracking page just shows an error.",
            "The order hasn't arrived and the tracking link errors out.",
            "My package is delayed and clicking the tracking link gives an error page.",
        ],
        "effects": [
            "My order is delayed and the tracking link gives an error page. Honestly the whole website throws a 404 for me on every link.",
            "My order is delayed and the tracking link gives an error page. I'd like to cancel it and get a full refund please.",
            "My order is delayed and the tracking link gives an error page. Where is my package — it was due 5 days ago?",
        ],
    },
    {
        "id": "upgrade_fail",
        "base": "I want to upgrade my plan but the checkout keeps failing.",
        "nulls": [
            "I'm trying to upgrade my plan and the checkout fails every time.",
            "Upgrading my plan won't work — the checkout keeps erroring out.",
            "Every time I try to upgrade, the checkout page fails.",
        ],
        "effects": [
            "I want to upgrade my plan but the checkout keeps failing. The checkout page itself crashes with a JavaScript error.",
            "I want to upgrade my plan but the checkout keeps failing. My card was charged anyway and I want that reversed.",
            "I want to upgrade my plan but the checkout keeps failing. Which plan tier do you recommend for a 10-person team?",
        ],
    },
    {
        "id": "laptop_ship",
        "base": "I ordered a laptop three weeks ago and it still has not shipped. Where is it?",
        "nulls": [
            "I bought a laptop three weeks ago and it still hasn't shipped — where is it?",
            "It's been three weeks since I ordered a laptop and nothing has shipped yet.",
            "My laptop order from three weeks ago still hasn't shipped. Any update?",
        ],
        "effects": [
            "I ordered a laptop three weeks ago and it still has not shipped. Also I can't even log into my account to check the order.",
            "I ordered a laptop three weeks ago and it still has not shipped. Just refund me, I don't want it anymore.",
            "I ordered a laptop three weeks ago and it still has not shipped. The order page also shows a server error when it loads.",
        ],
    },
    {
        "id": "app_crash",
        "base": "The mobile app crashes every time I tap the Settings icon.",
        "nulls": [
            "Every time I tap Settings, the mobile app crashes.",
            "The app crashes the moment I open the Settings screen.",
            "Tapping the Settings icon makes the mobile app crash each time.",
        ],
        "effects": [
            "The mobile app crashes every time I tap the Settings icon. Also I was charged for a plan I never bought.",
            "The mobile app crashes every time I tap the Settings icon. I'm locked out and need my account recovered.",
            "The mobile app crashes every time I tap the Settings icon. Do you offer an enterprise plan with phone support?",
        ],
    },
    # Deliberately BALANCED prompts (true ~50/50 tension) to try to surface the
    # near-boundary regime where minimax actually flips between runs.
    {
        "id": "cancel_refund",
        "base": "I want to cancel my subscription and get a refund for this month.",
        "nulls": [
            "Please cancel my subscription and refund me for this month.",
            "I'd like to end my subscription and have this month's charge refunded.",
            "Cancel my plan and give me a refund for the current month, please.",
        ],
        "effects": [
            "I want to cancel my subscription and get a refund for this month. The only reason is the app has been completely broken for weeks.",
            "I want to cancel my subscription and get a refund for this month. Mainly I just need the duplicate charge reversed today.",
            "I want to cancel my subscription and get a refund for this month. Also please delete my account and all my data entirely.",
        ],
    },
    {
        "id": "payment_method",
        "base": "Can you help me update the payment method on my account?",
        "nulls": [
            "I need help changing the payment method on my account.",
            "How do I update the card on file for my account?",
            "Please help me switch the payment method linked to my account.",
        ],
        "effects": [
            "Can you help me update the payment method on my account? The settings page errors out whenever I try to save a card.",
            "Can you help me update the payment method on my account? I was also charged on the old card and want that refunded.",
            "Can you help me update the payment method on my account? I'm switching because I'm upgrading to the business plan.",
        ],
    },
    {
        "id": "autorenew",
        "base": "My subscription auto-renewed and I'd like my money back.",
        "nulls": [
            "My plan auto-renewed and I want a refund.",
            "The subscription renewed automatically and I'd like my money back.",
            "I got auto-renewed and would like to be refunded.",
        ],
        "effects": [
            "My subscription auto-renewed and I'd like my money back. I never even use it because it crashes on launch every time.",
            "My subscription auto-renewed and I'd like my money back. Please also turn off auto-renew on my account permanently.",
            "My subscription auto-renewed and I'd like my money back. I'd consider staying if you have a cheaper tier to sell me.",
        ],
    },
]


def make_client() -> OpenAI:
    if not API_KEY:
        sys.exit("Set OPENROUTER_API_KEY in .env first (see .env.example).")
    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


_CLIENT = None


def classify(text: str) -> tuple[str, float]:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = make_client()
    resp = _CLIENT.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
        temperature=TEMP,
        max_tokens=16,
        # snap-decision regime (no chain-of-thought) — noisier, and the hard case the
        # moat targets; also avoids reasoning tokens eating a tiny max_tokens budget.
        extra_body={"reasoning": {"enabled": False}},
    )
    reply = (resp.choices[0].message.content or "").lower()
    outcome = "unparsed"
    for tok in re.findall(r"[a-z]+", reply):
        if tok in CATS:
            outcome = tok
            break
    usage = resp.usage
    cost = 0.0
    if usage is not None:
        cost = getattr(usage, "cost", None)
        if cost is None and getattr(usage, "model_extra", None):
            cost = usage.model_extra.get("cost", 0.0)
        cost = cost or 0.0
    return outcome, float(cost)


def run_all(calls: list[tuple]) -> tuple[dict, float, int]:
    """calls: list of (key, text). Returns {key: [outcomes]}, total_cost, n_errors."""
    results: dict = {}
    total_cost = 0.0
    errors = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(classify, text): key for key, text in calls}
        done = 0
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                outcome, cost = fut.result()
                total_cost += cost
            except Exception:
                outcome, errors = "error", errors + 1
            results.setdefault(key, []).append(outcome)
            done += 1
            if done % 40 == 0:
                print(f"  … {done}/{len(calls)} calls", flush=True)
    return results, total_cost, errors


def main() -> None:
    calls: list[tuple] = []
    for d in DATASET:
        for _ in range(N_AA):
            calls.append((("AA", d["id"]), d["base"]))
        for j, t in enumerate(d["nulls"]):
            for _ in range(M_EDIT):
                calls.append((("E", d["id"], "null", j), t))
        for j, t in enumerate(d["effects"]):
            for _ in range(M_EDIT):
                calls.append((("E", d["id"], "eff", j), t))

    print(f"Spike-1 pilot: model={MODEL} temp={TEMP} N_AA={N_AA} M={M_EDIT} | {len(calls)} calls\n")
    results, cost, errors = run_all(calls)

    # A/A baselines + stratify each base by its noise floor.
    p0: dict = {}
    stratum: dict = {}
    indeterminate = []
    print("\nA/A baselines (noise floor per base):")
    for d in DATASET:
        outs = [o for o in results.get(("AA", d["id"]), []) if o in CATS]
        c = Counter(outs)
        total = sum(c.values()) or 1
        dist = {k: v / total for k, v in c.items()}
        p0[d["id"]] = dist
        modal = max(dist.values()) if dist else 0.0
        flip = 1 - modal
        if flip > GATE_FLIP:
            strat = "INDETERMINATE"
            indeterminate.append(d["id"])
        elif flip > NEAR_FLIP:
            strat = "near-boundary"
        else:
            strat = "clear"
        stratum[d["id"]] = strat
        top = ", ".join(f"{k}={v:.2f}" for k, v in sorted(dist.items(), key=lambda x: -x[1])[:3])
        print(f"  {d['id']:<14} modal={modal:.2f} flip={flip:.2f}  -> {strat:<14} [{top}]")

    # Per-edit outcome distributions.
    edit_dist: dict = {}
    for key, outs in results.items():
        if key[0] == "E":
            edit_dist[key] = Counter(o for o in outs if o in CATS)

    # Score every edit instance: score = P0(outcome) (lower => more likely edit-caused).
    # Exclude edits whose base is auto-INDETERMINATE (we wouldn't attempt attribution).
    # For TPR, only count REALIZED effects (the edit's own modal outcome differs from the
    # base's) — otherwise we'd conflate "the edit had no effect on the model" with
    # "the effect was undetectable", which is the real question.
    null_scores: list[float] = []
    eff_scores: dict[str, list[float]] = {"clear": [], "near-boundary": []}
    realized: dict[str, list[int]] = {"clear": [0, 0], "near-boundary": [0, 0]}  # [realized, total]
    for d in DATASET:
        sid = d["id"]
        if stratum[sid] == "INDETERMINATE":
            continue
        dist = p0[sid]
        base_modal = max(dist, key=dist.get) if dist else None
        strat = stratum[sid]
        for key, c in edit_dist.items():
            if key[1] != sid or not c:
                continue
            outs = list(c.elements())
            if key[2] == "null":
                null_scores.extend(dist.get(o, 0.0) for o in outs)
            else:
                realized[strat][1] += 1
                edit_modal = max(c, key=c.get)
                if edit_modal != base_modal:  # the edit actually moved the model
                    realized[strat][0] += 1
                    eff_scores[strat].extend(dist.get(o, 0.0) for o in outs)

    # Calibrate tau for FPR <= 5% on the null edits, then read TPR per stratum.
    cand = sorted(set(null_scores + [s for v in eff_scores.values() for s in v]))
    n_null = len(null_scores) or 1
    tau_star, fpr_star = -1.0, 0.0
    for tau in cand:
        fpr = sum(1 for s in null_scores if s <= tau) / n_null
        if fpr <= 0.05:
            tau_star, fpr_star = tau, fpr

    def tpr(scores: list[float]) -> float:
        return (sum(1 for s in scores if s <= tau_star) / len(scores)) if scores else float("nan")

    tpr_near = tpr(eff_scores["near-boundary"])
    tpr_clear = tpr(eff_scores["clear"])
    y_indet = len(indeterminate) / len(DATASET)

    def rate(rt):
        return (rt[0] / rt[1]) if rt[1] else float("nan")

    print("\n── calibration ──")
    print(f"  labeler: flag edit-caused if P0(outcome) <= tau ; calibrated tau*={tau_star:.3f} (FPR={fpr_star:.2%})")
    print(f"  effect-realization rate (edit actually moved the model): "
          f"clear={rate(realized['clear']):.2f} near-boundary={rate(realized['near-boundary']):.2f}")
    print(f"  TPR@~5%FPR on REALIZED effects:  clear={tpr_clear:.2f}   near-boundary={tpr_near:.2f}   (decision metric: near-boundary)")
    print(f"  auto-INDETERMINATE fraction = {y_indet:.2f}  ({len(indeterminate)}/{len(DATASET)} bases: {indeterminate})")
    print(f"  cost=${cost:.4f} over {len(calls)} calls, {errors} errors")

    # Decision vs ratified thresholds (PROVISIONAL — minimax pilot, no bitwise oracle).
    near_ok = (not tpr_near != tpr_near) and tpr_near >= X_NEAR_TPR  # NaN-safe
    y_ok = y_indet <= Y_MAX_INDET
    if eff_scores["near-boundary"] == []:
        decision = "INCONCLUSIVE (no near-boundary bases observed — raise temp or add ambiguous prompts)"
    elif not near_ok or not y_ok:
        decision = "OSS-FIRST (closed-API attribution below bar on this pilot)"
    else:
        decision = "PROMISING (meets X and Y on this pilot — confirm on Claude before relying)"
    print(f"\n  >>> DECISION (PROVISIONAL): {decision}")
    print("      vs ratified X(near-TPR@5%FPR)>=0.70, Y(indeterminate)<=0.40")

    out = ROOT / "runs" / "spike1-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": MODEL, "temp": TEMP, "N_AA": N_AA, "M": M_EDIT,
        "p0": p0, "stratum": stratum, "tau_star": tau_star, "fpr_star": fpr_star,
        "tpr_near": tpr_near, "tpr_clear": tpr_clear, "y_indeterminate": y_indet,
        "cost_usd": cost, "calls": len(calls), "errors": errors, "decision": decision,
    }, indent=2))
    print(f"\n  raw results -> {out}")


if __name__ == "__main__":
    main()
