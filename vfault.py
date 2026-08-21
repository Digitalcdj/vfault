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
import time
import hmac
import hashlib
import secrets
import sqlite3
from difflib import get_close_matches
from datetime import datetime, timezone


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

# In-memory hot cache: subject -> list of triples (each tagged with domain)
HOT_CACHE = {}
LOADED_SHARDS = []

# Shard access control
FREE_SHARDS = {'wordpress'}
PAID_SHARDS = {'woocommerce', 'python', 'javascript', 'laravel', 'react'}
ALL_SHARDS = FREE_SHARDS | PAID_SHARDS

# Namespace patterns: if a name matches any of these, it *should* be in a shard.
# Names that don't match any pattern are likely private/custom code -> "unknown".
SHARD_NAMESPACE_PREFIXES = (
    'wp_', 'WP_', 'wc_', 'get_', 'set_', 'is_', 'has_', 'add_', 'remove_',
    'do_', 'apply_', 'register_', 'unregister_', 'delete_', 'update_',
    'check_', 'create_', 'edit_', 'the_', 'have_', 'sanitize_', 'esc_',
    'current_user_', 'get_user_', 'wp_ajax_', 'admin_', 'comment_',
    'post_', 'term_', 'taxonomy_', 'nav_menu_', 'sidebar_', 'widget_',
    'shortcode_', 'media_', 'plugin_', 'theme_', 'block_', 'rest_',
    'woocommerce_',
)

SHARD_NAMESPACE_DOTTED_MODULES = (
    'os.', 'sys.', 'json.', 're.', 'math.', 'datetime.', 'collections.',
    'pathlib.', 'hashlib.', 'random.', 'itertools.', 'functools.', 'typing.',
    'io.', 'csv.', 'sqlite3.', 'http.', 'urllib.', 'logging.', 'threading.',
    'asyncio.', 'subprocess.', 'shutil.', 'tempfile.', 'pickle.', 'copy.',
    'pprint.', 'string.', 'textwrap.', 'struct.', 'enum.', 'dataclasses.',
    'abc.', 'contextlib.', 'decimal.', 'fractions.', 'statistics.', 'secrets.',
    'hmac.', 'base64.', 'email.', 'html.', 'xml.', 'configparser.', 'argparse.',
    'unittest.', 'doctest.', 'socket.', 'ssl.', 'select.', 'signal.', 'queue.',
    'multiprocessing.', 'concurrent.',
    'fs.', 'path.', 'crypto.', 'url.', 'util.', 'net.', 'dns.', 'tls.',
    'stream.', 'buffer.', 'events.', 'child_process.', 'cluster.', 'zlib.',
    'readline.', 'vm.', 'worker_threads.', 'http2.', 'dgram.', 'perf_hooks.',
    'querystring.', 'JSON.', 'Math.', 'Array.', 'Object.', 'String.',
    'Number.', 'Promise.', 'Buffer.', 'RegExp.', 'Map.', 'Set.', 'Date.',
    'Error.', 'console.', 'process.',
    'ReactDOM.', 'ReactDOMServer.', 'NextResponse.', 'NextRequest.',
)

SHARD_REACT_HOOK_PATTERN = re.compile(r'^use[A-Z]')


def is_shard_namespace(name):
    """Check if a name looks like it belongs to a known shard namespace.
    Returns True if the name should be in a shard (and not_found means hallucination).
    Returns False if the name is likely private/custom code (unknown, not hallucination)."""
    # WordPress/WooCommerce/PHP prefixed functions
    if any(name.startswith(p) for p in SHARD_NAMESPACE_PREFIXES):
        return True
    # Python/JS dotted module references
    if any(name.startswith(m) for m in SHARD_NAMESPACE_DOTTED_MODULES):
        return True
    # React hooks: use[A-Z]... could be custom hooks (useShoppingCart, useAuth)
    # so they go to "unknown" not "not_found" — custom hooks are expected
    # Laravel helpers
    if name in LARAVEL_HELPERS_SET:
        return True
    # Class.method patterns from known shard classes
    if '.' in name:
        class_part = name.split('.')[0]
        # Known shard class prefixes
        if class_part.startswith(('WP_', 'WC_', 'Illuminate')):
            return True
    return False

def get_allowed_shards(plan):
    """Return the set of shard domains a plan can access."""
    if plan in ('pro', 'team', 'business', 'enterprise'):
        return ALL_SHARDS
    return FREE_SHARDS


def load_hot_cache(conn, domain=None):
    """Load triples from a single database connection into memory."""
    global HOT_CACHE
    c = conn.cursor()
    c.execute("SELECT subject, predicate, object, status, since_version, "
              "deprecated_version, replacement, source_file, domain FROM triples")

    count = 0
    for row in c.fetchall():
        subject = row[0]
        triple_domain = row[8] or domain or 'unknown'
        if subject not in HOT_CACHE:
            HOT_CACHE[subject] = []
        HOT_CACHE[subject].append({
            'predicate': row[1],
            'object': row[2],
            'status': row[3],
            'since': row[4],
            'deprecated': row[5],
            'replacement': row[6],
            'source': row[7],
            'domain': triple_domain,
        })
        count += 1

    print(f"  Loaded {count} triples ({domain or 'unknown'}) -> "
          f"{len(HOT_CACHE)} total subjects")


def load_all_shards():
    """Scan shards directory and load every .db file found."""
    global LOADED_SHARDS
    if not os.path.exists(SHARDS_DIR):
        # Fallback to single DB
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            load_hot_cache(conn, domain='wordpress')
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
                load_hot_cache(conn, domain=shard_name)
                conn.close()
                LOADED_SHARDS.append(shard_name)

    if not LOADED_SHARDS:
        print("No shards found. Gate will report everything as not found.")
    else:
        print(f"All shards loaded: {LOADED_SHARDS}")
        print(f"Hot cache: {len(HOT_CACHE)} subjects, "
              f"{sum(len(v) for v in HOT_CACHE.values())} triples")


def get_all_subjects(allowed_shards=None):
    """Get all known function/hook names, optionally filtered by allowed shards."""
    if allowed_shards is None:
        return list(HOT_CACHE.keys())
    return [
        s for s, triples in HOT_CACHE.items()
        if any(t['domain'] in allowed_shards for t in triples)
    ]


def get_triples(subject, allowed_shards=None):
    """Get triples for a subject, filtered by allowed shards."""
    triples = HOT_CACHE.get(subject, [])
    if allowed_shards is None:
        return triples
    return [t for t in triples if t['domain'] in allowed_shards]


# ---------------------------------------------------------------------------
# API Key management
# ---------------------------------------------------------------------------

KEYS_DB_PATH = os.environ.get('VFAULT_KEYS_DB',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vfault_keys.db')
)

# Plan limits: requests per day
PLAN_LIMITS = {
    'free':       100,
    'pro':        5000,
    'team':       15000,
    'business':   50000,
    'enterprise': 100000,
}

# Per-minute burst limits
PLAN_BURST = {
    'free':       15,
    'pro':        60,
    'team':       90,
    'business':   200,
    'enterprise': 300,
}

