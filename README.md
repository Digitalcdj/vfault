# VFault — Verification at the Fault Lines

**Catches AI hallucinations in code. Deterministic. Under 1ms. Zero false positives.**

Every AI coding tool hallucinates function names, deprecated APIs, and wrong parameters. Every hallucination detection tool uses another AI to check — which can hallucinate too.

VFault checks against the actual source code. No AI in the verification layer. The function either exists or it doesn't.

```
$ vfault check "Use wp_register_scripts() to register your JS file"

NOT FOUND — HALLUCINATION CAUGHT
  wp_register_scripts → did you mean: wp_register_script
```

## Quick start

```bash
git clone https://github.com/YOUR_USERNAME/vfault.git
cd vfault
pip install -r requirements.txt

# Run the demo
python3 vfault.py --demo

# Check any AI-generated code
python3 vfault.py --check "your AI output here"

# Start the API
python3 vfault.py --serve
```

## What it catches

```
Hallucinated function:  wp_register_scripts → wp_register_script
Deprecated function:    get_currentuserinfo → use wp_get_current_user()
Outdated parameter:     bool $in_footer → (array|bool) $args (WP 6.3+)
Fabricated hook:        any hook not in WordPress core → flagged
```

## Benchmark results

Tested against three frontier models on 50+ WordPress questions:

| Test | Result |
|------|--------|
| Grok (xAI) — 50 standard questions | 94 claims, 0 hallucinations |
| Gemini (Google) — 50 standard questions | 93 claims, 0 hallucinations |
| Claude (Anthropic) — 8 hard questions | 61 claims, 4 hallucinations |
| **Gate caught Claude's hallucinations** | **4/4 (100%)** |
| Deliberately fabricated input | 100% fake functions caught |
| False positive rate | 0% |

## How it's different

| | Every competitor | VFault |
|---|---|---|
| Method | AI judges AI | Deterministic source code lookup |
| Speed | 152ms+ | <1ms |
| False positives | 5–10% | 0% |
| Corrections | No — score only | Yes + closest match + source |
| Can verifier hallucinate? | Yes | Impossible |
| Cost | Paid | Free CLI, paid hosted |

## Store contents (WordPress shard)

| Category | Count |
|----------|-------|
| Active functions | 4,192 |
| Deprecated functions | 402 |
| Action hooks | 610 |
| Filter hooks | 1,736 |
| Class methods | 27 |
| Total triples | 25,398 |
| Database size | 5.8 MB |

## API

Start the server:

```bash
python3 vfault.py --serve
```

Endpoints:

```bash
# Verify text for hallucinations
curl -X POST http://localhost:8000/verify \
  -H "Content-Type: application/json" \
  -d '{"text": "Use wp_register_scripts() to add JS"}'

# Look up a specific function
curl http://localhost:8000/lookup/wp_enqueue_script

# Search functions by prefix
curl http://localhost:8000/search/wp_enqueue

# Compare parameter signatures
curl -X POST http://localhost:8000/compare_params \
  -H "Content-Type: application/json" \
  -d '{"function": "wp_enqueue_script", "stated_params": "bool $in_footer"}'

# Store statistics
curl http://localhost:8000/stats

# Health check
curl http://localhost:8000/health
```

## Rebuild the store

```bash
git clone --depth 1 https://github.com/WordPress/WordPress.git wordpress-source
cd shards/wordpress
python3 parser.py ../../wordpress-source
cd ../..
```

Run after each WordPress release to keep the store current.

## Run tests

```bash
python3 tests/run_tests.py
```

9 automated tests covering function verification, hallucination detection, deprecation tracking, parameter comparison, class method recognition, false positive resistance, and end-to-end verification.

## Project structure

```
vfault/
├── vfault.py                    # Gate CLI + API (Agent 2)
├── requirements.txt             # fastapi, uvicorn
├── LICENSE                      # MIT
├── shards/
│   └── wordpress/
│       ├── parser.py            # Parses WP source → triples
│       ├── wordpress.db         # 25,398 verified triples
│       └── summary.json         # Store statistics
├── tests/
│   ├── run_tests.py             # Automated test suite
│   └── benchmark_questions.txt  # 50 test questions
└── docs/
    └── architecture.txt         # Full architecture paper
```

## Pricing

| Tier | What you get | Price |
|------|-------------|-------|
| **Free** | CLI tool + WordPress store + parser | £0 |
| **Developer** | Hosted API (unlimited requests) | £15/month |
| **Pro** | API + VS Code extension + extra shards | £30/month |
| **Team** | API + CI/CD GitHub Action + dashboard | £50/month/seat |
| **Enterprise** | Custom shard from your codebase + SLA | Contact |

The CLI is free forever. The hosted service saves you from running infrastructure.

## Roadmap

- [x] WordPress shard — built, tested, benchmarked
- [x] CLI tool — working
- [x] API with 6 endpoints — working
- [x] Production guardrails — rate limiting, input validation
- [x] Benchmarks across 3 frontier models — measured
- [ ] GitHub release
- [ ] VS Code extension
- [ ] Python stdlib shard
- [ ] JavaScript/Node shard
- [ ] CI/CD GitHub Action
- [ ] Hosted API service

## Contributing

Contributions welcome:

- **New shards:** WooCommerce, ACF, Laravel, React, Django
- **Gate improvements:** better extraction, streaming mode, IDE plugins
- **Benchmarks:** more models, harder questions, real-world codebases
- **Documentation:** tutorials, integration guides

## The architecture

VFault uses a three-agent streaming pipeline. For the full specification including the verified/contested knowledge distinction, competitive analysis, economic model, and deployment roadmap, see `docs/architecture.txt`.

## Author

Ian Fraser — [Exceed Web Services](https://exceedwebservices.co.uk)
Ceredigion, Wales

*VFault: verification at the fault lines.*
