#!/usr/bin/env python3
"""
VFault messy real-world test suite
====================================
Tests the gate against realistic AI-generated output:
long prose, markdown, mixed code blocks, partial references,
multi-language in one response, and edge cases.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from vfault import load_all_shards, verify_text, verify_claim, HOT_CACHE


def setup():
    load_all_shards()
    print(f"Loaded {len(HOT_CACHE)} subjects")
    print()


def test_long_markdown_response():
    """Test with a realistic long markdown AI response about WordPress."""
    print("TEST 1: Long markdown WordPress response")
    print("-" * 50)

    text = """
# How to Build a Custom WordPress Plugin

## Step 1: Setting Up the Plugin File

Create a new file called `my-plugin.php` in `wp-content/plugins/my-plugin/`:

```php
<?php
/**
 * Plugin Name: My Custom Plugin
 * Description: A sample plugin
 * Version: 1.0
 */

// Prevent direct access
if (!defined('ABSPATH')) exit;

// Hook into WordPress
add_action('init', 'mcp_init');
add_action('wp_enqueue_scripts', 'mcp_enqueue_assets');
add_action('admin_enqueue_scripts', 'mcp_admin_assets');

function mcp_enqueue_assets() {
    wp_enqueue_script('mcp-frontend', plugin_dir_url(__FILE__) . 'js/frontend.js', array('jquery'), '1.0', true);
    wp_enqueue_style('mcp-styles', plugin_dir_url(__FILE__) . 'css/style.css');
    wp_localize_script('mcp-frontend', 'mcpAjax', array(
        'ajaxurl' => admin_url('admin-ajax.php'),
        'nonce' => wp_create_nonce('mcp_nonce'),
    ));
}

function mcp_admin_assets($hook) {
    if ($hook !== 'toplevel_page_mcp-settings') return;
    wp_enqueue_script('mcp-admin', plugin_dir_url(__FILE__) . 'js/admin.js');
}
```

## Step 2: Adding a Settings Page

Use `add_menu_page()` to register your admin page:

```php
add_action('admin_menu', function() {
    add_menu_page(
        'My Plugin Settings',
        'My Plugin',
        'manage_options',
        'mcp-settings',
        'mcp_settings_page'
    );
});

function mcp_settings_page() {
    if (!current_user_can('manage_options')) {
        wp_die('Unauthorized');
    }

    // Save settings
    if (isset($_POST['mcp_nonce']) && wp_verify_nonce($_POST['mcp_nonce'], 'mcp_save')) {
        update_option('mcp_setting_1', sanitize_text_field($_POST['setting_1']));
    }

    $value = get_option('mcp_setting_1', '');
    ?>
    <div class="wrap">
        <h1>Settings</h1>
        <form method="post">
            <?php wp_nonce_field('mcp_save', 'mcp_nonce'); ?>
            <input type="text" name="setting_1" value="<?php echo esc_attr($value); ?>">
            <?php submit_button(); ?>
        </form>
    </div>
    <?php
}
```

## Step 3: Custom Post Type

Register a custom post type using `register_post_type()`:

```php
add_action('init', function() {
    register_post_type('portfolio', array(
        'labels' => array('name' => 'Portfolio', 'singular_name' => 'Project'),
        'public' => true,
        'has_archive' => true,
        'show_in_rest' => true,
        'supports' => array('title', 'editor', 'thumbnail'),
    ));

    register_taxonomy('project_type', 'portfolio', array(
        'labels' => array('name' => 'Project Types'),
        'hierarchical' => true,
        'show_in_rest' => true,
    ));
});
```

## Step 4: AJAX Handler

```php
add_action('wp_ajax_mcp_save_data', 'mcp_ajax_handler');
add_action('wp_ajax_nopriv_mcp_save_data', 'mcp_ajax_handler');

function mcp_ajax_handler() {
    check_ajax_referer('mcp_nonce', 'nonce');

    $data = sanitize_text_field($_POST['data']);
    $result = wp_insert_post(array(
        'post_title' => $data,
        'post_type' => 'portfolio',
        'post_status' => 'draft',
    ));

    if (is_wp_error($result)) {
        wp_send_json_error('Failed to save');
    }

    wp_send_json_success(array('id' => $result));
}
```

