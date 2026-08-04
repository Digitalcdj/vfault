#!/usr/bin/env python3
"""
VFault API — Agent 2: Resolver-Assembler
==========================================
FastAPI service that checks WordPress function/hook claims
against the verified triple store.

Takes text input (e.g. AI-generated code or advice),
extracts WordPress references, verifies each one,
and returns a detailed verification report.

Usage:
    python3 vfault.py                     # Run demo
    python3 vfault.py --demo              # Run demo
    python3 vfault.py --check "text..."   # CLI check mode
    python3 vfault.py --serve             # Start API server

API:
    POST /verify              {"text": "Use wp_register_scripts to add JS"}
    POST /compare_params      {"function": "wp_enqueue_script", "stated_params": "..."}
    GET  /lookup/{name}
    GET  /search/{prefix}
    GET  /stats
    GET  /health
"""

import re
import os
import sys
import json
import sqlite3
from difflib import get_close_matches


# ---------------------------------------------------------------------------
# Hot cache + store connection
# ---------------------------------------------------------------------------

SHARDS_DIR = os.environ.get('VFAULT_SHARDS_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shards')
)

# Legacy single-DB path for backwards compatibility
DB_PATH = os.environ.get('VFAULT_DB_PATH',
    os.path.join(SHARDS_DIR, 'wordpress', 'wordpress.db')
)

# In-memory hot cache: subject -> list of triples
HOT_CACHE = {}
LOADED_SHARDS = []


def load_hot_cache(conn):
    """Load triples from a single database connection into memory."""
    global HOT_CACHE
    c = conn.cursor()
    c.execute("SELECT subject, predicate, object, status, since_version, "
              "deprecated_version, replacement, source_file FROM triples")

    for row in c.fetchall():
        subject = row[0]
        if subject not in HOT_CACHE:
            HOT_CACHE[subject] = []
        HOT_CACHE[subject].append({
            'predicate': row[1],
            'object': row[2],
            'status': row[3],
            'since': row[4],
            'deprecated': row[5],
            'replacement': row[6],
            'source': row[7]
        })

    print(f"Hot cache loaded: {len(HOT_CACHE)} subjects, "
          f"{sum(len(v) for v in HOT_CACHE.values())} triples")


def load_all_shards():
    """Scan shards directory and load every .db file found."""
    global LOADED_SHARDS
    if not os.path.exists(SHARDS_DIR):
        # Fallback to single DB
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            load_hot_cache(conn)
            conn.close()
            LOADED_SHARDS.append('wordpress')
        return

    for shard_name in sorted(os.listdir(SHARDS_DIR)):
        shard_path = os.path.join(SHARDS_DIR, shard_name)
        if not os.path.isdir(shard_path):
            continue
        # Find .db files in this shard
        for fname in os.listdir(shard_path):
            if fname.endswith('.db'):
                db_file = os.path.join(shard_path, fname)
                print(f"Loading shard: {shard_name} ({fname})")
                conn = sqlite3.connect(db_file)
                load_hot_cache(conn)
                conn.close()
                LOADED_SHARDS.append(shard_name)

    if not LOADED_SHARDS:
        print("No shards found. Gate will report everything as not found.")


def get_all_subjects():
    """Get all known function/hook names for fuzzy matching."""
    return list(HOT_CACHE.keys())


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

# WordPress function name pattern
WP_FUNC_PATTERN = re.compile(
    r'\b((?:wp_|WP_|get_|set_|is_|has_|add_|remove_|do_|apply_|register_|'
    r'unregister_|delete_|update_|check_|create_|edit_|the_|have_|'
    r'sanitize_|esc_|wp_kses|absint|zeroise|human_|size_|number_|'
    r'__return_|current_user_|get_user_|wp_ajax_|admin_|comment_|'
    r'post_|term_|taxonomy_|nav_menu_|sidebar_|widget_|'
    r'shortcode_|media_|plugin_|theme_|block_|rest_)'
    r'[a-z_]{2,80})\b'
)

