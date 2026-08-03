# VFault — Technical Architecture

## Overview

VFault verifies AI-generated code against parsed source code using deterministic lookup. No LLM in the verification layer. The function either exists in the source or it doesn't.

The system uses structured triples (subject, predicate, object) parsed directly from authoritative source code repositories. Each triple carries verification status, source file, version introduced, deprecation status, and replacement information.

## Three-Agent Pipeline

### Agent 1 — Generator
Any LLM (Claude, GPT, Grok, Gemini). Generates a response to the user's query. Tags its own factual claims using inline markers. Does not verify anything.

### Agent 2 — Resolver-Assembler
Deterministic lookup engine. No LLM needed. Takes tagged claims from Agent 1 and checks each one against the hot-cached triple store in real time.

For deterministic domains (code, APIs, documentation), "not found in store" IS the verification. The function either exists or it doesn't. No probabilistic scoring required.

### Agent 3 — Verifier
Fires only for non-deterministic domains (medical, legal, emerging science) where "not found" means "we don't know yet" rather than "this is wrong." Architecturally independent from Agent 1 — different model, different provider, rotating pool. Optimised for scepticism, not helpfulness.

Not needed for the WordPress shard. WordPress is deterministic.

### Pipeline flow

```
User query
    |
    v
[Agent 1: Generator] — streams response + claim tags
    |
    v
[Claim extraction] — context-aware pattern matching
    |
    v
[Agent 2: Resolver-Assembler]
    |
    Hot cache (microseconds) → DB fallback (milliseconds)
    |                               |
    Found                       Not found
    |                               |
    |                    [Deterministic domain?]
    |                      Yes            No
    |                       |              |
    |                  "Not found      [Agent 3: Verifier]
    |                   = wrong"       (parallel, batched)
    |                       |              |
    v                       v              v
[Response with verification metadata]
    - Facts confirmed with source reference
    - Errors flagged with correction suggestions
    - Deprecated functions flagged with replacement
    - Parameter mismatches identified
```

## Knowledge Store

### Structure
SQLite database of structured triples:

```
subject:    wp_enqueue_script
predicate:  parameters
object:     (string) $handle, (string) $src = '', ...
status:     verified
source:     wp-includes/functions.wp-scripts.php
since:      2.1.0
```

### Triple types per function
- `type` — function, hook_action, hook_filter, class_method
- `parameters` — full parameter signature with types and defaults
- `parameter_count` — number of parameters
- `return_type` — return type annotation
- `description` — docblock description
- `deprecated_in` — version deprecated (if applicable)
- `replaced_by` — replacement function (if deprecated)

### Two categories
- **Verified:** confirmed by authoritative source. State as fact.
- **Contested:** qualified experts disagree. Present positions and evidence.

For the WordPress shard, all triples are verified — parsed directly from the WordPress source code repository.

## Gate (Agent 2) — How It Works

### Claim extraction
Context-aware pattern matching extracts WordPress function and hook references from any text:

- Recognises `wp_`, `get_`, `add_`, `register_`, `esc_`, `sanitize_` and other WordPress prefixes
- Catches function calls with parentheses
- Extracts hook names from `add_action`/`add_filter` calls
- Filters out false positives: capability strings, user-defined hooks, PHP magic methods, variable names

### Verification
Each extracted claim is checked against the hot cache (entire store loaded in memory):

1. **Found + active:** return verified with version, source file, parameters
2. **Found + deprecated:** return deprecated with version and replacement
3. **Not found:** return not found with fuzzy-matched suggestions (closest matches via difflib)

### Parameter comparison
Compares model-stated parameter signatures against stored correct signatures:

- Detects missing parameters
- Detects extra/wrong parameters
- Detects type mismatches
- Returns the correct signature from source

### Guardrails
- Input size limit: 50,000 characters
- Rate limiting: 100 requests/minute/IP
- Name sanitisation: strips injection attempts, special characters
- Search result cap: 50 results
- Field length limits on all inputs

## API Endpoints

```
POST /verify           — verify text for hallucinated functions
POST /compare_params   — compare stated params against correct
GET  /lookup/{name}    — look up a specific function
GET  /search/{prefix}  — search functions by prefix
GET  /stats            — store statistics
GET  /health           — health check
```

## Store Builder (Parser)

Parses WordPress source code from the GitHub repository:

1. Walks `wp-includes/` and `wp-admin/` directories
2. Extracts function declarations with their immediately preceding docblocks
3. Parses `@since`, `@deprecated`, `@param`, `@return`, `@see` annotations
4. Extracts `do_action()` and `apply_filters()` hook invocations
5. Generates structured triples and writes to SQLite

### Key parser features
- Strict docblock attribution: only matches the docblock immediately before a function declaration
- Handles tab-indented pluggable functions (inside `if (!function_exists())` blocks)
- Separates function docblocks from inline filter/hook docblocks
- Deduplicates hook invocations

### Rebuilding
```bash
git clone --depth 1 https://github.com/WordPress/WordPress.git wordpress-source
cd shards/wordpress
python3 parser.py ../../wordpress-source
```

Run after each WordPress release to keep the store current.

## Optimisations

- **Hot cache:** entire store loaded in memory at startup for microsecond lookups
- **Deterministic shortcutting:** Agent 3 skipped entirely for code/API domains
- **Context-aware extraction:** eliminates false positives at extraction time, not post-verification
- **Failure-mapped construction:** store built from known error patterns, not all knowledge
- **Streaming verification:** claims checked during generation, not after

## Domain Sharding

Each domain gets its own shard with its own parser and store:

```
shards/
├── wordpress/        # Built — 25,398 triples
│   ├── parser.py
│   ├── wordpress.db
│   └── summary.json
├── python/           # Planned
├── javascript/       # Planned
└── woocommerce/      # Planned
```

Same store schema across all shards. Same gate logic. Different parsers per source format.

## What VFault Does Not Do

- Understand whether a function is used correctly in context
- Catch logic or reasoning errors
- Cover plugin APIs (WooCommerce, ACF, etc.) without additional shards
- Replace code review or testing
- Work on non-deterministic domains without Agent 3
