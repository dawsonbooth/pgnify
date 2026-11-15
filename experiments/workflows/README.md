# The workflow benchmark's request texts

The texts a benchmark session adds to the ordinary request, or sends instead of it, as
experiments/bench_session.py composes them. Each is fixed for the benchmark and recorded by hash
in every run's command.json; a changed text is a new configuration. Placeholders in braces are
filled by the launcher.

- `survey.md`, B: survey the part, write a book map, then convert.
- `strategist.md`, C's first session: survey and map only.
- `with-map.md`, C's converter and C-verified: the ordinary request with a map given.
- `delegate.md`, D: plan with work units, delegate to subagents, reconcile, audit, write.
- `review.md`, R: review a conversion against the book and correct it on a copy.
- `map.md` and `map-units.md`: the book map's format, without and with work units.
- `nudge.md`: the fixed reply to a session that ends its turn before writing its PGN.