# Python dotted module references (os.path.join, json.dumps, etc.)
PY_DOTTED_PATTERN = re.compile(
    r'\b((?:os|sys|json|re|math|datetime|collections|pathlib|hashlib|'
    r'random|itertools|functools|typing|io|csv|sqlite3|http|urllib|'
    r'logging|threading|asyncio|subprocess|shutil|tempfile|pickle|'
    r'copy|pprint|string|textwrap|struct|enum|dataclasses|abc|'
    r'contextlib|decimal|fractions|statistics|secrets|hmac|base64|'
    r'email|html|xml|configparser|argparse|unittest|doctest|'
    r'socket|ssl|select|signal|queue|multiprocessing|'
    r'os\.path|collections\.abc|concurrent\.futures|'
    r'http\.client|http\.server|urllib\.parse|urllib\.request)'
    r'\.[\w.]{1,80})\b'
)

# JavaScript/Node dotted references (fs.readFile, JSON.parse, etc.)
JS_DOTTED_PATTERN = re.compile(
    r'\b((?:fs|path|http|https|crypto|url|util|os|net|dns|tls|'
    r'stream|buffer|events|child_process|cluster|zlib|readline|'
    r'vm|worker_threads|http2|dgram|perf_hooks|querystring|'
    r'JSON|Math|Array|Object|String|Number|Promise|Buffer|'
    r'RegExp|Map|Set|Date|Error|console|process|'
    r'Array\.prototype|String\.prototype|Object)'
    r'\.[\w.]{1,80})\b'
)

# Laravel helper functions and facade calls
LARAVEL_HELPERS_SET = {
    'abort', 'abort_if', 'abort_unless', 'app', 'auth', 'back',
    'bcrypt', 'blank', 'broadcast', 'cache', 'class_basename',
    'collect', 'config', 'cookie', 'csrf_field', 'csrf_token',
    'dd', 'decrypt', 'dispatch', 'dump', 'encrypt', 'env',
    'event', 'fake', 'filled', 'info', 'logger', 'method_field',
    'now', 'old', 'optional', 'policy', 'redirect', 'report',
    'request', 'rescue', 'resolve', 'response', 'retry', 'route',
    'session', 'storage_path', 'tap', 'throw_if', 'throw_unless',
    'today', 'trans', 'trans_choice', 'url', 'validator', 'value',
    'view', 'with', 'app_path', 'base_path', 'config_path',
    'database_path', 'lang_path', 'public_path', 'resource_path',
    'asset', 'secure_asset', 'secure_url', 'to_route', 'action',
    'str', 'e', 'head', 'last', 'data_get', 'data_set',
    'data_fill', 'data_forget', 'transform',
}

# Also catch function calls with parentheses
WP_CALL_PATTERN = re.compile(
    r'\b([a-z_]{3,80})\s*\('
)

# Hook name patterns (in add_action/add_filter calls)
HOOK_REF_PATTERN = re.compile(
    r"(?:add_action|add_filter|remove_action|remove_filter|has_action|has_filter"
    r"|do_action|apply_filters)\s*\(\s*['\"]([a-z_\-/{}]+)['\"]"
)

# Patterns to EXCLUDE — these are not function calls
CAPABILITY_PATTERN = re.compile(
    r"(?:current_user_can|user_can|'capability_type'|'capabilities'|'capability')\s*"
    r"(?:\(|=>)\s*(?:array\s*\()?\s*['\"]"
)

# Known user-defined patterns to skip
SKIP_PREFIXES = ('my_', 'custom_')
SKIP_EXACT = {
    '__construct', 'wp_query',
    'post_id', 'post_type', 'has_archive',
    'post_title', 'post_status', 'post_content', 'post_author',
    'post_date', 'post_name', 'post_parent', 'post_excerpt',
    'post_modified', 'comment_status', 'ping_status', 'post_password',
    'menu_order', 'post_mime_type', 'comment_count',
}