# Lemon Squeezy webhook secret (set via environment variable)
LEMONSQUEEZY_SECRET = os.environ.get('LEMONSQUEEZY_WEBHOOK_SECRET', '')


def init_keys_db():
    """Create the API keys table if it doesn't exist."""
    conn = sqlite3.connect(KEYS_DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS api_keys (
        api_key     TEXT PRIMARY KEY,
        email       TEXT NOT NULL,
        plan        TEXT NOT NULL DEFAULT 'pro',
        status      TEXT NOT NULL DEFAULT 'active',
        created_at  TEXT NOT NULL,
        ls_customer_id  TEXT,
        ls_subscription_id TEXT,
        note        TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS usage_log (
        api_key     TEXT NOT NULL,
        date        TEXT NOT NULL,
        count       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (api_key, date)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_keys_email ON api_keys(email)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_keys_status ON api_keys(status)')
    conn.commit()
    conn.close()
    print(f"API keys database ready: {KEYS_DB_PATH}")


def generate_api_key(plan='pro'):
    """Generate a secure API key in vf_{plan}_{random} format."""
    random_part = secrets.token_hex(16)
    return f"vf_{plan}_{random_part}"


def create_api_key(email, plan='pro', ls_customer_id=None, ls_subscription_id=None, note=None):
    """Create and store a new API key."""
    api_key = generate_api_key(plan)
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(KEYS_DB_PATH)
    c = conn.cursor()
    c.execute(
        'INSERT INTO api_keys (api_key, email, plan, status, created_at, '
        'ls_customer_id, ls_subscription_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (api_key, email, plan, 'active', now, ls_customer_id, ls_subscription_id, note)
    )
    conn.commit()
    conn.close()
    return api_key


def validate_api_key(api_key):
    """Look up an API key. Returns dict with plan info or None."""
    conn = sqlite3.connect(KEYS_DB_PATH)
    c = conn.cursor()
    c.execute(
        'SELECT api_key, email, plan, status FROM api_keys WHERE api_key = ?',
        (api_key,)
    )
    row = c.fetchone()
    conn.close()

    if not row:
        return None
    if row[3] != 'active':
        return None

    return {'api_key': row[0], 'email': row[1], 'plan': row[2], 'status': row[3]}


def get_daily_usage(user_id):
    """Get today's request count for a user (API key or IP)."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    conn = sqlite3.connect(KEYS_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT count FROM usage_log WHERE api_key = ? AND date = ?', (user_id, today))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def increment_usage(user_id):
    """Increment today's request count for a user."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    conn = sqlite3.connect(KEYS_DB_PATH)
    c = conn.cursor()
    c.execute(
        'INSERT INTO usage_log (api_key, date, count) VALUES (?, ?, 1) '
        'ON CONFLICT(api_key, date) DO UPDATE SET count = count + 1',
        (user_id, today)
    )
    conn.commit()
    conn.close()


def verify_lemonsqueezy_signature(payload_bytes, signature):
    """Verify Lemon Squeezy webhook signature."""
    if not LEMONSQUEEZY_SECRET:
        return False
    expected = hmac.new(
        key=LEMONSQUEEZY_SECRET.encode(),
        msg=payload_bytes,
        digestmod=hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Email sending (via Brevo SMTP)
# ---------------------------------------------------------------------------

BREVO_SMTP_HOST = os.environ.get('BREVO_SMTP_HOST', 'smtp-relay.brevo.com')
BREVO_SMTP_PORT = int(os.environ.get('BREVO_SMTP_PORT', '587'))
BREVO_SMTP_USER = os.environ.get('BREVO_SMTP_USER', '')
BREVO_SMTP_PASS = os.environ.get('BREVO_SMTP_PASS', '')
VFAULT_FROM_EMAIL = os.environ.get('VFAULT_FROM_EMAIL', 'vfault@exceedwebservices.com')
VFAULT_FROM_NAME = os.environ.get('VFAULT_FROM_NAME', 'VFault')


def send_api_key_email(to_email, api_key, plan='pro'):
    """Send the API key to the customer via Brevo SMTP."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not BREVO_SMTP_USER or not BREVO_SMTP_PASS:
        print(f"[VFault] SMTP not configured — cannot email {to_email}")
        return False

    subject = "Your VFault API Key"

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0c0c0b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;">
<div style="max-width:600px;margin:0 auto;padding:40px 24px;">

<div style="height:3px;background:linear-gradient(90deg,#1baf7a,#0a7a52);margin-bottom:32px;border-radius:2px;"></div>

<div style="font-size:20px;font-weight:500;color:#e8e6df;margin-bottom:32px;">
<span style="color:#1baf7a;">V</span>Fault
</div>

<h1 style="font-size:24px;font-weight:600;color:#e8e6df;margin:0 0 16px;">Welcome to VFault {plan.title()}</h1>

<p style="font-size:16px;color:#c3c2b7;line-height:1.6;margin:0 0 24px;">
Your API key is ready. Use it to access all paid shards across WordPress, WooCommerce, Python, JavaScript, Laravel, and React.
</p>

<div style="background:#1a1a19;border:1px solid #2c2c2a;border-radius:8px;padding:20px;margin:0 0 24px;">
<div style="font-size:12px;color:#9a9890;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Your API Key</div>
<div style="font-size:16px;font-family:ui-monospace,'Cascadia Mono','SF Mono',Consolas,monospace;color:#1baf7a;word-break:break-all;">{api_key}</div>
</div>

<h2 style="font-size:18px;font-weight:500;color:#e8e6df;margin:0 0 12px;">Getting started</h2>

<p style="font-size:14px;color:#c3c2b7;line-height:1.7;margin:0 0 8px;">
<strong style="color:#e8e6df;">API requests:</strong> Include your key in the X-API-Key header:
</p>

<div style="background:#1a1a19;border:1px solid #2c2c2a;border-radius:8px;padding:16px;margin:0 0 24px;font-size:13px;font-family:ui-monospace,Consolas,monospace;color:#9a9890;overflow-x:auto;">
curl -H "X-API-Key: {api_key}" \\<br>
&nbsp;&nbsp;https://exceedweb.pythonanywhere.com/verify \\<br>
&nbsp;&nbsp;-X POST -H "Content-Type: application/json" \\<br>
&nbsp;&nbsp;-d '{{"text": "your code here"}}'
</div>

<p style="font-size:14px;color:#c3c2b7;line-height:1.7;margin:0 0 8px;">
<strong style="color:#e8e6df;">VS Code extension:</strong> Add the key to your VS Code settings under <span style="font-family:monospace;color:#1baf7a;">vfault.apiKey</span>.
</p>

<p style="font-size:14px;color:#c3c2b7;line-height:1.7;margin:0 0 24px;">
<strong style="color:#e8e6df;">Check usage:</strong> Visit <a href="https://exceedweb.pythonanywhere.com/usage" style="color:#1baf7a;">exceedweb.pythonanywhere.com/usage</a> with your key to see remaining requests.
</p>

<h2 style="font-size:18px;font-weight:500;color:#e8e6df;margin:0 0 12px;">Your plan</h2>

<div style="background:#1a1a19;border:1px solid #2c2c2a;border-radius:8px;padding:16px;margin:0 0 32px;">
<table style="width:100%;font-size:14px;border-collapse:collapse;">
<tr><td style="padding:6px 0;color:#9a9890;">Plan</td><td style="padding:6px 0;color:#e8e6df;">{plan.title()}</td></tr>
<tr><td style="padding:6px 0;color:#9a9890;">Daily requests</td><td style="padding:6px 0;color:#e8e6df;">5,000</td></tr>
<tr><td style="padding:6px 0;color:#9a9890;">Shards</td><td style="padding:6px 0;color:#e8e6df;">All (WordPress, WooCommerce, Python, JavaScript, Laravel, React)</td></tr>
<tr><td style="padding:6px 0;color:#9a9890;">Support</td><td style="padding:6px 0;color:#e8e6df;">Priority email</td></tr>
</table>
</div>

<p style="font-size:14px;color:#c3c2b7;line-height:1.7;margin:0 0 8px;">
Full documentation at <a href="https://vfault.com/docs.html" style="color:#1baf7a;">vfault.com/docs.html</a>
</p>

<p style="font-size:14px;color:#c3c2b7;line-height:1.7;margin:0 0 32px;">
Questions? Reply to this email or contact <a href="mailto:vfault@exceedwebservices.com" style="color:#1baf7a;">vfault@exceedwebservices.com</a>
</p>

<div style="border-top:1px solid #2c2c2a;padding-top:24px;font-size:12px;color:#9a9890;">
VFault — Verification at the fault lines<br>
Built by <a href="https://www.exceedwebservices.com" style="color:#9a9890;">Ian Fraser — Exceed Web Services</a> · Ceredigion, Wales
</div>

</div>
</body>
</html>"""

    plain_body = f"""Welcome to VFault {plan.title()}

Your API Key: {api_key}

Include it in the X-API-Key header on all API requests.

Plan: {plan.title()}
Daily requests: 5,000
Shards: All (WordPress, WooCommerce, Python, JavaScript, Laravel, React)

Docs: https://vfault.com/docs.html
Usage: https://exceedweb.pythonanywhere.com/usage

Questions? Contact vfault@exceedwebservices.com

VFault — Verification at the fault lines
Built by Ian Fraser — Exceed Web Services
"""

    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{VFAULT_FROM_NAME} <{VFAULT_FROM_EMAIL}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg['Reply-To'] = VFAULT_FROM_EMAIL

        msg.attach(MIMEText(plain_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))

        server = smtplib.SMTP(BREVO_SMTP_HOST, BREVO_SMTP_PORT)
        server.starttls()
        server.login(BREVO_SMTP_USER, BREVO_SMTP_PASS)
        server.send_message(msg)
        server.quit()

        print(f"[VFault] API key emailed to {to_email}")
        return True

    except Exception as e:
        print(f"[VFault] Email failed to {to_email}: {e}")
        return False


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

# WordPress function name pattern
WP_FUNC_PATTERN = re.compile(
    r'\b((?:wp_|WP_|wc_|get_|set_|is_|has_|add_|remove_|do_|apply_|register_|'
    r'unregister_|delete_|update_|check_|create_|edit_|the_|have_|'
    r'sanitize_|esc_|wp_kses|absint|zeroise|human_|size_|number_|'
    r'__return_|current_user_|get_user_|wp_ajax_|admin_|comment_|'
    r'post_|term_|taxonomy_|nav_menu_|sidebar_|widget_|'
    r'shortcode_|media_|plugin_|theme_|block_|rest_|woocommerce_)'
    r'[a-z_]{2,80})\b'
)

# React hooks and core functions
REACT_PATTERN = re.compile(
    r'\b(use[A-Z][a-zA-Z]{1,50}|'
    r'createElement|cloneElement|createContext|createRef|forwardRef|'
    r'isValidElement|startTransition|lazy|memo|act|cache|'
    r'Component|PureComponent|Fragment|Suspense|StrictMode|Profiler|'
    r'ReactDOM\.createRoot|ReactDOM\.hydrateRoot|ReactDOM\.createPortal|'
    r'ReactDOM\.flushSync|ReactDOM\.render|ReactDOM\.hydrate|'
    r'ReactDOMServer\.renderToString|ReactDOMServer\.renderToPipeableStream|'
    r'componentDidMount|componentDidUpdate|componentWillUnmount|'
    r'componentWillMount|componentWillReceiveProps|componentWillUpdate|'
    r'shouldComponentUpdate|getDerivedStateFromProps|getSnapshotBeforeUpdate|'
    r'UNSAFE_componentWillMount|UNSAFE_componentWillReceiveProps|'
    r'getServerSideProps|getStaticProps|getStaticPaths|getInitialProps|'
    r'generateStaticParams|generateMetadata|revalidatePath|revalidateTag|'
    r'NextResponse\.redirect|NextResponse\.rewrite|NextResponse\.next|NextResponse\.json|'
    r'useRouter|usePathname|useSearchParams|useParams|'
    r'notFound|permanentRedirect|draftMode|localFont|'
    r'NextRequest|NextResponse|ImageResponse'
    r')\b'
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

# JavaScript/Node/Third-party dotted references
JS_DOTTED_PATTERN = re.compile(
    r'\b((?:fs|path|http|https|crypto|url|util|os|net|dns|tls|'
    r'stream|buffer|events|child_process|cluster|zlib|readline|'
    r'vm|worker_threads|http2|dgram|perf_hooks|querystring|'
    r'JSON|Math|Array|Object|String|Number|Promise|Buffer|'
    r'RegExp|Map|Set|Date|Error|console|process|'
    r'Array\.prototype|String\.prototype|Object|'
    r'ReactDOM|ReactDOMServer|NextResponse|NextRequest|'
    r'axios|express|mongoose|moment|dayjs|'
    r'_|pd|np|plt|tf|torch|scipy|sk|cv2|'
    r'jest|expect|assert|chai|sinon|cy)'
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

# Class::method and $obj->method patterns (for class/method pairing validation)
CLASS_METHOD_PATTERN = re.compile(
    r'\b([A-Z][a-zA-Z_0-9]+)(?:::|\->)([a-z_][a-z_0-9]*)\b'
)

# Function calls with parameter text (for param comparison second pass)
FUNC_CALL_WITH_PARAMS = re.compile(
    r'\b([a-zA-Z_][\w]*)\s*\(([^)]{1,500})\)'
)

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
    # Common English words that match function names
    'render', 'use', 'call', 'get', 'set', 'update', 'delete',
    'create', 'check', 'add', 'remove', 'register', 'apply',
    'load', 'save', 'send', 'read', 'write', 'run', 'start',
    'stop', 'init', 'reset', 'clear', 'close', 'open', 'format',
    'filter', 'sort', 'merge', 'split', 'join', 'map', 'reduce',
    'import', 'export', 'include', 'require', 'return', 'print',
    'log', 'error', 'warn', 'debug', 'test', 'assert',
}

# Known third-party library functions — not hallucinations, just not in our shards
KNOWN_THIRD_PARTY = {
    'useSWR': 'swr', 'useSWRConfig': 'swr', 'useSWRMutation': 'swr',
    'useSWRInfinite': 'swr', 'useSWRSubscription': 'swr',
    'useQuery': '@tanstack/react-query', 'useMutation': '@tanstack/react-query',
    'useQueryClient': '@tanstack/react-query', 'useInfiniteQuery': '@tanstack/react-query',
    'useIsFetching': '@tanstack/react-query', 'useSuspenseQuery': '@tanstack/react-query',
    'useForm': 'react-hook-form', 'useController': 'react-hook-form',
    'useFormContext': 'react-hook-form', 'useWatch': 'react-hook-form',
    'useFieldArray': 'react-hook-form',
    'useStore': 'zustand',
    'useSelector': 'react-redux', 'useDispatch': 'react-redux',
    'useAnimation': 'framer-motion', 'useMotionValue': 'framer-motion',
    'useSpring': 'framer-motion', 'useScroll': 'framer-motion',
    'useInView': 'framer-motion',
    'axios.get': 'axios', 'axios.post': 'axios', 'axios.put': 'axios',
    'axios.delete': 'axios', 'axios.patch': 'axios', 'axios.create': 'axios',
    '_.debounce': 'lodash', '_.throttle': 'lodash', '_.cloneDeep': 'lodash',
    '_.merge': 'lodash', '_.get': 'lodash', '_.set': 'lodash',
    '_.isEmpty': 'lodash', '_.isEqual': 'lodash', '_.uniq': 'lodash',
    '_.groupBy': 'lodash', '_.sortBy': 'lodash',
    'requests.get': 'requests', 'requests.post': 'requests',
    'requests.put': 'requests', 'requests.delete': 'requests',
    'pd.DataFrame': 'pandas', 'pd.read_csv': 'pandas',
    'np.array': 'numpy', 'np.zeros': 'numpy', 'np.ones': 'numpy',
    'plt.plot': 'matplotlib', 'plt.show': 'matplotlib',
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

    # WordPress and WooCommerce function name patterns
    for match in WP_FUNC_PATTERN.finditer(text):
        name = match.group(1)
        if name in SKIP_EXACT:
            continue
        if name.startswith(SKIP_PREFIXES):
            continue
        if is_user_defined_hook(name, text):
            continue
        claims.add(name)

    # React hooks, components, and Next.js APIs
    for match in REACT_PATTERN.finditer(text):
        claims.add(match.group(1))

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
        elif (name.startswith(('wp_', 'wc_', 'woocommerce_', 'get_', 'set_', 'is_', 'has_', 'add_',
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


def extract_class_method_pairs(text):
    """Extract ClassName::method and ClassName->method pairs from text.
    Returns list of dicts: {'class': 'WC_Product', 'method': 'get_total'}"""
    pairs = []
    for match in CLASS_METHOD_PATTERN.finditer(text):
        class_name = match.group(1)
        method_name = match.group(2)
        if method_name not in SKIP_EXACT:
            pairs.append({'class': class_name, 'method': method_name})
    return pairs


def extract_calls_with_params(text):
    """Extract function calls with their stated parameter text.
    Returns dict: {'func_name': 'stated_params_text', ...}
    Only captures calls containing $ (PHP-style params) for comparison."""
    calls = {}
    for match in FUNC_CALL_WITH_PARAMS.finditer(text):
        func_name = match.group(1)
        params_text = match.group(2).strip()
        # Only capture if it contains PHP-style $param references
        if '$' in params_text and func_name not in SKIP_EXACT:
            # Take the first occurrence (most likely to be a signature/definition)
            if func_name not in calls:
                calls[func_name] = params_text
    return calls


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_claim(name, allowed_shards=None, whitelist=None):
    """Verify a single function/hook name against the store.
    If whitelist is provided, names matching any prefix are skipped (whitelisted)."""
    # Whitelist check: skip names matching user's private namespaces
    if whitelist:
        for prefix in whitelist:
            if name.startswith(prefix):
                return {
                    'name': name,
                    'exists': True,
                    'status': 'whitelisted',
                    'message': f"Skipped — matches whitelist prefix '{prefix}'.",
                }

    # Hot cache lookup (microseconds)
    if name in HOT_CACHE:
        triples = get_triples(name, allowed_shards)

        # If the name exists in the cache but not in allowed shards,
        # treat it as not found for this user (they need to upgrade)
        if not triples and HOT_CACHE.get(name):
            return {
                'name': name,
                'exists': True,
                'status': 'upgrade_required',
                'message': 'This function exists in a paid shard. Upgrade to Pro for access.',
                'upgrade_url': 'https://vfault.com/pricing.html',
            }

        if triples:
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
                result['message'] = 'Verified — exists in source.'

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

    all_subjects = get_all_subjects(allowed_shards)
    suggestions = []

    # Tier 1: Fuzzy match (close spelling — typos, plurals, underscores)
    close = get_close_matches(name, all_subjects, n=5, cutoff=0.5)
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

    # Check if it's a known third-party library function
    if name in KNOWN_THIRD_PARTY:
        return {
            'name': name,
            'exists': True,
            'status': 'third_party',
            'library': KNOWN_THIRD_PARTY[name],
            'message': f"Third-party library function from {KNOWN_THIRD_PARTY[name]}. Not covered by VFault shards.",
        }

    # Determine if this is a hallucination (shard namespace) or unknown (private code)
    in_shard_scope = is_shard_namespace(name)

    if in_shard_scope:
        result = {
            'name': name,
            'exists': False,
            'status': 'not_found',
            'message': f"NOT FOUND. This may be a hallucination.",
        }
    else:
        result = {
            'name': name,
            'exists': False,
            'status': 'unknown',
            'message': f"Unknown — not in any public shard. May be private/custom code.",
        }

    if suggestions:
        result['suggestions'] = suggestions
        if in_shard_scope:
            result['message'] += f" Did you mean: {', '.join(suggestions[:3])}?"
        else:
            result['message'] += f" Closest public matches: {', '.join(suggestions[:3])}"

    return result


def compare_params(func_name, stated_params_text, allowed_shards=None):
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


# ---------------------------------------------------------------------------
# Context Rules Engine
# ---------------------------------------------------------------------------
# Rules check HOW a function is used, not just IF it exists.
# Each rule: function_name -> list of checks.
# A check has: 'context' (regex that should be present), 'message' (warning),
# and 'severity' ('warning' or 'error').
#
# This is a heuristic first-line check on snippets/text. It won't catch
# everything (multi-file, variable scoping) but flags the most common
# AI-generated context mistakes.

CONTEXT_RULES = {
    # WordPress: enqueue functions must be hooked to wp_enqueue_scripts
    'wp_enqueue_script': [{
        'id': 'wp_enqueue_hook',
        'context': r"add_action\s*\(\s*['\"]wp_enqueue_scripts['\"]",
        'missing_message': "wp_enqueue_script should be called inside an add_action('wp_enqueue_scripts', ...) callback.",
        'severity': 'warning',
    }],
    'wp_enqueue_style': [{
        'id': 'wp_style_hook',
        'context': r"add_action\s*\(\s*['\"]wp_enqueue_scripts['\"]",
        'missing_message': "wp_enqueue_style should be called inside an add_action('wp_enqueue_scripts', ...) callback.",
        'severity': 'warning',
    }],
    # Admin enqueue should use admin_enqueue_scripts
    'admin_enqueue_scripts': [{
        'id': 'admin_enqueue_hook',
        'context': r"add_action\s*\(\s*['\"]admin_enqueue_scripts['\"]",
        'missing_message': "Admin scripts should be enqueued inside add_action('admin_enqueue_scripts', ...).",
        'severity': 'warning',
    }],
    # register_post_type must be in init hook
    'register_post_type': [{
        'id': 'cpt_init_hook',
        'context': r"add_action\s*\(\s*['\"]init['\"]",
        'missing_message': "register_post_type should be called inside an add_action('init', ...) callback.",
        'severity': 'warning',
    }],
    # register_taxonomy must be in init hook
    'register_taxonomy': [{
        'id': 'taxonomy_init_hook',
        'context': r"add_action\s*\(\s*['\"]init['\"]",
        'missing_message': "register_taxonomy should be called inside an add_action('init', ...) callback.",
        'severity': 'warning',
    }],
    # add_meta_box must be in add_meta_boxes hook
    'add_meta_box': [{
        'id': 'metabox_hook',
        'context': r"add_action\s*\(\s*['\"]add_meta_boxes['\"]",
        'missing_message': "add_meta_box should be called inside an add_action('add_meta_boxes', ...) callback.",
        'severity': 'warning',
    }],
    # register_widget must be in widgets_init hook
    'register_widget': [{
        'id': 'widget_init_hook',
        'context': r"add_action\s*\(\s*['\"]widgets_init['\"]",
        'missing_message': "register_widget should be called inside an add_action('widgets_init', ...) callback.",
        'severity': 'warning',
    }],
    # register_sidebar must be in widgets_init hook
    'register_sidebar': [{
        'id': 'sidebar_init_hook',
        'context': r"add_action\s*\(\s*['\"]widgets_init['\"]",
        'missing_message': "register_sidebar should be called inside an add_action('widgets_init', ...) callback.",
        'severity': 'warning',
    }],
    # add_shortcode should be in init
    'add_shortcode': [{
        'id': 'shortcode_init_hook',
        'context': r"add_action\s*\(\s*['\"]init['\"]",
        'missing_message': "add_shortcode should be called inside an add_action('init', ...) callback.",
        'severity': 'warning',
    }],
    # wp_redirect / wp_safe_redirect must be followed by exit or die
    'wp_redirect': [{
        'id': 'redirect_exit',
        'context': r"wp_redirect\s*\(.*?\)\s*;\s*(exit|die)",
        'missing_message': "wp_redirect must be followed by exit; or die; to prevent further execution.",
        'severity': 'error',
    }],
    'wp_safe_redirect': [{
        'id': 'safe_redirect_exit',
        'context': r"wp_safe_redirect\s*\(.*?\)\s*;\s*(exit|die)",
        'missing_message': "wp_safe_redirect must be followed by exit; or die; to prevent further execution.",
        'severity': 'error',
    }],
    # wp_nonce checks should happen before processing form data
    'update_option': [{
        'id': 'update_option_nonce',
        'context': r"(wp_verify_nonce|check_admin_referer|check_ajax_referer)",
        'missing_message': "update_option should be protected by a nonce check (wp_verify_nonce, check_admin_referer, or check_ajax_referer).",
        'severity': 'warning',
    }],
    'delete_option': [{
        'id': 'delete_option_nonce',
        'context': r"(wp_verify_nonce|check_admin_referer|check_ajax_referer)",
        'missing_message': "delete_option should be protected by a nonce check.",
        'severity': 'warning',
    }],
    # wp_localize_script should appear after wp_enqueue_script
    'wp_localize_script': [{
        'id': 'localize_after_enqueue',
        'context': r"wp_enqueue_script",
        'missing_message': "wp_localize_script requires a script to be registered or enqueued first via wp_enqueue_script.",
        'severity': 'warning',
    }],
    # WooCommerce: wc_get_product should check for false return
    'wc_get_product': [{
        'id': 'wc_product_false_check',
        'context': r"(if\s*\(\s*!?\s*\$|false|!==\s*false|\?\?)",
        'missing_message': "wc_get_product can return false. Check the return value before using it.",
        'severity': 'warning',
    }],
    # WooCommerce: wc_get_order should check for false return
    'wc_get_order': [{
        'id': 'wc_order_false_check',
        'context': r"(if\s*\(\s*!?\s*\$|false|!==\s*false|\?\?)",
        'missing_message': "wc_get_order can return false. Check the return value before using it.",
        'severity': 'warning',
    }],

    # ── React / Next.js ──────────────────────────────────────────
    'useEffect': [{
        'id': 'async_use_effect',
        'context_forbidden': r"useEffect\s*\(\s*async",
        'missing_message': "useEffect callback must not be async directly. Define an async function inside the effect and call it.",
        'severity': 'error',
    }],
    'dangerouslySetInnerHTML': [{
        'id': 'dangerouslysetinnerhtml_xss',
        'context': r"(sanitize|DOMPurify|purify|escape|xss)",
        'missing_message': "dangerouslySetInnerHTML should use a sanitiser (e.g. DOMPurify) to prevent XSS.",
        'severity': 'error',
    }],

    # ── Python ───────────────────────────────────────────────────
    'os.system': [{
        'id': 'os_system_security',
        'context_forbidden': r"os\.system\s*\(",
        'missing_message': "os.system is a security risk. Use subprocess.run with a list of arguments instead.",
        'severity': 'warning',
    }],
    'subprocess.run': [{
        'id': 'subprocess_run_shell',
        'context_forbidden': r"subprocess\.run\s*\([^)]*shell\s*=\s*True",
        'missing_message': "subprocess.run with shell=True is a security risk. Use a list of arguments instead.",
        'severity': 'warning',
    }],
    'subprocess.call': [{
        'id': 'subprocess_call_shell',
        'context_forbidden': r"subprocess\.call\s*\([^)]*shell\s*=\s*True",
        'missing_message': "subprocess.call with shell=True is a security risk. Use a list of arguments instead.",
        'severity': 'warning',
    }],
    'subprocess.Popen': [{
        'id': 'subprocess_popen_shell',
        'context_forbidden': r"subprocess\.Popen\s*\([^)]*shell\s*=\s*True",
        'missing_message': "subprocess.Popen with shell=True is a security risk. Use a list of arguments instead.",
        'severity': 'warning',
    }],
    'eval': [{
        'id': 'eval_security',
        'context_forbidden': r"\beval\s*\(",
        'missing_message': "eval() executes arbitrary code and is a security risk. Use ast.literal_eval() for safe evaluation of literals.",
        'severity': 'error',
    }],
    'pickle.loads': [{
        'id': 'pickle_loads_untrusted',
        'context': r"(trusted|safe|verified|internal)",
        'missing_message': "pickle.loads can execute arbitrary code. Never unpickle data from untrusted sources.",
        'severity': 'error',
    }],
    'pickle.load': [{
        'id': 'pickle_load_untrusted',
        'context': r"(trusted|safe|verified|internal)",
        'missing_message': "pickle.load can execute arbitrary code. Never unpickle data from untrusted sources.",
        'severity': 'error',
    }],
    # ── JavaScript ───────────────────────────────────────────────
    'JSON.parse': [{
        'id': 'json_parse_try_catch',
        'context': r"(try\s*\{|catch\s*\(|\.catch\s*\(|\?\s*\.)",
        'missing_message': "JSON.parse throws on invalid input. Wrap in try/catch or validate the input first.",
        'severity': 'warning',
    }],

    # ── Laravel ──────────────────────────────────────────────────
    'env': [{
        'id': 'env_outside_config',
        'context': r"(config\(|config/|\.env|Config::)",
        'missing_message': "env() should only be called in config files. Use config() everywhere else — env() returns null when config is cached.",
        'severity': 'error',
    }],
    'redirect': [{
        'id': 'redirect_return',
        'context': r"return\s+(redirect|Redirect)",
        'missing_message': "redirect() must be returned from the controller method to take effect.",
        'severity': 'error',
    }],
    'bcrypt': [{
        'id': 'bcrypt_use_hash',
        'context': r"(Hash::make|Hash::check|password_hash)",
        'missing_message': "Use Hash::make() instead of bcrypt() directly — it respects the hashing driver configured in config/hashing.php.",
        'severity': 'warning',
    }],
    'decrypt': [{
        'id': 'decrypt_try_catch',
        'context': r"(try\s*\{|catch\s*\(|DecryptException)",
        'missing_message': "decrypt() throws DecryptException on invalid data. Wrap in try/catch.",
        'severity': 'warning',
    }],

}


def check_context_rules(text, verified_names, disable_rules=None):
    """Check usage context rules for verified functions found in the text.
    Two rule types:
      - 'context': pattern that SHOULD be present (missing = issue)
      - 'context_forbidden': pattern that SHOULD NOT be present (found = issue)
    disable_rules: optional list of function names to skip.
    Returns list of context issues."""
    issues = []
    skip_funcs = set()
    skip_ids = set()
    if disable_rules:
        for item in disable_rules:
            # Could be a function name or a rule ID
            if item in CONTEXT_RULES:
                skip_funcs.add(item)
            else:
                skip_ids.add(item)
    for func_name in verified_names:
        if func_name not in CONTEXT_RULES:
            continue
        if func_name in skip_funcs:
            continue
        # Confirm the function actually appears in the text
        if func_name not in text:
            continue
        for rule in CONTEXT_RULES[func_name]:
            if rule['id'] in skip_ids:
                continue
            if 'context_forbidden' in rule:
                # Pattern should NOT be present
                pattern = re.compile(rule['context_forbidden'], re.DOTALL)
                if pattern.search(text):
                    issues.append({
                        'id': rule['id'],
                        'function': func_name,
                        'status': 'context_issue',
                        'severity': rule['severity'],
                        'message': rule['missing_message'],
                    })
            elif 'context' in rule:
                # Pattern SHOULD be present
                pattern = re.compile(rule['context'], re.DOTALL)
                if not pattern.search(text):
                    issues.append({
                        'id': rule['id'],
                        'function': func_name,
                        'status': 'context_issue',
                        'severity': rule['severity'],
                        'message': rule['missing_message'],
                    })
    return issues


def verify_text(text, allowed_shards=None, whitelist=None, disable_rules=None):
    """Extract and verify all claims in a block of text.
    whitelist: optional list of namespace prefixes to skip (private code).
    disable_rules: optional list of function names to skip context checking for."""
    claims = extract_claims(text)

    results = {
        'input_length': len(text),
        'claims_found': len(claims),
        'verified': [],
        'deprecated': [],
        'not_found': [],
        'unknown': [],
        'whitelisted': [],
        'upgrade_required': [],
        'third_party': [],
        'param_issues': [],
        'class_mismatches': [],
        'context_issues': [],
        'summary': {}
    }

    for claim in sorted(claims):
        v = verify_claim(claim, allowed_shards, whitelist)

        if v['status'] == 'verified':
            results['verified'].append(v)
        elif v['status'] == 'deprecated':
            results['deprecated'].append(v)
        elif v['status'] == 'not_found':
            results['not_found'].append(v)
        elif v['status'] == 'unknown':
            results['unknown'].append(v)
        elif v['status'] == 'whitelisted':
            results['whitelisted'].append(v)
        elif v['status'] == 'upgrade_required':
            results['upgrade_required'].append(v)
        elif v['status'] == 'third_party':
            results['third_party'].append(v)

    # ── Second pass: parameter comparison ─────────────────────────
    # Extract function calls with stated params from the text,
    # then compare against stored signatures for verified functions.
    calls_with_params = extract_calls_with_params(text)
    verified_names = {v['name'] for v in results['verified']}
    deprecated_names = {v['name'] for v in results['deprecated']}
    checkable = verified_names | deprecated_names

    for func_name, stated_params in calls_with_params.items():
        if func_name not in checkable:
            continue
        param_result = compare_params(func_name, stated_params, allowed_shards)
        if param_result and param_result['status'] == 'param_mismatch':
            results['param_issues'].append(param_result)

    # ── Second pass: class/method pairing validation ──────────────
    # Extract ClassName::method and ClassName->method pairs,
    # then check if the method actually belongs to the stated class.
    # Shard subjects are stored as "ClassName.method_name".
    class_method_pairs = extract_class_method_pairs(text)
    for pair in class_method_pairs:
        method_name = pair['method']
        stated_class = pair['class']

        # Construct the expected subject key
        stated_key = f"{stated_class}.{method_name}"

        if stated_key in HOT_CACHE:
            # Correct pairing, nothing to flag
            continue

        # Method not found under stated class. Search for it under other classes.
        actual_owners = []
        search_suffix = f".{method_name}"
        for subject in HOT_CACHE:
            if subject.endswith(search_suffix):
                # Get the class predicate to confirm
                for t in HOT_CACHE[subject]:
                    if t['predicate'] == 'class':
                        actual_owners.append(t['object'])
                        break

        if not actual_owners:
            # Method doesn't exist under any class, skip (main pass handles it)
            continue

        # Filter by allowed shards
        if allowed_shards:
            filtered_owners = []
            for owner in actual_owners:
                owner_key = f"{owner}.{method_name}"
                owner_triples = get_triples(owner_key, allowed_shards)
                if owner_triples:
                    filtered_owners.append(owner)
            actual_owners = filtered_owners

        if not actual_owners:
            continue

        results['class_mismatches'].append({
            'method': method_name,
            'stated_class': stated_class,
            'actual_classes': actual_owners,
            'status': 'class_mismatch',
            'message': (
                f"CLASS MISMATCH: {stated_class}::{method_name} "
                f"but {method_name} belongs to "
                f"{', '.join(actual_owners)}."
            ),
        })

    # ── Third pass: usage context rules ───────────────────────────
    # Check if verified functions are used in the correct context
    # (e.g. wp_enqueue_script inside add_action callback).
    all_verified = (
        {v['name'] for v in results['verified']} |
        {v['name'] for v in results['deprecated']}
    )
    results['context_issues'] = check_context_rules(text, all_verified, disable_rules)

    results['summary'] = {
        'total_claims': len(claims),
        'verified': len(results['verified']),
        'deprecated': len(results['deprecated']),
        'not_found': len(results['not_found']),
        'unknown': len(results['unknown']),
        'whitelisted': len(results['whitelisted']),
        'upgrade_required': len(results['upgrade_required']),
        'third_party': len(results['third_party']),
        'param_issues': len(results['param_issues']),
        'class_mismatches': len(results['class_mismatches']),
        'context_issues': len(results['context_issues']),
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
    from fastapi import FastAPI, HTTPException, Request, Header
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
    from typing import Optional

    MAX_INPUT_LENGTH = 50000

    # Per-minute burst tracking (in-memory, resets on restart — that's fine)
    burst_store = {}

    app = FastAPI(
        title="VFault — Verification at the Fault Lines",
        description="Deterministic verification gateway for AI-generated code.",
        version="1.0.0"
    )

    # ── Auth + rate limiting ──────────────────────────────────────

    def authenticate(request: Request, x_api_key: Optional[str] = None):
        """Identify the caller and enforce rate limits.
        Returns dict with plan, user_id, remaining counts."""

        # 1. Identify caller
        if x_api_key:
            key_info = validate_api_key(x_api_key)
            if not key_info:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid or inactive API key."
                )
            plan = key_info['plan']
            user_id = x_api_key
        else:
            plan = 'free'
            user_id = f"ip_{request.client.host}" if request.client else "ip_unknown"

        # 2. Check daily limit
        daily_limit = PLAN_LIMITS.get(plan, 100)
        daily_used = get_daily_usage(user_id)

        if daily_used >= daily_limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Daily rate limit exceeded.",
                    "plan": plan,
                    "limit": daily_limit,
                    "used": daily_used,
                    "upgrade_url": "https://vfault.com/#pricing"
                }
            )

        # 3. Check per-minute burst limit
        burst_limit = PLAN_BURST.get(plan, 15)
        now = time.time()
        window_start = now - 60

        if user_id not in burst_store:
            burst_store[user_id] = []

        burst_store[user_id] = [
            t for t in burst_store[user_id] if t > window_start
        ]

        if len(burst_store[user_id]) >= burst_limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Too many requests per minute. Slow down.",
                    "plan": plan,
                    "burst_limit": burst_limit
                }
            )

        burst_store[user_id].append(now)

        # 4. Count this request
        increment_usage(user_id)

        return {
            "plan": plan,
            "user_id": user_id,
            "daily_remaining": daily_limit - daily_used - 1,
            "daily_limit": daily_limit,
        }

    def sanitise_name(name: str) -> str:
        """Strip anything that isn't a valid PHP function/hook character."""
        return re.sub(r'[^a-zA-Z0-9_\-/{}.]', '', name)[:200]

    # ── Models ────────────────────────────────────────────────────

    class VerifyRequest(BaseModel):
        text: str = Field(..., max_length=MAX_INPUT_LENGTH)
        whitelist: Optional[list] = Field(None, max_length=50)
        disable_rules: Optional[list] = Field(None, max_length=50)

    class ParamCompareRequest(BaseModel):
        function: str = Field(..., max_length=200)
        stated_params: str = Field(..., max_length=2000)

    # ── Startup ───────────────────────────────────────────────────

    @app.on_event("startup")
    def startup():
        init_keys_db()
        load_all_shards()

    # ── Endpoints ─────────────────────────────────────────────────

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "shards_loaded": LOADED_SHARDS,
            "subjects_cached": len(HOT_CACHE),
            "triples_cached": sum(len(v) for v in HOT_CACHE.values())
        }

    @app.post("/verify")
    def verify(req: VerifyRequest, request: Request,
               x_api_key: Optional[str] = Header(None)):
        auth = authenticate(request, x_api_key)
        if len(req.text) > MAX_INPUT_LENGTH:
            raise HTTPException(
                status_code=413,
                detail=f"Input too large. Max {MAX_INPUT_LENGTH} characters."
            )
        result = verify_text(req.text, whitelist=req.whitelist,
                             disable_rules=req.disable_rules)
        result['plan'] = auth['plan']
        result['daily_remaining'] = auth['daily_remaining']
        return result

    @app.get("/lookup/{name}")
    def lookup(name: str, request: Request,
               x_api_key: Optional[str] = Header(None)):
        authenticate(request, x_api_key)
        clean_name = sanitise_name(name)
        if not clean_name:
            raise HTTPException(status_code=400, detail="Invalid function name.")
        return verify_claim(clean_name)

    @app.get("/search/{prefix}")
    def search(prefix: str, request: Request, limit: int = 20,
               x_api_key: Optional[str] = Header(None)):
        authenticate(request, x_api_key)
        clean_prefix = sanitise_name(prefix)
        if not clean_prefix:
            raise HTTPException(status_code=400, detail="Invalid search prefix.")
        limit = min(limit, 50)
        matches = [s for s in HOT_CACHE.keys()
                   if s.startswith(clean_prefix)][:limit]
        return {"prefix": clean_prefix, "matches": matches, "count": len(matches)}

    @app.post("/compare_params")
    def compare(req: ParamCompareRequest, request: Request,
                x_api_key: Optional[str] = Header(None)):
        authenticate(request, x_api_key)
        clean_func = sanitise_name(req.function)
        if not clean_func:
            raise HTTPException(status_code=400, detail="Invalid function name.")
        result = compare_params(clean_func, req.stated_params)
        if result is None:
            return {"error": f"Function '{clean_func}' not found or has no parameters"}
        return result

    @app.get("/stats")
    def stats(request: Request, x_api_key: Optional[str] = Header(None)):
        authenticate(request, x_api_key)
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

    @app.get("/rules")
    def rules():
        """List all active context rules with severity and descriptions."""
        # Group rules by ecosystem
        ecosystems = {
            'wordpress': [],
            'woocommerce': [],
            'react': [],
            'python': [],
            'javascript': [],
            'laravel': [],
        }

        # Map function names to ecosystems
        wp_funcs = {
            'wp_enqueue_script', 'wp_enqueue_style', 'admin_enqueue_scripts',
            'register_post_type', 'register_taxonomy', 'add_meta_box',
            'register_widget', 'register_sidebar', 'add_shortcode',
            'wp_redirect', 'wp_safe_redirect', 'update_option',
            'delete_option', 'wp_localize_script',
        }
        wc_funcs = {'wc_get_product', 'wc_get_order'}
        react_funcs = {'useEffect', 'dangerouslySetInnerHTML'}
        python_funcs = {
            'os.system', 'subprocess.run', 'subprocess.call',
            'subprocess.Popen', 'eval', 'pickle.loads', 'pickle.load',
        }
        js_funcs = {'JSON.parse'}
        laravel_funcs = {'env', 'redirect', 'bcrypt', 'decrypt'}

        for func_name, rule_list in CONTEXT_RULES.items():
            for rule in rule_list:
                entry = {
                    'id': rule['id'],
                    'function': func_name,
                    'severity': rule['severity'],
                    'rule_type': 'forbidden' if 'context_forbidden' in rule else 'required',
                    'message': rule['missing_message'],
                }
                if func_name in wp_funcs:
                    ecosystems['wordpress'].append(entry)
                elif func_name in wc_funcs:
                    ecosystems['woocommerce'].append(entry)
                elif func_name in react_funcs:
                    ecosystems['react'].append(entry)
                elif func_name in python_funcs:
                    ecosystems['python'].append(entry)
                elif func_name in js_funcs:
                    ecosystems['javascript'].append(entry)
                elif func_name in laravel_funcs:
                    ecosystems['laravel'].append(entry)

        return {
            'total_rules': sum(len(v) for v in CONTEXT_RULES.values()),
            'ecosystems': ecosystems,
            'usage': (
                'Rules are checked automatically on /verify. '
                'To disable specific rules, pass disable_rules: '
                '["func_name"] in your /verify request.'
            ),
        }

    # ── Usage endpoint (check your own limits) ────────────────────

    @app.get("/usage")
    def usage(request: Request, x_api_key: Optional[str] = Header(None)):
        """Check your current usage and remaining requests."""
        if x_api_key:
            key_info = validate_api_key(x_api_key)
            if not key_info:
                raise HTTPException(status_code=401, detail="Invalid API key.")
            plan = key_info['plan']
            user_id = x_api_key
        else:
            plan = 'free'
            user_id = f"ip_{request.client.host}" if request.client else "ip_unknown"

        daily_limit = PLAN_LIMITS.get(plan, 100)
        daily_used = get_daily_usage(user_id)

        return {
            "plan": plan,
            "daily_limit": daily_limit,
            "daily_used": daily_used,
            "daily_remaining": max(0, daily_limit - daily_used),
        }

    # ── Lemon Squeezy webhook ─────────────────────────────────────

    @app.post("/webhook/lemonsqueezy")
    async def lemonsqueezy_webhook(request: Request):
        """Receives payment events from Lemon Squeezy.
        Creates API keys on subscription_created.
        Deactivates keys on subscription_expired/cancelled."""

        # Read raw body for signature verification
        body = await request.body()

        # Verify signature if secret is configured
        if LEMONSQUEEZY_SECRET:
            signature = request.headers.get('x-signature', '')
            if not verify_lemonsqueezy_signature(body, signature):
                raise HTTPException(status_code=403, detail="Invalid signature.")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON.")

        event = payload.get('meta', {}).get('event_name', '')
        data = payload.get('data', {})
        attrs = data.get('attributes', {})

        if event == 'subscription_created':
            email = attrs.get('user_email')
            if not email:
                raise HTTPException(status_code=400, detail="No email in payload.")

            customer_id = str(attrs.get('customer_id', ''))
            subscription_id = str(data.get('id', ''))

            # Map Lemon Squeezy variant to plan (configure your variant IDs here)
            plan = 'pro'  # Default — extend this when you add Team/Enterprise variants

            api_key = create_api_key(
                email=email,
                plan=plan,
                ls_customer_id=customer_id,
                ls_subscription_id=subscription_id,
                note=f"Auto-created via Lemon Squeezy webhook"
            )

            print(f"[VFault] New {plan} key created for {email}: {api_key[:20]}...")
            return {"status": "key_created", "email": email, "plan": plan}

        elif event in ('subscription_expired', 'subscription_cancelled'):
            subscription_id = str(data.get('id', ''))
            if subscription_id:
                conn = sqlite3.connect(KEYS_DB_PATH)
                c = conn.cursor()
                c.execute(
                    'UPDATE api_keys SET status = ? WHERE ls_subscription_id = ?',
                    ('inactive', subscription_id)
                )
                affected = c.rowcount
                conn.commit()
                conn.close()
                print(f"[VFault] Deactivated {affected} key(s) for subscription {subscription_id}")
                return {"status": "keys_deactivated", "count": affected}

        return {"status": "ignored", "event": event}

    # ── Admin: manual key creation (protect with a secret) ────────

    ADMIN_SECRET = os.environ.get('VFAULT_ADMIN_SECRET', '')

    @app.post("/admin/create-key")
    def admin_create_key(request: Request,
                         email: str = '',
                         plan: str = 'pro',
                         x_admin_secret: Optional[str] = Header(None)):
        """Manually create an API key. Requires VFAULT_ADMIN_SECRET header."""
        if not ADMIN_SECRET or not x_admin_secret:
            raise HTTPException(status_code=403, detail="Admin access denied.")
        if not hmac.compare_digest(ADMIN_SECRET, x_admin_secret):
            raise HTTPException(status_code=403, detail="Admin access denied.")
        if not email:
            raise HTTPException(status_code=400, detail="Email required.")
        if plan not in PLAN_LIMITS:
            raise HTTPException(status_code=400, detail=f"Invalid plan. Use: {list(PLAN_LIMITS.keys())}")

        api_key = create_api_key(email=email, plan=plan, note="Manual admin creation")
        return {"api_key": api_key, "email": email, "plan": plan}

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
    print(f"Claims found:       {results['summary']['total_claims']}")
    print(f"Verified:           {results['summary']['verified']}")
    print(f"Deprecated:         {results['summary']['deprecated']}")
    print(f"Not found:          {results['summary']['not_found']}")
    print(f"Unknown:            {results['summary']['unknown']}")
    print(f"Whitelisted:        {results['summary']['whitelisted']}")
    print(f"Param issues:       {results['summary']['param_issues']}")
    print(f"Class mismatches:   {results['summary']['class_mismatches']}")
    print(f"Context issues:     {results['summary']['context_issues']}")
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

    if results['unknown']:
        print()
        print("UNKNOWN (not in public shards)")
        print("-" * 40)
        for v in results['unknown']:
            print(f"  {v['name']}")
            if v.get('suggestions'):
                print(f"    closest: {', '.join(v['suggestions'])}")

    if results['whitelisted']:
        print()
        print("WHITELISTED (skipped)")
        print("-" * 40)
        for v in results['whitelisted']:
            print(f"  {v['name']}")

    if results['param_issues']:
        print()
        print("PARAMETER ISSUES")
        print("-" * 40)
        for p in results['param_issues']:
            print(f"  {p['function']}: {p['message']}")
            print(f"    stored:  {p['stored_params']}")
            print(f"    stated:  {p['stated_params']}")

    if results['class_mismatches']:
        print()
        print("CLASS/METHOD MISMATCHES")
        print("-" * 40)
        for c in results['class_mismatches']:
            print(f"  {c['message']}")

    if results['context_issues']:
        print()
        print("CONTEXT ISSUES")
        print("-" * 40)
        for c in results['context_issues']:
            severity = "ERROR" if c['severity'] == 'error' else "WARNING"
            print(f"  [{severity}] {c['function']}: {c['message']}")

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
