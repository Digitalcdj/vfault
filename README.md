# VFault — Verification at the Fault Lines

**Catches AI hallucinations in code. Deterministic. Under 1ms. Zero false positives.**

Every AI coding tool hallucinates function names, deprecated APIs, and wrong parameters. Every hallucination detection tool uses another AI to check — which can hallucinate too.

VFault checks against the actual source code. No AI in the verification layer. The function either exists or it doesn't.

🌐 **Website:** [vfault.com](https://vfault.com)
📡 **Live API:** [exceedweb.pythonanywhere.com](https://exceedweb.pythonanywhere.com)
📖 **Docs:** [vfault.com/docs.html](https://vfault.com/docs.html)

```
$ vfault check "Use wp_register_scripts() to register your JS file"

NOT FOUND — HALLUCINATION CAUGHT
  wp_register_scripts → did you mean: wp_register_script
```

## Quick start

### VS Code extension

Coming soon to the VS Code Marketplace. Follow this repo for the release.

Works with **VS Code** and **Cursor**:
- 🔴 **Red underline** — function doesn't exist (hallucinated). Hover for suggestions.
- 🟡 **Yellow underline** — function is deprecated. Hover for the replacement.
- 🟢 **Green dotted** — function verified. Hover for version, parameters, source.
- 💡 **Quick fix** — click the lightbulb, pick the correct function, one-click replace.

### CLI

```
git clone https://github.com/Digitalcdj/vfault.git
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

## How it's different

|                           | VFault                           | Every competitor    |
| ------------------------- | -------------------------------- | ------------------- |
| Method                    | Deterministic source code lookup | AI judges AI        |
| Speed                     | <1ms                             | 152ms+              |
| False positives           | 0%                               | 5–10%               |
| Corrections               | Yes + closest match + source     | No — score only     |
| Can verifier hallucinate? | Impossible                       | Yes                 |
| Ecosystem isolation       | Yes (shards)                     | No                  |

## Benchmark results

Tested against three frontier models on 50+ WordPress questions:

| Test                                    | Result                      |
| --------------------------------------- | --------------------------- |
| Grok (xAI) — 50 standard questions      | 94 claims, 0 hallucinations |
| Gemini (Google) — 50 standard questions | 93 claims, 0 hallucinations |
| Claude (Anthropic) — 8 hard questions   | 61 claims, 4 hallucinations |
| **Gate caught Claude's hallucinations** | **4/4 (100%)**              |
| Deliberately fabricated input           | 100% fake functions caught  |
| False positive rate                     | 0%                          |

## Store contents (WordPress shard)

| Category             | Count  |
| -------------------- | ------ |
| Active functions     | 4,192  |
| Deprecated functions | 402    |
| Action hooks         | 610    |
| Filter hooks         | 1,736  |
| Class methods        | 27     |
| Total triples        | 25,398 |
| Database size        | 5.8 MB |

## API

Base URL: `https://exceedweb.pythonanywhere.com`

All endpoints accept an optional `X-API-Key` header. Without a key, you're on the free tier (100 requests/day).

```bash
# Verify text for hallucinations
curl -X POST https://exceedweb.pythonanywhere.com/verify \
  -H "Content-Type: application/json" \
  -d '{"text": "Use wp_register_scripts() to add JS"}'

# Look up a specific function
curl https://exceedweb.pythonanywhere.com/lookup/wp_enqueue_script

# Search functions by prefix
curl https://exceedweb.pythonanywhere.com/search/wp_enqueue

# Compare parameter signatures
curl -X POST https://exceedweb.pythonanywhere.com/compare_params \
  -H "Content-Type: application/json" \
  -d '{"function": "wp_enqueue_script", "stated_params": "bool $in_footer"}'

# Check your usage
curl https://exceedweb.pythonanywhere.com/usage

# Store statistics
curl https://exceedweb.pythonanywhere.com/stats

# Health check
curl https://exceedweb.pythonanywhere.com/health
```

## Pricing

| Tier           | What you get                                    | Price          |
| -------------- | ----------------------------------------------- | -------------- |
| **Free**       | WordPress shard + CLI + VS Code + 100 req/day   | £0 forever     |
| **Pro**        | All shards + 5,000 req/day + API key            | £19/month      |
| **Team**       | All shards + 20,000 req/day + team keys         | £49/month      |
| **Enterprise** | Custom shard + 100,000 req/day + priority       | From £199/month |

The CLI and WordPress shard are free forever. The hosted service saves you from running infrastructure.

## Roadmap

- [x] Three-agent pipeline architecture
- [x] WordPress shard — 25,398 triples
- [x] CLI tool
- [x] FastAPI web server with 7 endpoints
- [x] Live API deployed on PythonAnywhere
- [x] Benchmarks across 3 frontier models
- [x] Website with live demo — vfault.com
- [x] VS Code extension with diagnostics, hover, quick-fix, autocomplete
- [x] API key authentication and tiered rate limiting
- [x] Lemon Squeezy webhook integration
- [ ] VS Code Marketplace publication
- [ ] Lemon Squeezy store live
- [ ] WooCommerce shard
- [ ] Python, JavaScript, Laravel shards on public API
- [ ] React / Next.js shard
- [ ] Django shard
- [ ] CI/CD GitHub Action

## Rebuild the store

```
git clone --depth 1 https://github.com/WordPress/WordPress.git wordpress-source
cd shards/wordpress
python3 parser.py ../../wordpress-source
cd ../..
```

Run after each WordPress release to keep the store current.

## Run tests

```
python3 tests/run_tests.py
```

## Project structure

```
vfault/
├── vfault.py                    # Gate CLI + API
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
    └── architecture.md          # Full architecture paper
```

## Contributing

Contributions welcome:

- **New shards:** WooCommerce, ACF, React, Django
- **Gate improvements:** better extraction, streaming mode
- **Benchmarks:** more models, harder questions, real-world codebases
- **Documentation:** tutorials, integration guides

## Author

Ian Fraser — [Exceed Web Services](https://exceedwebservices.co.uk) · Ceredigion, Wales

*VFault: verification at the fault lines.*
