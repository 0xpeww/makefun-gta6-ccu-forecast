# Twitch CCU forecast — GTA VI launch day

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/0xpeww/makefun-gta6-ccu-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/0xpeww/makefun-gta6-ccu-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Forecast the Grand Theft Auto VI category's concurrent Twitch viewers for each hour of its
release day, as five quantiles per hour, scored by pinball loss on the log scale.

Bounty: <https://make.fun/bounty/gta6-twitch-ccu-forecast-01>

## What is here

- `main.py` — the entry: Python 3.13, standard library only, no network.
- `LICENSE` — ISC.

## The model

Hour 1 of the release day is given. Hours 2 to 24 are forecast as

    ln(v_h / v_1) = r_h + z_tau * c * sqrt(h - 1)

where `r_h` is the median log-ratio of viewers at hour `h` to hour 1 across comparable AAA
launches shipped in the case, `c` is a fitted spread scale (0.20), and `z_tau` the standard
normal quantile at each of the five levels. Hours with fewer than five usable comparables
fall back to the house baseline's shape (`0.15 * sqrt(h - 1)`).

The comparable pool is filtered by scale — only launches whose hour 1 reaches
`max(20000, 0.15 * this case's hour 1)` are used. That filter is the model's main idea: a
launch that unlocks at 05:00Z has a pre-release hour 1 and a curve that climbs steeply
through hours 2-24, and letting those into the median corrupts the shape for a launch that
was already live in its first hour.

## Run it

    mkdir -p out
    MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py

It reads every `*.in` case in the input directory and writes `<id>.out`: 23 rows, hours
2..24, each carrying five finite, positive, non-decreasing quantiles.

## How it was measured

The kit does not ship the scorer, so the model was validated the only honest way available:
leave-one-launch-out over the 38 comparables in the case, scored with the bounty's own
metric. Restricted to launches with a first hour of at least 20,000 viewers, the model
scores a mean pinball loss of 0.3176 against the house baseline's 0.3588 on the same
launches, winning 18 of 25.

A caveat stated plainly: that backtest is on past launches, not on the four practice cases,
whose answers the server keeps. The server's practice grade is the measurement that counts.