**Important**: Always use `wp_verify_nonce()` and `sanitize_text_field()` to secure your forms. Use `esc_html()` and `esc_attr()` when outputting data.
"""

    r = verify_text(text)
    claims = r['summary']['total_claims']
    verified = r['summary']['verified']
    not_found = r['summary']['not_found']

    print(f"  Claims: {claims}, Verified: {verified}, Not found: {not_found}")

    # Should find many real functions and zero hallucinations
    ok = not_found == 0 and verified > 10
    print(f"  Result: {'PASS' if ok else 'FAIL'} — {'clean code, no hallucinations' if ok else f'{not_found} false positives!'}")
    if r['not_found']:
        for nf in r['not_found']:
            print(f"    FALSE POSITIVE: {nf['name']}")
    print()
    return ok


def test_mixed_language_prose():
    """Test with prose mixing all four languages — realistic Stack Overflow answer style."""
    print("TEST 2: Mixed language prose (SO answer style)")
    print("-" * 50)

    text = """
    When working with JSON data across different frameworks, you need to
    know the right functions for each:

    In **Python**, use `json.dumps()` to serialize and `json.loads()` to
    deserialize. Don't use `json.stringify()` — that's JavaScript, not Python.
    For file operations, `os.path.join()` builds paths and `pathlib.Path`
    gives you an object-oriented interface.

    In **JavaScript/Node.js**, it's `JSON.parse()` and `JSON.stringify()`.
    For file I/O, use `fs.readFile()` or `fs.readFileSync()`. Don't use
    `fs.readFilePromise()` — it doesn't exist. The promise version is
    `fs.promises.readFile()`.

    In **WordPress**, enqueue scripts with `wp_enqueue_script()` — never
    use `wp_register_scripts()` (note the plural, it's wrong — the correct
    function is `wp_register_script()`).

    In **Laravel**, use `collect()` to create collections, `dd()` for
    debugging, and `config()` to access configuration. Don't write
    `collection()` — it's `collect()`.

    Common mistakes I see:
    - Using `re.find_all()` in Python (correct: `re.findall()`)
    - Using `math.square_root()` (correct: `math.sqrt()`)
    - Using `Array.prototype.flat_map()` in JS (correct: `flatMap`, no underscore)
    """

    r = verify_text(text)
    verified = r['summary']['verified']
    not_found = r['summary']['not_found']

    print(f"  Claims: {r['summary']['total_claims']}, Verified: {verified}, Not found: {not_found}")

    # Should catch the hallucinations and verify the real ones
    caught_names = [nf['name'] for nf in r['not_found']]
    expected_catches = ['json.stringify', 'fs.readFilePromise', 'wp_register_scripts',
                       're.find_all', 'math.square_root', 'Array.prototype.flat_map']

    caught = sum(1 for e in expected_catches if e in caught_names)
    print(f"  Expected catches: {len(expected_catches)}, Actually caught: {caught}")

    ok = caught >= 4 and verified >= 8
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    if r['not_found']:
        for nf in r['not_found']:
            sug = ', '.join(nf.get('suggestions', [])[:2])
            print(f"    Caught: {nf['name']}" + (f" -> {sug}" if sug else ""))
    print()
    return ok


def test_code_with_comments():
    """Test with inline comments and mixed prose in code."""
    print("TEST 3: Code with inline comments")
    print("-" * 50)

    text = """
```python
import json
import os
from collections import defaultdict
from pathlib import Path

# Parse the JSON config file
# Note: don't use json.load_file() — it doesn't exist
# Use json.load() with an open file handle instead
with open('config.json') as f:
    config = json.load(f)

# Build the file path — use os.path.join, NOT os.path.concat
file_path = os.path.join(config['base_dir'], 'output.txt')

# Process data using collections.OrderedDict
from collections import OrderedDict
data = OrderedDict(sorted(config.items()))

# Write results — use json.dumps, not json.stringify
output = json.dumps(data, indent=2)

# Use pathlib for modern file operations
p = Path(file_path)
p.write_text(output)

# Get random sample — use random.sample, not random.pick
import random
sample = random.sample(range(100), 10)

# Calculate — use math.sqrt, not math.square_root
import math
result = math.sqrt(sum(x**2 for x in sample))
```
"""

    r = verify_text(text)
    print(f"  Claims: {r['summary']['total_claims']}, Verified: {r['summary']['verified']}, Not found: {r['summary']['not_found']}")

    # Hallucinations mentioned in comments ARE correctly caught
    ok = r['summary']['verified'] >= 5
    expected_catches = ['json.stringify', 'os.path.concat', 'math.square_root', 'random.pick']
    caught_names = [nf['name'] for nf in r['not_found']]
    real_catches = sum(1 for e in expected_catches if e in caught_names)
    print(f"  Hallucinations in comments caught: {real_catches}/{len(expected_catches)}")
    ok = ok and real_catches >= 3
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    if r['not_found']:
        for nf in r['not_found']:
            sug = ', '.join(nf.get('suggestions', [])[:2])
            print(f"    Caught: {nf['name']}" + (f" -> {sug}" if sug else ""))
    print()
    return ok


def test_laravel_tutorial():
    """Test with a realistic Laravel tutorial response."""
    print("TEST 4: Laravel tutorial response")
    print("-" * 50)

    text = """
