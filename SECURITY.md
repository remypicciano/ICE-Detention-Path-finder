# Security Policy

This project reads sensitive government data about people in immigration
custody. Its security priorities are:

1. **Privacy of the data** — real identifiers, generated pathways, and datasets
   must never leak through this repository, its issues, or its CI.
2. **Trustworthiness of the pipeline** — downloads, lookups, and verification
   must be tamper-resistant and honest about uncertainty.

## Reporting a vulnerability

**Do not open a public issue for a security or privacy concern.** Report it
privately to the maintainer via
[Rémy Picciano's GitHub profile](https://github.com/remypicciano), or use
GitHub's [private vulnerability reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository.

Please include:

- What the vulnerability is and its potential impact
- Steps to reproduce, using fabricated identifiers only
- Any relevant environment details

You can expect an initial response within a week. We will coordinate disclosure
and credit you if you wish.

## Scope

The following are in scope:

- Code execution, path traversal, or data-exfiltration via crafted input to any
  tool in this repository
- A downloader that could be made to accept a tampered or spoofed dataset
- Leakage of real identifiers through the repository, releases, CI logs, or
  application bundles

Out of scope:

- The upstream Deportation Data Project, its data, or its infrastructure
- ICE's own systems and datasets

## Data-handling policy

- Never post real identifiers or generated pathways for real people in issues,
  PRs, or screenshots.
- The application makes no network requests during lookups. Network use is
  limited to the explicit dataset-download feature, which validates every
  download (HTTPS, readable Parquet, expected columns) before replacing local
  files.
- Datasets are excluded from Git and from application bundles. Keep it that way.
