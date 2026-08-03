# Changelog

## v0.1.0 — August 2026

Initial public release.

### What's included
- WordPress shard: 25,398 triples parsed from WordPress core source
- 4,192 active functions, 402 deprecated, 2,346 hooks, 27 class methods
- CLI tool: `--check`, `--demo`, `--serve`
- FastAPI with 6 endpoints: verify, lookup, search, compare_params, stats, health
- Context-aware claim extraction with 0% false positive rate
- Parameter signature comparison (catches outdated params)
- Deprecation tracking with replacement suggestions
- Fuzzy matching with closest-match corrections
- Production guardrails: rate limiting, input validation, name sanitisation
- 9/9 automated tests passing

### Benchmarks
- Tested against Grok (xAI), Gemini (Google), Claude (Anthropic)
- 310+ claims verified across all tests
- 4 real hallucinations caught on hard questions (100% catch rate)
- 0% false positive rate on all tested input
