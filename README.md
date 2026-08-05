# [VFault](https://vfault.com) — Verification at the Fault Lines

**Catches AI hallucinations in code. Deterministic. Under 1ms. Zero false positives.**

Every AI coding tool hallucinates function names, deprecated APIs, and wrong parameters. Every hallucination detection tool uses another AI to check — which can hallucinate too.

VFault checks against the actual source code. No AI in the verification layer. The function either exists or it doesn't.

**[Try it live on vfault.com →](https://vfault.com)**

```
$ python3 vfault.py --check "Use wp_register_scripts() to register your JS file"

VFAULT VERIFICATION REPORT
Claims found: 1
Verified:     0
Not found:    1

NOT FOUND (possible hallucinations)
  wp_register_scripts
    suggestions: wp_register_script, wp_deregister_script
```

## Quick start

```bash
git clone https://github.com/Digitalcdj/vfault.git
cd vfault
pip install -r requirements.txt

# Run the demo
python3 vfault.py --demo

# Check any AI-generated code
python3 vfault.py --check "your AI output here"

# Self-hosted API
python3 vfault.py --serve

# Or use the live API
curl https://exceedweb.pythonanywhere.com/health
```

## What it catches

```
Hallucinated function:  wp_register_scripts → wp_register_script
Deprecated function:    get_currentuserinfo → use wp_get_current_user()
Outdated parameter:     bool $in_footer → (array|bool) $args (WP 6.3+)
Fabricated hook:        any hook not in WordPress core → flagged
```

## See it work

**Hallucinated function — caught with correction:**
```
Input:  "Use wp_is_rest_request() to check if it's a REST call"
Output: NOT FOUND — wp_is_rest_request
        Did you mean: wp_is_serving_rest_request
```

**Deprecated function — caught with replacement:**
```
Input:  "Get user data with get_currentuserinfo()"
Output: DEPRECATED in 4.5.0. Use wp_get_current_user() instead.
```

**Outdated parameter — caught with current signature:**
```
Input:  "wp_enqueue_script('handle', 'src', array(), '1.0', bool $in_footer)"
Output: PARAMETER MISMATCH
        Missing params: $args
        Extra/wrong params: $in_footer
        Correct: (string) $handle, (string) $src, (string[]) $deps,
                 (string|bool|null) $ver, (array|bool) $args = array()
```

**Clean code — verified with source info:**
```
Input:  "Use wp_enqueue_script() and add_action() to load scripts"
Output: VERIFIED
        wp_enqueue_script (since 2.1.0)
        add_action (since 1.2.0)
```

## Benchmark results

### Standard test — 50 WordPress questions

| Model | Claims | Verified | Hallucinations | False positives |
|-------|--------|----------|----------------|-----------------|
| Grok (xAI) | 94 | 94 | 0 | 0 |
| Gemini (Google) | 93 | 93 | 0 | 0 |

### Hard test — 8 advanced topics

| Model | Claims | Verified | Hallucinations | Gate caught |
|-------|--------|----------|----------------|-------------|
| Claude (Anthropic) | 61 | 51 | 4 | 4/4 (100%) |

Claude hallucinated: `wp_is_rest_request`, `wp_register_personal_data_eraser`, `wp_register_personal_data_exporter`, `get_rewrite_rules`. The gate caught all four with correction suggestions.

### Deliberately fabricated input

100% catch rate on fake function names. 90% total catch rate including deprecated detection.

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

### Live API

The free public API is available at `https://exceedweb.pythonanywhere.com`

```bash
# Verify text
curl -X POST https://exceedweb.pythonanywhere.com/verify \
  -H "Content-Type: application/json" \
  -d '{"text": "Use wp_register_scripts() to add JS"}'

# Look up a function
curl https://exceedweb.pythonanywhere.com/lookup/wp_enqueue_script

# Search by prefix
curl https://exceedweb.pythonanywhere.com/search/wp_enqueue

# Stats
curl https://exceedweb.pythonanywhere.com/stats
```

### Self-hosted

```bash
python3 vfault.py --serve
```

### POST /verify — check text for hallucinations

Response:
```json
{
  "summary": {
    "total_claims": 1,
    "verified": 0,
    "deprecated": 0,
    "not_found": 1,
    "hallucination_rate": "100.0%"
  },
  "not_found": [
    {
      "name": "wp_register_scripts",
      "exists": false,
      "status": "not_found",
      "message": "NOT FOUND. Did you mean: wp_register_script?",
      "suggestions": ["wp_register_script", "wp_deregister_script"]
    }
  ]
}
```

### GET /lookup/{name} — look up a specific function

Response:
```json
{
  "name": "wp_enqueue_script",
  "exists": true,
  "type": "function",
  "status": "verified",
  "since": "2.1.0",
  "source": "wp-includes/functions.wp-scripts.php",
  "parameters": "(string) $handle, (string) $src = '', (string[]) $deps = array(), (string|bool|null) $ver = false, (array|bool) $args = array()",
  "message": "Verified — exists in WordPress core."
}
```

### POST /compare_params — compare parameter signatures

Response:
```json
{
  "function": "wp_enqueue_script",
  "stored_params": "(string) $handle, (string) $src = '', ...",
  "status": "param_mismatch",
  "missing_params": ["src", "deps", "ver", "args"],
  "extra_params": ["in_footer"],
  "message": "PARAMETER MISMATCH: Missing params: $src, $deps, $ver, $args; Extra/wrong params: $in_footer"
}
```

### GET /search/{prefix} — search functions by prefix

### GET /stats — store statistics

### GET /health — health check

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
python3 tests/run_tests.py          # Core tests (9/9)
python3 tests/test_realworld.py     # Messy real-world tests (8/8)
python3 tests/test_load.py          # Load & concurrency tests (6/6)
```

101 automated tests covering function verification, hallucination detection, deprecation tracking, parameter comparison, class method recognition, false positive resistance, real-world AI output, and performance under load.

## Project structure

```
vfault/
├── vfault.py                    # Gate CLI + API (Agent 2)
├── requirements.txt             # fastapi, uvicorn
├── pyproject.toml               # Package configuration
├── LICENSE                      # MIT
├── CONTRIBUTING.md              # How to contribute
├── CHANGELOG.md                 # Release history
├── shards/
│   └── wordpress/
│       ├── parser.py            # Parses WP source → triples
│       ├── wordpress.db         # 25,398 verified triples
│       └── summary.json         # Store statistics
├── tests/
│   ├── run_tests.py             # Core test suite
│   ├── test_realworld.py        # Messy real-world tests
│   ├── test_load.py             # Load & concurrency tests
│   └── benchmark_questions.txt  # 50 test questions
└── docs/
    └── architecture.md          # Technical architecture
```

## Pricing

The CLI and WordPress shard are free forever. Additional shards, VS Code extension, and enterprise features coming soon.

Visit [vfault.com](https://vfault.com) for details.

## Roadmap

- [x] WordPress shard — built, tested, benchmarked
- [x] CLI tool — working
- [x] API with 6 endpoints — working
- [x] Production guardrails — rate limiting, input validation
- [x] Benchmarks across 3 frontier models — measured
- [x] GitHub release — v0.1.0
- [x] Website — [vfault.com](https://vfault.com)
- [x] Hosted API — [exceedweb.pythonanywhere.com](https://exceedweb.pythonanywhere.com)
- [x] Live demo on website — try it without installing
- [x] Python stdlib shard — built (Pro tier)
- [x] JavaScript/Node shard — built (Pro tier)
- [x] Laravel shard — built (Pro tier)
- [ ] VS Code extension
- [ ] CI/CD GitHub Action

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on submitting changes, adding new shards, and improving the gate.

## The architecture

VFault uses a three-agent streaming pipeline. For the full specification including the verified/contested knowledge distinction and deployment roadmap, see [docs/architecture.md](docs/architecture.md).

## Author

Ian Fraser — [vfault.com](https://vfault.com) — [Exceed Web Services](https://exceedwebservices.co.uk)
Ceredigion, Wales

*VFault: verification at the fault lines.*