## Laravel Routes and Controllers

Define routes in `routes/web.php`:

```php
Route::get('/posts', [PostController::class, 'index']);
Route::post('/posts', [PostController::class, 'store']);
Route::get('/posts/{id}', [PostController::class, 'show']);
```

In your controller, use the `collect()` helper to work with collections
and `config()` to access configuration:

```php
class PostController extends Controller
{
    public function index()
    {
        $posts = Post::all();
        $filtered = collect($posts)->filter(fn($p) => $p->published);

        return view('posts.index', compact('filtered'));
    }

    public function store(Request $request)
    {
        $validated = $request->validate([
            'title' => 'required|string|max:255',
            'body' => 'required',
        ]);

        $post = Post::create($validated);

        return redirect()->route('posts.show', $post->id);
    }
}
```

For debugging, use `dd()` to dump and die, or `dump()` to dump without
stopping execution. Access environment variables with `env()` and
encrypt data with `encrypt()`.

**Tip**: Use `Cache::remember()` for expensive queries and `Storage::put()`
for file uploads.
"""

    r = verify_text(text)
    print(f"  Claims: {r['summary']['total_claims']}, Verified: {r['summary']['verified']}, Not found: {r['summary']['not_found']}")

    ok = r['summary']['verified'] >= 4 and r['summary']['not_found'] == 0
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    if r['not_found']:
        for nf in r['not_found']:
            print(f"    FALSE POSITIVE: {nf['name']}")
    print()
    return ok


def test_node_api_response():
    """Test with a Node.js API tutorial."""
    print("TEST 5: Node.js API tutorial")
    print("-" * 50)

    text = """
## Building a REST API with Node.js