# Known capability strings (used as values, not functions)
CAPABILITY_STRINGS = {
    'edit_posts', 'edit_others_posts', 'publish_posts', 'read_private_posts',
    'delete_posts', 'delete_others_posts', 'delete_published_posts',
    'edit_published_posts', 'manage_options', 'manage_categories',
    'moderate_comments', 'upload_files', 'edit_pages', 'read',
    'edit_post', 'read_post', 'delete_post',
}


def is_inside_capability_context(text, match_start):
    """Check if a match position is inside a capabilities array or current_user_can call."""
    if match_start < 0:
        return False
    lookback = text[max(0, match_start - 500):match_start]
    if "'capabilities'" in lookback or "'capability_type'" in lookback:
        return True
    # Also catch custom capability values like edit_book, delete_book etc
    # These follow the pattern 'core_cap' => 'custom_cap'
    line_start = text.rfind('\n', 0, match_start)
    line = text[line_start:match_start + 50] if line_start >= 0 else text[:match_start + 50]
    if "=>" in line and any(cap in line for cap in CAPABILITY_STRINGS):
        return True
    return False


def is_user_defined_hook(name, text):
    """Check if a hook name is user-defined (my_hourly_event, wp_ajax_my_action, etc.)."""
    if name.startswith(SKIP_PREFIXES):
        return True
    if name.startswith('wp_ajax_') and not name.startswith('wp_ajax_nopriv_'):
        # wp_ajax_{action} is always user-defined
        return True
    if name.startswith('wp_ajax_nopriv_'):
        return True
    return False


