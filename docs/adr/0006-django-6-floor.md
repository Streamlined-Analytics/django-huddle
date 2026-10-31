# ADR-0006: Django 6.0 is the minimum supported version

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** none

## Context

The first consumer, Comply 100, runs Django 6. Supporting 5.2 LTS as well would
double the test matrix for a new package with no existing users.

## Decision

Require `django>=6` and Python 3.12+. Test against Django 6.0 and 6.1.

## Consequences

- Django 5.2 LTS projects can't use it yet. Lowering the floor later is
  possible if there is demand, since nothing yet depends on 6.0-only APIs.