```javascript
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const url = require('url');
const { URL } = require('url');

const server = http.createServer((req, res) => {
    const parsedUrl = new URL(req.url, `http://${req.headers.host}`);

    if (parsedUrl.pathname === '/api/data' && req.method === 'GET') {
        const filePath = path.join(__dirname, 'data.json');
        fs.readFile(filePath, 'utf8', (err, data) => {
            if (err) {
                res.writeHead(500);
                return res.end(JSON.stringify({ error: 'Read failed' }));
            }
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(data);
        });
    }

    if (parsedUrl.pathname === '/api/hash' && req.method === 'POST') {
        let body = '';
        req.on('data', chunk => body += chunk);
        req.on('end', () => {
            const hash = crypto.createHash('sha256').update(body).digest('hex');
            res.writeHead(200);
            res.end(JSON.stringify({ hash }));
        });
    }
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => console.log(`Server running on ${PORT}`));
```

Use `Buffer.from()` to handle binary data and `Array.prototype.map()`
to transform arrays. For promises, use `Promise.all()` to run multiple
async operations concurrently.
"""

    r = verify_text(text)
    print(f"  Claims: {r['summary']['total_claims']}, Verified: {r['summary']['verified']}, Not found: {r['summary']['not_found']}")

    ok = r['summary']['verified'] >= 5 and r['summary']['not_found'] == 0
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    if r['not_found']:
        for nf in r['not_found']:
            print(f"    FALSE POSITIVE: {nf['name']}")
    print()
    return ok


def test_hallucinated_tutorial():
    """Test with a tutorial containing deliberate hallucinations across languages."""
    print("TEST 6: Tutorial with deliberate hallucinations")
    print("-" * 50)

    text = """
## Cross-Framework Data Processing

### Python
```python
import json
data = json.load_from_file('data.json')  # WRONG
result = json.stringify(data)             # WRONG — JS habit
items = collections.HashMap()             # WRONG — Java habit
matches = re.find_all(r'\\d+', text)      # WRONG — underscore
```

### JavaScript
```javascript
const data = JSON.load('data.json');           // WRONG — Python habit
const file = fs.readFilePromise('data.json');   // WRONG — doesn't exist
const server = http.createWebServer();          // WRONG
const flat = arr.flat_map(x => x);             // WRONG — underscore
```

### WordPress
```php
wp_register_scripts('my-script', 'url');     // WRONG — plural
wp_enqueue_javascripts('my-js');             // WRONG — doesn't exist
$user = get_currentuserinfo();               // WRONG — deprecated
$request = wp_is_rest_request();             // WRONG — wrong name
```

### Laravel
```php
$items = collection([1, 2, 3]);     // WRONG — it's collect()
dump_die($variable);                // WRONG — it's dd()
$cfg = get_config('app.name');      // WRONG — it's config()
return render_template('view');     // WRONG — Flask habit
```
"""

    r = verify_text(text)
    caught = r['summary']['not_found']
    deprecated = r['summary']['deprecated']
    total_bad = caught + deprecated

    print(f"  Claims: {r['summary']['total_claims']}, Verified: {r['summary']['verified']}, Caught: {caught}, Deprecated: {deprecated}")

    # Should catch most of these
    ok = total_bad >= 8
    print(f"  Result: {'PASS' if ok else 'FAIL'} — caught {total_bad} hallucinations")
    if r['not_found']:
        for nf in r['not_found']:
            sug = ', '.join(nf.get('suggestions', [])[:2])
            print(f"    Caught: {nf['name']}" + (f" -> {sug}" if sug else ""))
    if r['deprecated']:
        for d in r['deprecated']:
            print(f"    Deprecated: {d['name']} — {d.get('message', '')}")
    print()
    return ok


def test_short_chat_response():
    """Test with very short, casual AI chat responses."""
    print("TEST 7: Short chat responses")
    print("-" * 50)

    texts = [
        "Just use wp_enqueue_script() for that",
        "Try json.dumps() to convert it",
        "fs.readFile() is what you want",
        "Yeah, collect() and dd() are your friends in Laravel",
        "The function is math.sqrt() not math.squareRoot()",
    ]

    total_ok = 0
    for text in texts:
        r = verify_text(text)
        verified = r['summary']['verified']
        ok = verified >= 1
        status = "PASS" if ok else "FAIL"
        print(f"  \"{text[:50]}...\" — {verified} verified, {status}")
        if ok: total_ok += 1

    print(f"  Result: {total_ok}/{len(texts)}")
    print()
    return total_ok == len(texts)


def test_empty_and_edge():
    """Test edge cases with real-world messiness."""
    print("TEST 8: Edge cases and messy input")
    print("-" * 50)

    tests = [
        ("Empty string", "", 0, 0),
        ("Just punctuation", "!!! ??? ... --- <<<>>>", 0, 0),
        ("Just numbers", "42 3.14 100 999", 0, 0),
        ("URL with function name", "https://developer.wordpress.org/reference/functions/wp_enqueue_script/", 0, 0),
        ("Backtick-wrapped", "`wp_enqueue_script`", 1, 0),
        ("Double backtick", "``wp_enqueue_script``", 1, 0),
    ]

    passed = 0
    for label, text, min_verified, max_not_found in tests:
        r = verify_text(text)
        v = r['summary']['verified']
        nf = r['summary']['not_found']
        ok = v >= min_verified and nf <= max_not_found
        status = "PASS" if ok else "FAIL"
        print(f"  {label}: {v} verified, {nf} not found — {status}")
        if ok: passed += 1

    print(f"  Result: {passed}/{len(tests)}")
    print()
    return passed == len(tests)


def main():
    print("=" * 60)
    print("VFAULT MESSY REAL-WORLD TEST SUITE")
    print("=" * 60)
    print()

    setup()

    tests = [
        test_long_markdown_response,
        test_mixed_language_prose,
        test_code_with_comments,
        test_laravel_tutorial,
        test_node_api_response,
        test_hallucinated_tutorial,
        test_short_chat_response,
        test_empty_and_edge,
    ]

    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(False)

    passed = sum(results)
    total = len(results)

    print("=" * 60)
    print(f"RESULTS: {passed}/{total} tests passed")
    print("=" * 60)

    if passed == total:
        print("ALL TESTS PASSED")
    else:
        print(f"{total - passed} TESTS FAILED")
        sys.exit(1)


if __name__ == '__main__':
    main()