def extract_claims(text):
    """Extract function and API references from text across all loaded shards.
    Covers WordPress, Python, JavaScript/Node, and Laravel patterns."""
    claims = set()

    # WordPress function name patterns
    for match in WP_FUNC_PATTERN.finditer(text):
        name = match.group(1)
        if name in SKIP_EXACT:
            continue
        if name.startswith(SKIP_PREFIXES):
            continue
        if is_user_defined_hook(name, text):
            continue
        claims.add(name)

    # Python dotted references (os.path.join, json.dumps, etc.)
    for match in PY_DOTTED_PATTERN.finditer(text):
        claims.add(match.group(1))

    # JavaScript/Node dotted references (fs.readFile, JSON.parse, etc.)
    for match in JS_DOTTED_PATTERN.finditer(text):
        name = match.group(1)
        # Skip environment variable access (process.env.*)
        if name.startswith('process.env'):
            continue
        claims.add(name)

    # Laravel and general function calls with parentheses
    for match in re.finditer(r'\b([a-z_]{2,80})\s*\(', text):
        name = match.group(1)
        if name in SKIP_EXACT:
            continue
        if name.startswith(SKIP_PREFIXES):
            continue
        if name in LARAVEL_HELPERS_SET:
            claims.add(name)
        elif (name.startswith(('wp_', 'get_', 'set_', 'is_', 'has_', 'add_',
                            'remove_', 'the_', 'esc_', 'sanitize_',
                            'register_', 'unregister_', 'delete_',
                            'update_', 'check_', 'create_', 'do_',
                            'apply_', 'current_user_'))
            or name in HOT_CACHE):
            claims.add(name)

    # Hook references (from add_action/add_filter calls)
    for match in HOOK_REF_PATTERN.finditer(text):
        name = match.group(1)
        if name.startswith(SKIP_PREFIXES):
            continue
        if is_user_defined_hook(name, text):
            continue
        claims.add(name)

    # Filter out capability strings used as values
    # Check each claim — if it's a known capability AND appears in a capability context
    filtered = set()
    for name in claims:
        # Check for custom capability strings (edit_book, delete_book etc)
        # These appear inside 'capabilities' => array(...) blocks
        if is_inside_capability_context(text, text.find(name)):
            continue
        # Known WordPress capability strings used as function args
        if name in CAPABILITY_STRINGS and f"current_user_can( '{name}'" in text:
            # This is fine — current_user_can is the function, the string is the arg
            # We want to verify current_user_can, not the capability string
            continue
        if name in CAPABILITY_STRINGS and name not in HOT_CACHE:
            continue
        filtered.add(name)

    return list(filtered)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_claim(name):
    """Verify a single function/hook name against the store."""
    # Hot cache lookup (microseconds)
    if name in HOT_CACHE:
        triples = HOT_CACHE[name]

        type_triple = next((t for t in triples if t['predicate'] == 'type'), None)
        dep_triple = next((t for t in triples if t['predicate'] == 'deprecated_in'), None)
        rep_triple = next((t for t in triples if t['predicate'] == 'replaced_by'), None)
        param_triple = next((t for t in triples if t['predicate'] == 'parameters'), None)
        desc_triple = next((t for t in triples if t['predicate'] == 'description'), None)
        since_triple = next((t for t in triples if t['predicate'] == 'type' and t['since']), None)
        ret_triple = next((t for t in triples if t['predicate'] == 'return_type'), None)

        result = {
            'name': name,
            'exists': True,
            'type': type_triple['object'] if type_triple else 'unknown',
            'since': since_triple['since'] if since_triple else None,
            'source': type_triple['source'] if type_triple else None,
        }

        if dep_triple:
            result['status'] = 'deprecated'
            result['deprecated_in'] = dep_triple['object']
            result['replacement'] = rep_triple['object'] if rep_triple else None
            result['message'] = (
                f"DEPRECATED in {dep_triple['object']}."
                + (f" Use {rep_triple['object']} instead." if rep_triple else "")
            )
        else:
            result['status'] = 'verified'
            result['message'] = 'Verified — exists in WordPress core.'

        if param_triple:
            result['parameters'] = param_triple['object']
        if desc_triple:
            result['description'] = desc_triple['object']
        if ret_triple:
            result['return_type'] = ret_triple['object']

        return result

    # Not found — hallucination or unknown
    # Multi-tier suggestion system:
    # 1. Fuzzy match (close spelling)
    # 2. Namespace/module match (same module, different function)
    # 3. Cross-language equivalents (json.stringify -> json.dumps)

    all_subjects = get_all_subjects()
    suggestions = []

    # Tier 1: Fuzzy match (close spelling — typos, plurals, underscores)
    close = get_close_matches(name, all_subjects, n=5, cutoff=0.7)
    suggestions.extend(close)

    # Tier 2: Namespace match — same module/prefix, list available functions
    if '.' in name:
        # Dotted name: get the module part and find siblings
        parts = name.rsplit('.', 1)
        module_prefix = parts[0] + '.'
        func_part = parts[1].lower()
        siblings = [s for s in all_subjects
                    if s.startswith(module_prefix) and s not in suggestions]
        # Rank siblings by relevance to the function name
        scored = []
        for sib in siblings:
            sib_func = sib.rsplit('.', 1)[-1].lower()
            # Simple relevance: shared characters / length
            common = sum(1 for c in func_part if c in sib_func)
            score = common / max(len(func_part), len(sib_func), 1)
            scored.append((score, sib))
        scored.sort(reverse=True)
        for score, sib in scored[:3]:
            if sib not in suggestions:
                suggestions.append(sib)
    elif '_' in name:
        # Underscore name (WordPress style): match prefix
        prefix = name.split('_')[0] + '_'
        siblings = [s for s in all_subjects
                    if s.startswith(prefix) and s not in suggestions]
        close_siblings = get_close_matches(name, siblings, n=3, cutoff=0.6)
        for s in close_siblings:
            if s not in suggestions:
                suggestions.append(s)

    # Tier 3: Cross-language equivalents
    CROSS_LANG_MAP = {
        'json.stringify': ['json.dumps'],
        'json.parse': ['json.loads'],
        'JSON.load': ['JSON.parse'],
        'JSON.loads': ['JSON.parse'],
        'JSON.dump': ['JSON.stringify'],
        'JSON.dumps': ['JSON.stringify'],
        'math.square_root': ['math.sqrt', 'Math.sqrt'],
        'math.squareRoot': ['Math.sqrt', 'math.sqrt'],
        'Math.square_root': ['Math.sqrt'],
        'os.read_file': ['os.read', 'open'],
        'fs.readFilePromise': ['fs.readFile', 'fs.promises.readFile'],
        'fs.writeJSON': ['fs.writeFile', 'JSON.stringify'],
        'http.createWebServer': ['http.createServer'],
        'collections.HashMap': ['collections.OrderedDict', 'dict'],
        'Array.prototype.flat_map': ['Array.prototype.flatMap'],
        'Buffer.fromString': ['Buffer.from'],
        'render_template': ['view'],
        'make_response': ['response'],
        'dump_die': ['dd'],
        'collection': ['collect'],
        'get_config': ['config'],
    }

    cross = CROSS_LANG_MAP.get(name, [])
    for equiv in cross:
        if equiv in HOT_CACHE and equiv not in suggestions:
            suggestions.insert(0, equiv)  # Cross-language matches go first

    # Deduplicate and limit
    seen = set()
    unique = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    suggestions = unique[:5]

    result = {
        'name': name,
        'exists': False,
        'status': 'not_found',
        'message': f"NOT FOUND. This may be a hallucination.",
    }

    if suggestions:
        result['suggestions'] = suggestions
        result['message'] += f" Did you mean: {', '.join(suggestions[:3])}?"

    return result


