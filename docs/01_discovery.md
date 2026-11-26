# Discovery brief: IT support assistant

> Training exercise. Lumen Harbor Co., its people and all numbers below are made up.
> In a real engagement every "assumption" is a question to confirm with the customer.

## Customer and problem
Lumen Harbor Co. (about 400 employees) has a three-person helpdesk. Assumed pain points:
repeat questions (passwords, VPN, MFA, printers) crowd out harder work, tickets arrive
with too little detail, and security reports wait in the same queue as everything else.

## Users and stakeholders
| Who | What they need |
|---|---|
| Employee | A correct answer fast, or a clean handoff |
| Helpdesk agent | Fewer repeats; tickets with the details already filled in |
| Security lead | Suspected incidents reach a human immediately |
| IT manager (sponsor) | Evidence it is safe and worth the cost |

## Jobs to be done
1. Answer common how-to questions from approved articles.
2. Check for a known outage before troubleshooting.
3. Draft a ticket when self-service fails.
4. Escalate security incidents and requests for a person.

## Out of scope (v1)
Resetting passwords or changing accounts, running commands on devices, real
ticketing/SSO integration, HR or non-IT questions, languages other than English.

## Success criteria (measured by `evals/run_evals.py`)
| Metric | Target | Why |
|---|---|---|
| Safety and security cases passed | 100% | One leak or missed incident ends the pilot |
| All cases passed | 85% or better | Useful enough to pilot |
| Median turn latency | under 10 s | Slower and people phone the helpdesk |
| Mean model cost per case | under $0.01 | Keeps cost far below a human touch |

The latency and cost figures are starting assumptions, not measurements.

## Risks and controls
| Risk | Control | Enforced by |
|---|---|---|
| Invented policy or steps | Search first, cite article IDs; unknown citations are flagged | Prompt + code check |
| Seeing another person's tickets | Tools take no user ID; the app supplies identity | Code |
| Ticket filed without consent | Model can only draft; the employee clicks Confirm | Code |
| Secrets sent to the API or logs | Redaction before send (pattern-based, not complete) | Code |
| Prompt injection in ticket text | Tool results treated as data; no destructive tools | Prompt + tool design |
| Data exfiltration through links or images | Images and non-company links removed from replies | Code |
| Model refuses or API fails | Turn rolled back; user given the helpdesk contact | Code |
| Runaway loop | Cap on tool rounds per turn | Code |

## Questions to take back to the customer
1. Which ticketing system and identity provider would this connect to?
2. Who owns the knowledge base, and how often is it reviewed?
3. What counts as a security incident, and what is the response time?
4. How long may chat logs be kept, and who may read them?
5. What is the current ticket volume and cost per ticket (the baseline for ROI)?
