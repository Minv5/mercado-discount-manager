# Security Policy

## Supported versions

Security fixes are applied to the current `main` branch and the current desktop release line. Older packaged releases may not receive backports.

## Reporting a vulnerability

Do not post vulnerabilities, credentials, tokens, customer data, or a working exploit in a public issue.

Use GitHub's private vulnerability reporting for this repository when it is available. If it is not available, contact the maintainer through the GitHub profile and include only the minimum information needed to establish a private channel. After a private channel is established, provide affected versions, a safe reproduction, impact, and proposed mitigations.

The maintainer will acknowledge a report within 7 days when contact details are available, assess the report, and coordinate disclosure after a fix or mitigation is ready. No bounty program is offered.

## Scope

In scope: credential storage and redaction, OAuth callback handling, webhook verification, local service endpoints, dependency vulnerabilities, and safeguards around marketplace writes.

Out of scope: reports that require exposing another person's Mercado Libre account data, or operational requests to change marketplace prices, enrollments, updates, or cancellations.
