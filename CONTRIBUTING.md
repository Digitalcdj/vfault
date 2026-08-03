# Contributing to VFault

Thanks for your interest in contributing.

## Ways to contribute

**Report bugs** — open an issue with the input text that caused the problem and what you expected vs what happened.

**Test against more models** — run AI-generated WordPress code through VFault and share the results. We're especially interested in edge cases where models hallucinate less common functions.

**Add new shards** — the biggest contribution. Each shard needs a parser that reads the framework's source and outputs structured triples in the same SQLite schema. See `shards/wordpress/parser.py` for the pattern.

Planned shards:
- WooCommerce
- Python stdlib
- JavaScript/Node core
- PHP stdlib
- Laravel
- React

**Improve claim extraction** — the gate's context-aware extraction works well but there are always edge cases. If you find a false positive or a missed claim, open an issue with the exact text.

**Documentation** — tutorials, integration guides, examples of VFault catching real hallucinations in the wild.

## How to submit changes

1. Fork the repo
2. Create a branch: `git checkout -b my-fix`
3. Make your changes
4. Run the tests: `python3 tests/run_tests.py`
5. All 9 tests must pass
6. Open a pull request with a clear description of what you changed and why

## Code style

- Python 3.8+
- No external dependencies beyond FastAPI and uvicorn for the API
- Keep the gate fast — no operations that break the <1ms lookup promise
- Context-aware extraction must maintain 0% false positive rate

## Questions?

Open an issue. Keep it specific.