def compare_params(func_name, stated_params_text):
    """Compare model-stated parameters against stored correct parameters.
    Returns a dict with match status and details."""
    if func_name not in HOT_CACHE:
        return None

    triples = HOT_CACHE[func_name]
    param_triple = next((t for t in triples if t['predicate'] == 'parameters'), None)

    if not param_triple:
        return None

    stored_params = param_triple['object']

    # Extract parameter names from stored signature
    stored_names = re.findall(r'\$(\w+)', stored_params)
    stated_names = re.findall(r'\$(\w+)', stated_params_text)

    # Check for name mismatches
    missing = [n for n in stored_names if n not in stated_names]
    extra = [n for n in stated_names if n not in stored_names]

    # Check for type mismatches by comparing type annotations
    stored_types = {}
    for m in re.finditer(r'\(([^)]+)\)\s*\$(\w+)', stored_params):
        stored_types[m.group(2)] = m.group(1)

    stated_types = {}
    for m in re.finditer(r'\(([^)]+)\)\s*\$(\w+)', stated_params_text):
        stated_types[m.group(2)] = m.group(1)

    # Also handle "type $name" format (no parentheses)
    for m in re.finditer(r'(\w[\w|\\?]*)\s+\$(\w+)', stated_params_text):
        if m.group(2) not in stated_types:
            stated_types[m.group(2)] = m.group(1)

    type_mismatches = []
    for param_name, stored_type in stored_types.items():
        if param_name in stated_types:
            stated_type = stated_types[param_name]
            # Normalize for comparison
            s_norm = stored_type.replace(' ', '').lower()
            t_norm = stated_type.replace(' ', '').lower()
            if s_norm != t_norm:
                type_mismatches.append({
                    'param': param_name,
                    'stored_type': stored_type,
                    'stated_type': stated_type
                })

    result = {
        'function': func_name,
        'stored_params': stored_params,
        'stated_params': stated_params_text,
        'param_match': len(missing) == 0 and len(extra) == 0,
        'type_match': len(type_mismatches) == 0,
        'missing_params': missing,
        'extra_params': extra,
        'type_mismatches': type_mismatches,
    }

    if missing or extra or type_mismatches:
        issues = []
        if missing:
            issues.append(f"Missing params: {', '.join('$' + n for n in missing)}")
        if extra:
            issues.append(f"Extra/wrong params: {', '.join('$' + n for n in extra)}")
        for tm in type_mismatches:
            issues.append(
                f"${tm['param']}: stated '{tm['stated_type']}' "
                f"but actual is '{tm['stored_type']}'"
            )
        result['status'] = 'param_mismatch'
        result['message'] = 'PARAMETER MISMATCH: ' + '; '.join(issues)
    else:
        result['status'] = 'params_verified'
        result['message'] = 'Parameters match stored signature.'

    return result


