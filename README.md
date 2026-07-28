# Healthcare RCM Intelligence Platform
> 🚧 In development. Live demo link will appear here.

End-to-end revenue-cycle analytics: CMS synthetic Medicare claims warehouse,
transparently simulated adjudication layer, explainable denial-risk and
appeal-recovery ML, and an analyst work-queue dashboard.

**Data honesty:** all denial/appeal/workflow outcomes are simulated
(`sim_` prefix) and calibrated to cited industry benchmark ranges. See
`docs/provenance_register.md` and `docs/assumptions.md`.

## Models

Two models, both predicting against the **simulated** adjudication layer:

- **Model A — pre-submission denial risk.** Point-in-time features only; the
  champion is a regularized logistic regression, and gradient boosting does not
  beat it. `make train`
- **Model C — appeal success + Expected Net Recovery work queue.** A negative
  result, reported as one: the probability does not earn its place in the
  ranking. `make train-appeal`

**Read `docs/model_card.md` before quoting any number from either.** It states
what the metrics are and are not evidence of, carries every figure with its
interval, and explains the leakage boundary that separates the two models.
