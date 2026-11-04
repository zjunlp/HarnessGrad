---
title: Introducing HarnessGrad
date: 2026-10-16
---

HarnessGrad is the gradient for harness self-improvement: the signal a method needs before it
can improve anything, built so the number can be checked rather than asserted. This site is
its front page.

## What it is for

Every harness-evolution method reports a final score delta. None of them can say what that
delta is *made of*, and none report it on a curve a reader can compare against another
method's. HarnessGrad defines one: a base harness, a fixed task set, a sampling loop, and a
curve point that records the harness state, the two models involved, the score with an
interval, and the compute spent.

The repository proposes no RSI method. It ships runnable reference methods, but the
contribution is the instrument that decides whether any of them worked — and it measures
every candidate itself, because a subject that scores itself makes the comparison
meaningless.

## What runs today

- **The local demo**, end to end, with zero network and zero cost.
- **The measurement contract**, written down as `INTERFACE.md`.
- **Enforced isolation**: a harness cannot see the platform while it runs, and runs made
  under different isolation rules are never placed on one axis.
- **Per-dataset tests**, so one change does not require the whole suite.

## What is not claimed

Local results are not production evidence, and a demo or diagnostic grants no production
authorization. Per-dataset gaps in runtime, quota, snapshot and cleanup enforcement are
recorded with a disposition rather than smoothed over.

No dataset has been chosen, and the metric is defined no further than "defined on a task
set and pairable". Contamination — whether the agent model has seen the benchmark tasks —
is not detectable from inside the framework, and is the largest known threat to the
validity of any curve.

[Getting started](/HarnessGrad/getting-started/) runs the demo.
