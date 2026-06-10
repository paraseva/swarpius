<!--
Thanks for the contribution! A few quick reminders before submitting:

  1. Branch name must match: ^(feature|fix|docs|chore|refactor)/[a-z0-9._-]+$
  2. Every commit needs a Signed-off-by line — use `git commit -s`
  3. Local checks should pass before opening:
       Agent      — `./dev ruff check .` and `./dev pytest`
       Web client — `npm run lint`, `npm test`, `npm run build`
  4. See CONTRIBUTING.md for the full guide.

Delete the comment block above this line once you're done with it.
-->

## Summary

<!-- 1-3 sentences: what does this PR change, and why? Focus on the why. -->

## Related issue

<!-- Use "Closes #N" / "Fixes #N" / "Refs #N" so the issue auto-closes on merge. -->

Closes #

## Test plan

<!--
Checklist of how you verified the change. Reviewers will read this and
optionally re-run items themselves. Be specific — "I ran the tests"
isn't useful; "I added test_X covering Y, plus manually exercised the
flow at Z" is.
-->

- [ ]
- [ ]

## Checklist

- [ ] Branch name follows the convention (`feature/`, `fix/`, `docs/`, `chore/`, or `refactor/`).
- [ ] All commits are signed off (`git commit -s`).
- [ ] `ruff check .` and `pytest` pass locally (if agent code changed).
- [ ] `npm run lint`, `npm test`, and `npm run build` pass locally (if web-client changed).
- [ ] Documentation updated where relevant (README, CLAUDE.md, agent/docs/, etc.).
- [ ] CHANGELOG.md updated if this is a user-visible change.

## Notes for reviewers

<!-- Optional: known limitations, follow-up work, things you'd specifically like a second opinion on. -->