def verify_text(text):
    """Extract and verify all WordPress claims in a block of text."""
    claims = extract_claims(text)

    results = {
        'input_length': len(text),
        'claims_found': len(claims),
        'verified': [],
        'deprecated': [],
        'not_found': [],
        'summary': {}
    }

    for claim in sorted(claims):
        v = verify_claim(claim)

        if v['status'] == 'verified':
            results['verified'].append(v)
        elif v['status'] == 'deprecated':
            results['deprecated'].append(v)
        elif v['status'] == 'not_found':
            results['not_found'].append(v)

    results['summary'] = {
        'total_claims': len(claims),
        'verified': len(results['verified']),
        'deprecated': len(results['deprecated']),
        'not_found': len(results['not_found']),
        'hallucination_rate': (
            f"{len(results['not_found']) / len(claims) * 100:.1f}%"
            if claims else "0%"
        )
    }

    return results


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

def create_app():
    from fastapi import FastAPI, HTTPException, Request
    from pydantic import BaseModel, Field
    import time

    MAX_INPUT_LENGTH = 50000
    RATE_LIMIT_PER_MINUTE = 100
    rate_limit_store = {}

    app = FastAPI(
        title="VFault — WordPress Shard",
        description="VFault: Verification at the fault lines. Agent 2 Resolver-Assembler.",
        version="0.2.0"
    )

    def check_rate_limit(request: Request):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_start = now - 60

        if client_ip not in rate_limit_store:
            rate_limit_store[client_ip] = []

        rate_limit_store[client_ip] = [
            t for t in rate_limit_store[client_ip] if t > window_start
        ]

        if len(rate_limit_store[client_ip]) >= RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Max 100 requests per minute."
            )

        rate_limit_store[client_ip].append(now)

    def sanitise_name(name: str) -> str:
        """Strip anything that isn't a valid PHP function/hook character."""
        import re
        return re.sub(r'[^a-zA-Z0-9_\-/{}]', '', name)[:200]

    class VerifyRequest(BaseModel):
        text: str = Field(..., max_length=MAX_INPUT_LENGTH)

    @app.on_event("startup")
    def startup():
        load_all_shards()

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "shards_loaded": LOADED_SHARDS,
            "subjects_cached": len(HOT_CACHE),
            "triples_cached": sum(len(v) for v in HOT_CACHE.values())
        }

    @app.post("/verify")
    def verify(req: VerifyRequest, request: Request):
        check_rate_limit(request)
        if len(req.text) > MAX_INPUT_LENGTH:
            raise HTTPException(
                status_code=413,
                detail=f"Input too large. Max {MAX_INPUT_LENGTH} characters."
            )
        return verify_text(req.text)

    @app.get("/lookup/{name}")
    def lookup(name: str, request: Request):
        check_rate_limit(request)
        clean_name = sanitise_name(name)
        if not clean_name:
            raise HTTPException(status_code=400, detail="Invalid function name.")
        return verify_claim(clean_name)

    @app.get("/search/{prefix}")
    def search(prefix: str, request: Request, limit: int = 20):
        check_rate_limit(request)
        clean_prefix = sanitise_name(prefix)
        if not clean_prefix:
            raise HTTPException(status_code=400, detail="Invalid search prefix.")
        limit = min(limit, 50)
        matches = [s for s in HOT_CACHE.keys()
                   if s.startswith(clean_prefix)][:limit]
        return {"prefix": clean_prefix, "matches": matches, "count": len(matches)}

    class ParamCompareRequest(BaseModel):
        function: str = Field(..., max_length=200)
        stated_params: str = Field(..., max_length=2000)

    @app.post("/compare_params")
    def compare(req: ParamCompareRequest, request: Request):
        check_rate_limit(request)
        clean_func = sanitise_name(req.function)
        if not clean_func:
            raise HTTPException(status_code=400, detail="Invalid function name.")
        result = compare_params(clean_func, req.stated_params)
        if result is None:
            return {"error": f"Function '{clean_func}' not found or has no parameters"}
        return result

    @app.get("/stats")
    def stats(request: Request):
        check_rate_limit(request)
        functions = sum(1 for s, triples in HOT_CACHE.items()
                       if any(t['predicate'] == 'type' and t['object'] == 'function' for t in triples))
        deprecated = sum(1 for s, triples in HOT_CACHE.items()
                        if any(t['predicate'] == 'deprecated_in' for t in triples))
        hooks = sum(1 for s, triples in HOT_CACHE.items()
                   if any(t['predicate'] == 'type' and t['object'].startswith('hook_') for t in triples))
        methods = sum(1 for s, triples in HOT_CACHE.items()
                     if any(t['predicate'] == 'type' and t['object'] == 'class_method' for t in triples))
        return {
            "subjects": len(HOT_CACHE),
            "triples": sum(len(v) for v in HOT_CACHE.values()),
            "functions": functions,
            "deprecated": deprecated,
            "hooks": hooks,
            "class_methods": methods,
            "database": DB_PATH
        }

    return app


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

