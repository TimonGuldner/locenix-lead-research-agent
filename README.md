# LOCENIX Lead Research Agent

Separate research-only browser agent for finding high-quality local-business leads for LOCENIX.

## Purpose
The agent researches local businesses where a free LOCENIX Local Visibility Check is likely to reveal multiple concrete GBP / Google Maps optimization opportunities. It does not contact anyone.

## Architecture
`tasks/current_task.json -> GitHub Actions -> Browser Use + local Chromium -> public web research -> results/leads.json + results/leads.csv`

Each run researches one area/category slice and appends only deduplicated leads at or above the configured minimum score. A scheduled run checks twice per hour, but does nothing while the task is disabled.

## Safety
- Research only
- No emails, DMs or form submissions
- No logins or account creation
- No CAPTCHA / security bypass
- Only public business contact details
- Never invent email addresses or exact ranking claims

## Required GitHub Actions secret
Add `OPENAI_API_KEY` under Repository Settings -> Secrets and variables -> Actions.

The key must never be committed to this repository.

## Start a 100-lead run
1. Add the `OPENAI_API_KEY` Actions secret.
2. Set `"enabled": true` in `tasks/current_task.json`.
3. Update `wake.txt` or manually run the `LOCENIX Lead Research Agent` workflow.
4. The agent works in batches and persists progress under `results/`.
5. Once `target_leads` is reached, later runs stop automatically.

## Current defaults
- target: 100 leads
- batch size: 8
- minimum LOCENIX Funnel Fit score: 70/100
- starting regions: Troisdorf, Siegburg, Sankt Augustin, Bonn, Koeln, Rhein-Sieg-Kreis, then NRW
- preferred categories: physiotherapy, beauty, hair, dental, automotive, trades and other strong local-service categories

## Result files
- `results/leads.json` — canonical structured lead list
- `results/leads.csv` — spreadsheet-friendly export
- `results/state.json` — cursor/progress
- `results/latest_run.json` — diagnostics for the most recent batch

## Chat control
`wake.txt` exists specifically so a connected ChatGPT/GitHub workflow can update one harmless file and immediately wake the workflow. The task settings remain the source of truth.
