---
title: TriviaQA key audit
subtitle: Partial observations; official denominator remains unknown
theme: paper
cols: 2
---

## A New observations

| Observation | Measured result |
| --- | --- |
| rc.nocontext validation rows | 17,944; corroborates structured count |
| Two rc validation shards | 8,972 rows; 8,202 distinct QuestionIds |
| Across those shards | 770 shared QuestionIds; 1,339 shared candidate document keys |

Source: tests/fixtures/hg_trivia3/observations.json:134,470,584,622.

## B Evidence limits

Four validation key shards and thirteen footers completed. Transport failures left the broader audit incomplete.
Source: tests/fixtures/hg_trivia3/key-audit-summary.json:1 and observations.json:104.

HumanAnswers input incidence, original verification membership, official mapping and numeric denominator remain UNKNOWN.
Source: registry/w-trivia3-review.md:63,121,153.

## C Declaration reconciliation

Twelve declarations describe eight exported routes. Four extra unfiltered source-specific declarations have no exported route or count.
Source: registry/w-trivia2-review.md:103; tests/fixtures/hg_trivia3/declarations.json:1.

The 17,944-row footer contradicts the 18,669 prose count for pinned rc.nocontext validation.
Source: registry/w-trivia3-review.md:111.

## D Settling artifact

Get one immutable input/export membership-and-preservation audit packet.
Bind original source and verification flags, export row ordinals, official keys, duplicate order and requested key list.
Include HumanAnswers incidence and preservation booleans without answer text.
Source: registry/w-trivia3-review.md:161.