def cli_check(text):
    """Run verification from command line."""
    load_all_shards()

    results = verify_text(text)

    print()
    print("=" * 60)
    print("VFAULT VERIFICATION REPORT")
    print("=" * 60)
    print()
    print(f"Claims found: {results['summary']['total_claims']}")
    print(f"Verified:     {results['summary']['verified']}")
    print(f"Deprecated:   {results['summary']['deprecated']}")
    print(f"Not found:    {results['summary']['not_found']}")
    print(f"Hallucination rate: {results['summary']['hallucination_rate']}")

    if results['verified']:
        print()
        print("VERIFIED")
        print("-" * 40)
        for v in results['verified']:
            since = f" (since {v['since']})" if v.get('since') else ""
            print(f"  {v['name']}{since}")
            if v.get('parameters'):
                print(f"    params: {v['parameters']}")

    if results['deprecated']:
        print()
        print("DEPRECATED")
        print("-" * 40)
        for v in results['deprecated']:
            print(f"  {v['name']} — {v['message']}")

    if results['not_found']:
        print()
        print("NOT FOUND (possible hallucinations)")
        print("-" * 40)
        for v in results['not_found']:
            print(f"  {v['name']}")
            if v.get('suggestions'):
                print(f"    suggestions: {', '.join(v['suggestions'])}")

    print()
    return results


def demo():
    """Run a demo with sample AI-generated text."""
    sample = """
    To add custom scripts in WordPress, use the wp_enqueue_scripts hook
    with add_action. Inside your callback, call wp_register_scripts()
    to register your JavaScript file, then wp_enqueue_script() to load it.

    For styles, use wp_enqueue_style() and wp_register_style().

    You can check if a user is logged in with is_user_logged_in()
    and get their data with get_currentuserinfo().

    To add a meta box, use add_meta_boxes action hook and
    the add_meta_box() function.

    For custom post types, call register_post_type() in your
    init hook callback.
    """

    print()
    print("=" * 60)
    print("VFAULT DEMO — checking AI-generated WordPress advice")
    print("=" * 60)
    print()
    print("INPUT TEXT:")
    print(sample)

    return cli_check(sample)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        text = ' '.join(sys.argv[2:])
        cli_check(text)
    elif len(sys.argv) > 1 and sys.argv[1] == '--demo':
        demo()
    elif len(sys.argv) > 1 and sys.argv[1] == '--serve':
        import uvicorn
        app = create_app()
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        demo()
