# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's private vulnerability reporting feature
for this repository. Do not include API keys, access tokens, cookies, account identifiers, or
private market-research data in a public issue.

## Credential handling

Real credentials belong only in a local `.env` or an external secret manager. The project does
not require contributors to share provider credentials. If a credential is committed by mistake,
revoke it immediately before rewriting repository history.

## Supported scope

The current prototype is intended for local research use. Public network exposure, automated
broker access, and live order execution are outside the supported security boundary.
