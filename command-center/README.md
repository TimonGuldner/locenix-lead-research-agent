# LOCENIX Command Center

Internal dashboard for the LOCENIX autonomous growth department.

## Vercel deployment

Import the GitHub repository `TimonGuldner/locenix-lead-research-agent` into Vercel and set **Root Directory** to `command-center`.

The dashboard status endpoint needs no secret because it reads public, non-sensitive agent state JSON from the public repository.

To enable the Command Console for creating department proposals, add this Vercel Environment Variable:

- `GITHUB_TOKEN`: fine-grained GitHub token with Contents read/write access restricted to `TimonGuldner/locenix-lead-research-agent`.

Never expose the token in browser code. It is used only by `api/department.js` on the server.

## Safety model

- New departments are created as `PROPOSED` only.
- Agent 7 detects new department proposals automatically.
- External messaging remains disabled.
- Unknown workflows are not dispatched automatically.
- GitHub remains the source of truth.

## Live status

`/api/status` combines the current state of Lead Research, Deep QA, Maps Visibility, Sales Queue, Outreach Drafts and the Growth Manager. The browser refreshes every 30 seconds.
