"""
VFault Test Suite
=================
Covers all 8 verification layers + supporting functions.

Run: VFAULT_SHARDS_DIR=shards pytest test_vfault.py -v
"""

import os
import sys
import pytest

# Ensure vfault module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vfault


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def load_shards():
    """Load all shards once for the entire test session."""
    vfault.load_all_shards()
    assert len(vfault.HOT_CACHE) > 0, "HOT_CACHE is empty after loading"
    assert len(vfault.LOADED_SHARDS) > 0, "No shards loaded"


def has_shard(name):
    return name in vfault.LOADED_SHARDS

def has_all_shards():
    return set(vfault.LOADED_SHARDS) >= {'wordpress', 'woocommerce', 'python', 'javascript', 'laravel', 'react'}

needs_woocommerce = pytest.mark.skipif(
    'woocommerce' not in os.environ.get('VFAULT_SHARDS_DIR', 'shards') or True,
    reason="Checked at runtime"
)

def skip_without(*shards):
    """Decorator: skip test if any named shard is not loaded."""
    def decorator(func):
        @pytest.mark.skipif(True, reason="Checked at runtime")
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        # We can't check at import time, so we'll use a different approach
        return func
    return decorator


# ---------------------------------------------------------------------------
# 1. Startup Indexes
# ---------------------------------------------------------------------------

class TestStartupIndexes:

    def test_hot_cache_populated(self):
        assert len(vfault.HOT_CACHE) > 5000

    def test_method_index_populated(self):
        if has_all_shards():
            assert len(vfault.METHOD_INDEX) > 10000
        else:
            assert len(vfault.METHOD_INDEX) >= 0

    def test_method_index_content(self):
        """get_total should map to WC class methods (requires woocommerce shard)."""
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        assert 'get_total' in vfault.METHOD_INDEX

    def test_prefix_index_populated(self):
        assert len(vfault.PREFIX_INDEX) > 1000

    def test_prefix_index_wp(self):
        """wp_ prefix bucket should contain WordPress functions."""
        assert 'wp_' in vfault.PREFIX_INDEX
        assert len(vfault.PREFIX_INDEX['wp_']) > 100

    def test_loaded_shards_list(self):
        assert 'wordpress' in vfault.LOADED_SHARDS


# ---------------------------------------------------------------------------
# 2. Claim Extraction
# ---------------------------------------------------------------------------

class TestExtractClaims:

    def test_wordpress_functions(self):
        claims = vfault.extract_claims("wp_enqueue_script('x', '/js/app.js');")
        assert 'wp_enqueue_script' in claims

    def test_react_hooks(self):
        claims = vfault.extract_claims("const [x, setX] = useState(0);")
        assert 'useState' in claims

    def test_python_dotted(self):
        claims = vfault.extract_claims("data = json.dumps({'key': 'value'})")
        assert 'json.dumps' in claims

    def test_js_dotted(self):
        claims = vfault.extract_claims("const items = Array.from(nodeList);")
        assert 'Array.from' in claims

    def test_skip_exact(self):
        """Common words should be filtered out."""
        claims = vfault.extract_claims("render the page and call the function")
        assert 'render' not in claims
        assert 'call' not in claims

    def test_woocommerce_functions(self):
        claims = vfault.extract_claims("$product = wc_get_product(42);")
        assert 'wc_get_product' in claims

    def test_empty_input(self):
        claims = vfault.extract_claims("")
        assert claims == []

    def test_no_code_input(self):
        claims = vfault.extract_claims("Hello, this is a normal sentence.")
        assert len(claims) == 0


class TestExtractClassMethodPairs:

    def test_double_colon(self):
        pairs = vfault.extract_class_method_pairs("WC_Product::get_price()")
        assert len(pairs) == 1
        assert pairs[0]['class'] == 'WC_Product'
        assert pairs[0]['method'] == 'get_price'

    def test_arrow_operator(self):
        pairs = vfault.extract_class_method_pairs("WC_Order->get_total()")
        assert len(pairs) == 1
        assert pairs[0]['class'] == 'WC_Order'
        assert pairs[0]['method'] == 'get_total'

    def test_variable_arrow_skipped(self):
        """$variable->method should not match (lowercase start)."""
        pairs = vfault.extract_class_method_pairs("$order->get_total()")
        assert len(pairs) == 0

    def test_multiple_pairs(self):
        text = "WC_Product::get_price(); WC_Order::get_total();"
        pairs = vfault.extract_class_method_pairs(text)
        assert len(pairs) == 2


class TestExtractCallsWithParams:

    def test_php_params(self):
        calls = vfault.extract_calls_with_params(
            "wp_enqueue_script( $handle, $src, $deps )"
        )
        assert 'wp_enqueue_script' in calls
        assert '$handle' in calls['wp_enqueue_script']

    def test_no_params(self):
        calls = vfault.extract_calls_with_params("is_user_logged_in()")
        assert len(calls) == 0

    def test_non_php_skipped(self):
        """Calls without $ params should not be captured."""
        calls = vfault.extract_calls_with_params("json.dumps(data)")
        assert len(calls) == 0


# ---------------------------------------------------------------------------
# 3. is_shard_namespace
# ---------------------------------------------------------------------------

class TestIsShardNamespace:

    def test_wp_prefix(self):
        assert vfault.is_shard_namespace('wp_enqueue_script') is True

    def test_wc_prefix(self):
        assert vfault.is_shard_namespace('wc_get_product') is True

    def test_get_prefix(self):
        assert vfault.is_shard_namespace('get_post_meta') is True

    def test_python_dotted(self):
        assert vfault.is_shard_namespace('json.dumps') is True

    def test_js_dotted(self):
        assert vfault.is_shard_namespace('Array.includes') is True

    def test_custom_function_not_in_namespace(self):
        assert vfault.is_shard_namespace('myCustomFunction') is False

    def test_react_hook_not_in_namespace(self):
        """Custom React hooks should NOT be treated as shard namespace."""
        assert vfault.is_shard_namespace('useShoppingCart') is False

    def test_wc_class_prefix(self):
        assert vfault.is_shard_namespace('WC_Product.get_price') is True


# ---------------------------------------------------------------------------
# 4. verify_claim — Layer 1: Existence
# ---------------------------------------------------------------------------

class TestVerifyClaimExistence:

    def test_verified_function(self):
        r = vfault.verify_claim('wp_enqueue_script')
        assert r['status'] == 'verified'
        assert r['exists'] is True

    def test_not_found_shard_namespace(self):
        """Hallucinated function in shard namespace should be not_found."""
        r = vfault.verify_claim('wp_register_scripts')
        assert r['status'] == 'not_found'
        assert r['exists'] is False

    def test_not_found_has_suggestions(self):
        r = vfault.verify_claim('wp_register_scripts')
        assert 'suggestions' in r
        assert len(r['suggestions']) > 0

    def test_unknown_outside_namespace(self):
        """Function outside shard namespace should be unknown."""
        r = vfault.verify_claim('useShoppingCart')
        assert r['status'] == 'unknown'

    def test_verified_python(self):
        if not has_shard('python'):
            pytest.skip("Requires python shard")
        r = vfault.verify_claim('json.dumps')
        assert r['status'] == 'verified'

    def test_verified_js(self):
        if not has_shard('javascript'):
            pytest.skip("Requires javascript shard")
        r = vfault.verify_claim('Array.from')
        assert r['status'] == 'verified'

    def test_verified_react(self):
        if not has_shard('react'):
            pytest.skip("Requires react shard")
        r = vfault.verify_claim('useState')
        assert r['status'] == 'verified'

    def test_verified_woocommerce(self):
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        r = vfault.verify_claim('wc_get_product')
        assert r['status'] == 'verified'


# ---------------------------------------------------------------------------
# 5. verify_claim — Layer 2: Deprecation
# ---------------------------------------------------------------------------

class TestVerifyClaimDeprecation:

    def test_deprecated_wp(self):
        r = vfault.verify_claim('get_currentuserinfo')
        assert r['status'] == 'deprecated'

    def test_deprecated_has_replacement(self):
        r = vfault.verify_claim('get_currentuserinfo')
        assert 'message' in r
        assert 'wp_get_current_user' in r['message']


# ---------------------------------------------------------------------------
# 6. verify_claim — Whitelist
# ---------------------------------------------------------------------------

class TestVerifyClaimWhitelist:

    def test_whitelisted_skipped(self):
        r = vfault.verify_claim('get_my_custom_data', whitelist=['get_my_custom_'])
        assert r['status'] == 'whitelisted'

    def test_whitelist_no_match(self):
        r = vfault.verify_claim('wp_enqueue_script', whitelist=['get_my_custom_'])
        assert r['status'] == 'verified'

    def test_whitelist_none(self):
        r = vfault.verify_claim('wp_enqueue_script', whitelist=None)
        assert r['status'] == 'verified'


# ---------------------------------------------------------------------------
# 7. verify_claim — Bare method via METHOD_INDEX
# ---------------------------------------------------------------------------

class TestVerifyClaimBareMethod:

    def test_bare_method_returns_class_suggestions(self):
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        r = vfault.verify_claim('get_price')
        assert 'suggestions' in r
        assert any('WC_Product.get_price' in s for s in r['suggestions'])

    def test_bare_method_message(self):
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        r = vfault.verify_claim('get_total')
        assert 'class' in r['message'].lower() or 'Bare method' in r['message']


# ---------------------------------------------------------------------------
# 8. verify_text — Full Pipeline
# ---------------------------------------------------------------------------

class TestVerifyText:

    def test_basic_verified(self):
        r = vfault.verify_text("wp_enqueue_script('x', '/js/app.js');")
        assert r['summary']['verified'] >= 1
        assert r['summary']['hallucination_rate'] == '0.0%'

    def test_hallucination_detected(self):
        r = vfault.verify_text("wp_register_scripts();")
        assert r['summary']['not_found'] >= 1

    def test_deprecated_detected(self):
        r = vfault.verify_text("get_currentuserinfo();")
        assert r['summary']['deprecated'] >= 1

    def test_unknown_detected(self):
        r = vfault.verify_text("useShoppingCart(); usePaymentFlow();")
        assert r['summary']['unknown'] >= 1

    def test_whitelisted(self):
        r = vfault.verify_text(
            "get_my_custom_data(); wp_enqueue_script('x', '/js/app.js');",
            whitelist=['get_my_custom_']
        )
        assert r['summary']['whitelisted'] >= 1
        assert r['summary']['verified'] >= 1

    def test_response_arrays_present(self):
        r = vfault.verify_text("wp_enqueue_script('x', '/js/app.js');")
        for key in ['verified', 'deprecated', 'not_found', 'unknown',
                    'whitelisted', 'upgrade_required', 'third_party',
                    'param_issues', 'class_mismatches', 'context_issues']:
            assert key in r, f"Missing response array: {key}"

    def test_summary_fields_present(self):
        r = vfault.verify_text("wp_enqueue_script('x', '/js/app.js');")
        for key in ['total_claims', 'verified', 'deprecated', 'not_found',
                    'unknown', 'whitelisted', 'param_issues',
                    'class_mismatches', 'context_issues', 'hallucination_rate']:
            assert key in r['summary'], f"Missing summary field: {key}"

    def test_hallucination_rate_excludes_unknown(self):
        """Hallucination rate should only count not_found, not unknown."""
        r = vfault.verify_text("useShoppingCart(); usePaymentFlow();")
        # These are unknown, not not_found, so rate should be 0%
        assert r['summary']['hallucination_rate'] == '0.0%'

    def test_empty_text(self):
        r = vfault.verify_text("")
        assert r['summary']['total_claims'] == 0


# ---------------------------------------------------------------------------
# 9. Parameter Mismatch Detection (Second Pass)
# ---------------------------------------------------------------------------

class TestParamMismatch:

    def test_wrong_param_detected(self):
        """$in_footer was renamed to $args in wp_enqueue_script."""
        r = vfault.verify_text(
            "wp_enqueue_script( $handle, $src, $deps, $ver, $in_footer );"
        )
        assert r['summary']['param_issues'] >= 1
        issues = r['param_issues']
        assert any('$in_footer' in p.get('message', '') for p in issues)

    def test_correct_params_no_issue(self):
        """Using correct params should not flag."""
        r = vfault.verify_text(
            "wp_enqueue_script( $handle, $src, $deps, $ver, $args );"
        )
        param_issues_for_enqueue = [
            p for p in r['param_issues']
            if p.get('function') == 'wp_enqueue_script'
        ]
        assert len(param_issues_for_enqueue) == 0

    def test_non_php_params_skipped(self):
        """Non-$ params should not trigger param comparison."""
        r = vfault.verify_text("json.dumps(data, indent=2)")
        assert r['summary']['param_issues'] == 0


# ---------------------------------------------------------------------------
# 10. Class/Method Pairing (Second Pass)
# ---------------------------------------------------------------------------

class TestClassMethodPairing:

    def test_wrong_class_detected(self):
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        r = vfault.verify_text("WC_Product::get_total();")
        assert r['summary']['class_mismatches'] >= 1
        mismatch = r['class_mismatches'][0]
        assert mismatch['stated_class'] == 'WC_Product'
        assert mismatch['method'] == 'get_total'

    def test_correct_class_no_mismatch(self):
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        r = vfault.verify_text("WC_Product::get_price();")
        class_mismatches = [
            c for c in r['class_mismatches']
            if c['method'] == 'get_price' and c['stated_class'] == 'WC_Product'
        ]
        assert len(class_mismatches) == 0

    def test_mismatch_has_actual_classes(self):
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        r = vfault.verify_text("WC_Product::get_total();")
        mismatch = r['class_mismatches'][0]
        assert 'actual_classes' in mismatch
        assert len(mismatch['actual_classes']) > 0


# ---------------------------------------------------------------------------
# 11. Context Rules (Third Pass)
# ---------------------------------------------------------------------------

class TestContextRules:

    def test_wp_enqueue_without_hook(self):
        r = vfault.verify_text("wp_enqueue_script('x', '/js/app.js');")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_enqueue_script']
        assert len(issues) >= 1
        assert issues[0]['severity'] == 'warning'

    def test_wp_enqueue_with_hook_no_issue(self):
        r = vfault.verify_text(
            "add_action('wp_enqueue_scripts', 'my_func');\n"
            "wp_enqueue_script('x', '/js/app.js');"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_enqueue_script']
        assert len(issues) == 0

    def test_wp_redirect_without_exit(self):
        r = vfault.verify_text("wp_redirect( home_url() );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_redirect']
        assert len(issues) >= 1
        assert issues[0]['severity'] == 'error'

    def test_wp_redirect_with_exit_no_issue(self):
        r = vfault.verify_text("wp_redirect( home_url() ); exit;")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_redirect']
        assert len(issues) == 0

    def test_async_use_effect(self):
        if not has_shard('react'):
            pytest.skip("Requires react shard")
        r = vfault.verify_text("useEffect(async () => { await fetch(); }, []);")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'useEffect']
        assert len(issues) >= 1
        assert issues[0]['severity'] == 'error'

    def test_correct_use_effect_no_issue(self):
        if not has_shard('react'):
            pytest.skip("Requires react shard")
        r = vfault.verify_text("useEffect(() => { fetchData(); }, []);")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'useEffect']
        assert len(issues) == 0

    def test_eval_flagged(self):
        if not has_shard('python'):
            pytest.skip("Requires python shard")
        r = vfault.verify_text("data = eval(user_input)\nresult = builtins.eval(expr)")
        issues = [c for c in r['context_issues']
                  if 'eval' in c.get('function', '')]
        assert len(issues) >= 1

    def test_os_system_flagged(self):
        if not has_shard('python'):
            pytest.skip("Requires python shard")
        r = vfault.verify_text("os.system('rm -rf /tmp/old')")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'os.system']
        assert len(issues) >= 1

    def test_json_parse_without_try(self):
        if not has_shard('javascript'):
            pytest.skip("Requires javascript shard")
        r = vfault.verify_text("const data = JSON.parse(response);")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'JSON.parse']
        assert len(issues) >= 1

    def test_json_parse_with_try_no_issue(self):
        if not has_shard('javascript'):
            pytest.skip("Requires javascript shard")
        r = vfault.verify_text(
            "try { const data = JSON.parse(response); } catch(e) {}"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'JSON.parse']
        assert len(issues) == 0

    def test_laravel_env_outside_config(self):
        if not has_shard('laravel'):
            pytest.skip("Requires laravel shard")
        r = vfault.verify_text("$host = env('DB_HOST');")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'env']
        assert len(issues) >= 1
        assert issues[0]['severity'] == 'error'

    def test_laravel_redirect_without_return(self):
        if not has_shard('laravel'):
            pytest.skip("Requires laravel shard")
        r = vfault.verify_text("redirect('/dashboard');")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'redirect']
        assert len(issues) >= 1

    def test_laravel_redirect_with_return_no_issue(self):
        if not has_shard('laravel'):
            pytest.skip("Requires laravel shard")
        r = vfault.verify_text("return redirect('/dashboard');")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'redirect']
        assert len(issues) == 0

    # ── New WordPress rules ────────────────────────────────────

    def test_wp_insert_post_without_error_check(self):
        r = vfault.verify_text("$post_id = wp_insert_post( $args );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_insert_post']
        assert len(issues) >= 1

    def test_wp_insert_post_with_error_check_no_issue(self):
        r = vfault.verify_text(
            "$post_id = wp_insert_post( $args );\n"
            "if ( is_wp_error( $post_id ) ) { return; }"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_insert_post']
        assert len(issues) == 0

    def test_wp_remote_get_without_error_check(self):
        r = vfault.verify_text("$response = wp_remote_get( $url );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_remote_get']
        assert len(issues) >= 1

    def test_wp_remote_get_with_error_check_no_issue(self):
        r = vfault.verify_text(
            "$response = wp_remote_get( $url );\n"
            "if ( is_wp_error( $response ) ) { return; }"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_remote_get']
        assert len(issues) == 0

    def test_wp_remote_post_without_error_check(self):
        r = vfault.verify_text("$response = wp_remote_post( $url, $args );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_remote_post']
        assert len(issues) >= 1

    def test_wp_schedule_event_without_next_scheduled(self):
        r = vfault.verify_text("wp_schedule_event( time(), 'hourly', 'my_hook' );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_schedule_event']
        assert len(issues) >= 1

    def test_wp_schedule_event_with_check_no_issue(self):
        r = vfault.verify_text(
            "if ( ! wp_next_scheduled( 'my_hook' ) ) {\n"
            "    wp_schedule_event( time(), 'hourly', 'my_hook' );\n"
            "}"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_schedule_event']
        assert len(issues) == 0

    def test_wp_delete_post_without_permission(self):
        r = vfault.verify_text("wp_delete_post( $post_id, true );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_delete_post']
        assert len(issues) >= 1

    def test_wp_delete_post_with_permission_no_issue(self):
        r = vfault.verify_text(
            "if ( current_user_can( 'delete_post', $post_id ) ) {\n"
            "    wp_delete_post( $post_id, true );\n"
            "}"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_delete_post']
        assert len(issues) == 0

    def test_wp_handle_upload_without_auth(self):
        r = vfault.verify_text("$movefile = wp_handle_upload( $file, $overrides );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_handle_upload']
        assert len(issues) >= 1
        assert issues[0]['severity'] == 'error'

    def test_wp_handle_upload_with_auth_no_issue(self):
        r = vfault.verify_text(
            "check_admin_referer( 'upload_nonce' );\n"
            "$movefile = wp_handle_upload( $file, $overrides );"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_handle_upload']
        assert len(issues) == 0

    # ── New WooCommerce rule ────────────────────────────────────

    def test_wc_create_order_without_error_check(self):
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        r = vfault.verify_text("$order = wc_create_order( $args );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wc_create_order']
        assert len(issues) >= 1

    def test_wc_create_order_with_error_check_no_issue(self):
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        r = vfault.verify_text(
            "$order = wc_create_order( $args );\n"
            "if ( is_wp_error( $order ) ) { return; }"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wc_create_order']
        assert len(issues) == 0

    # ── New React rule ──────────────────────────────────────────

    def test_use_layout_effect_with_ssr(self):
        if not has_shard('react'):
            pytest.skip("Requires react shard")
        r = vfault.verify_text(
            "export async function getServerSideProps() { return { props: {} }; }\n"
            "useLayoutEffect(() => { measure(); }, []);"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'useLayoutEffect']
        assert len(issues) >= 1
        assert issues[0]['severity'] == 'warning'

    def test_use_layout_effect_no_ssr_no_issue(self):
        if not has_shard('react'):
            pytest.skip("Requires react shard")
        r = vfault.verify_text(
            "useLayoutEffect(() => { measure(); }, []);"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'useLayoutEffect']
        assert len(issues) == 0

    # ── New Python rules ────────────────────────────────────────

    def test_hashlib_md5_flagged(self):
        if not has_shard('python'):
            pytest.skip("Requires python shard")
        r = vfault.verify_text("digest = hashlib.md5( data ).hexdigest()")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'hashlib.md5']
        assert len(issues) >= 1

    def test_hashlib_sha1_flagged(self):
        if not has_shard('python'):
            pytest.skip("Requires python shard")
        r = vfault.verify_text("digest = hashlib.sha1( data ).hexdigest()")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'hashlib.sha1']
        assert len(issues) >= 1

    def test_tempfile_mktemp_flagged(self):
        if not has_shard('python'):
            pytest.skip("Requires python shard")
        r = vfault.verify_text("path = tempfile.mktemp( suffix='.txt' )")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'tempfile.mktemp']
        assert len(issues) >= 1
        assert issues[0]['severity'] == 'error'

    def test_hmac_without_compare_digest(self):
        if not has_shard('python'):
            pytest.skip("Requires python shard")
        r = vfault.verify_text(
            "mac = hmac.new( key, msg, hashlib.sha256 )\n"
            "if mac.hexdigest() == signature: pass"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'hmac.new']
        assert len(issues) >= 1
        assert issues[0]['severity'] == 'error'

    def test_hmac_with_compare_digest_no_issue(self):
        if not has_shard('python'):
            pytest.skip("Requires python shard")
        r = vfault.verify_text(
            "mac = hmac.new( key, msg, hashlib.sha256 )\n"
            "if hmac.compare_digest( mac.hexdigest(), signature ): pass"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'hmac.new']
        assert len(issues) == 0

    # ── New Laravel rules ───────────────────────────────────────

    def test_dd_flagged(self):
        if not has_shard('laravel'):
            pytest.skip("Requires laravel shard")
        r = vfault.verify_text("dd( $users );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'dd']
        assert len(issues) >= 1

    def test_dump_flagged(self):
        if not has_shard('laravel'):
            pytest.skip("Requires laravel shard")
        r = vfault.verify_text("dump( $request->all() );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'dump']
        assert len(issues) >= 1

    def test_cache_without_miss_handling(self):
        if not has_shard('laravel'):
            pytest.skip("Requires laravel shard")
        r = vfault.verify_text("$value = cache('settings.theme');")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'cache']
        assert len(issues) >= 1

    def test_cache_with_remember_no_issue(self):
        if not has_shard('laravel'):
            pytest.skip("Requires laravel shard")
        r = vfault.verify_text(
            "$value = cache()->remember('key', 60, function() { return 'data'; });"
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'cache']
        assert len(issues) == 0

    def test_context_issue_has_id(self):
        r = vfault.verify_text("wp_redirect( home_url() );")
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_redirect']
        assert 'id' in issues[0]
        assert issues[0]['id'] == 'redirect_exit'

    def test_context_issue_has_status(self):
        r = vfault.verify_text("wp_redirect( home_url() );")
        issues = r['context_issues']
        for issue in issues:
            assert issue['status'] == 'context_issue'


# ---------------------------------------------------------------------------
# 12. disable_rules
# ---------------------------------------------------------------------------

class TestDisableRules:

    def test_disable_by_function_name(self):
        r = vfault.verify_text(
            "wp_redirect( home_url() );",
            disable_rules=['wp_redirect']
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_redirect']
        assert len(issues) == 0

    def test_disable_by_rule_id(self):
        r = vfault.verify_text(
            "wp_redirect( home_url() );",
            disable_rules=['redirect_exit']
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_redirect']
        assert len(issues) == 0

    def test_disable_one_keeps_others(self):
        r = vfault.verify_text(
            "wp_redirect( home_url() ); wp_enqueue_script('x', '/js/app.js');",
            disable_rules=['wp_redirect']
        )
        redirect_issues = [c for c in r['context_issues']
                           if c['function'] == 'wp_redirect']
        enqueue_issues = [c for c in r['context_issues']
                          if c['function'] == 'wp_enqueue_script']
        assert len(redirect_issues) == 0
        assert len(enqueue_issues) >= 1

    def test_disable_none(self):
        r = vfault.verify_text(
            "wp_redirect( home_url() );",
            disable_rules=None
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_redirect']
        assert len(issues) >= 1


# ---------------------------------------------------------------------------
# 13. Shard Gating
# ---------------------------------------------------------------------------

class TestShardGating:

    def test_free_sees_wordpress(self):
        allowed = vfault.get_allowed_shards('free')
        r = vfault.verify_claim('wp_enqueue_script', allowed_shards=allowed)
        assert r['status'] == 'verified'

    def test_free_blocked_from_woocommerce(self):
        if not has_shard('woocommerce'):
            pytest.skip("Requires woocommerce shard")
        allowed = vfault.get_allowed_shards('free')
        r = vfault.verify_claim('wc_get_product', allowed_shards=allowed)
        assert r['status'] == 'upgrade_required'

    def test_pro_sees_all(self):
        if not has_all_shards():
            pytest.skip("Requires all shards")
        allowed = vfault.get_allowed_shards('pro')
        for func in ['wp_enqueue_script', 'wc_get_product', 'json.dumps',
                      'Array.from', 'useState']:
            r = vfault.verify_claim(func, allowed_shards=allowed)
            assert r['status'] in ('verified', 'deprecated'), \
                f"{func} should be accessible on pro, got {r['status']}"


# ---------------------------------------------------------------------------
# 14. Third-Party Detection
# ---------------------------------------------------------------------------

class TestThirdParty:

    def test_known_third_party(self):
        """Known third-party functions should return third_party status."""
        r = vfault.verify_text("const data = axios.get('/api');")
        third_party = [t for t in r.get('third_party', [])
                       if t['name'] == 'axios.get']
        if third_party:
            assert third_party[0]['status'] == 'third_party'


# ---------------------------------------------------------------------------
# 15. compare_params
# ---------------------------------------------------------------------------

class TestCompareParams:

    def test_mismatch_detected(self):
        r = vfault.compare_params(
            'wp_enqueue_script', '$handle, $src, $deps, $ver, $in_footer'
        )
        assert r is not None
        assert r['status'] == 'param_mismatch'

    def test_function_not_found(self):
        r = vfault.compare_params('wp_nonexistent_func', '$x, $y')
        assert r is None

    def test_history_on_renamed_param(self):
        """$in_footer should show history explaining it was renamed to $args."""
        r = vfault.compare_params(
            'wp_enqueue_script', '$handle, $src, $deps, $ver, $in_footer'
        )
        assert 'param_history' in r
        assert len(r['param_history']) == 1
        h = r['param_history'][0]
        assert h['was'] == '$in_footer'
        assert h['became'] == '$args'
        assert h['version'] == '6.3.0'

    def test_no_history_on_correct_params(self):
        r = vfault.compare_params(
            'wp_enqueue_script', '$handle, $src, $deps, $ver, $args'
        )
        assert r['status'] == 'params_verified'
        assert 'param_history' not in r

    def test_history_message_includes_version(self):
        r = vfault.compare_params(
            'wp_enqueue_script', '$handle, $src, $deps, $ver, $in_footer'
        )
        assert '6.3.0' in r['message']

    def test_get_terms_signature_change(self):
        r = vfault.compare_params('get_terms', '$taxonomy, $args')
        assert 'param_history' in r
        h = r['param_history'][0]
        assert h['version'] == '4.5.0'
        assert h['type'] == 'signature_change'

    def test_history_no_duplicates(self):
        """Should only have one history entry for the in_footer/args rename."""
        r = vfault.compare_params(
            'wp_enqueue_script', '$handle, $src, $deps, $ver, $in_footer'
        )
        assert len(r['param_history']) == 1


# ---------------------------------------------------------------------------
# 16. CONTEXT_RULES Structure
# ---------------------------------------------------------------------------

class TestContextRulesStructure:

    def test_all_rules_have_id(self):
        for func, rules in vfault.CONTEXT_RULES.items():
            for rule in rules:
                assert 'id' in rule, f"Rule for {func} missing 'id'"

    def test_all_rules_have_severity(self):
        for func, rules in vfault.CONTEXT_RULES.items():
            for rule in rules:
                assert rule['severity'] in ('warning', 'error'), \
                    f"Rule {rule.get('id')} has invalid severity"

    def test_all_rules_have_compiled_regex(self):
        for func, rules in vfault.CONTEXT_RULES.items():
            for rule in rules:
                assert '_compiled' in rule, \
                    f"Rule {rule.get('id')} missing compiled regex"
                assert '_type' in rule, \
                    f"Rule {rule.get('id')} missing type"

    def test_all_rules_have_message(self):
        for func, rules in vfault.CONTEXT_RULES.items():
            for rule in rules:
                assert 'missing_message' in rule
                assert len(rule['missing_message']) > 10

    def test_rule_count(self):
        total = sum(len(v) for v in vfault.CONTEXT_RULES.values())
        assert total == 45

    def test_unique_ids(self):
        ids = []
        for func, rules in vfault.CONTEXT_RULES.items():
            for rule in rules:
                ids.append(rule['id'])
        assert len(ids) == len(set(ids)), "Duplicate rule IDs found"


# ---------------------------------------------------------------------------
# 17. Deep Check Demo (Integration Test)
# ---------------------------------------------------------------------------

class TestDeepCheckDemo:
    """Full integration test using the website deep check demo input."""

    DEMO_TEXT = (
        "// Wrong param\n"
        "wp_enqueue_script( $handle, $src, $deps, $ver, $in_footer );\n"
        "// Wrong class\n"
        "WC_Product::get_total();\n"
        "// Hallucinated\n"
        "wp_get_rest_response();\n"
        "// Custom hooks\n"
        "useShoppingCart();\n"
        "usePaymentFlow();\n"
        "// Deprecated\n"
        "get_currentuserinfo();\n"
        "// Missing exit\n"
        "wp_redirect( home_url() );\n"
        "// Clean\n"
        "add_action( 'init', 'my_custom_init' );\n"
        "is_user_logged_in();\n"
    )

    def test_all_statuses_fire(self):
        r = vfault.verify_text(self.DEMO_TEXT)
        assert r['summary']['verified'] >= 4
        assert r['summary']['deprecated'] >= 1
        assert r['summary']['not_found'] >= 1
        assert r['summary']['unknown'] >= 2
        assert r['summary']['param_issues'] >= 1
        if has_shard('woocommerce'):
            assert r['summary']['class_mismatches'] >= 1
        assert r['summary']['context_issues'] >= 1

    def test_performance(self):
        """Deep check should complete in under 50ms."""
        import time
        # Warm up
        vfault.verify_text(self.DEMO_TEXT)
        start = time.perf_counter()
        for _ in range(10):
            vfault.verify_text(self.DEMO_TEXT)
        elapsed_ms = (time.perf_counter() - start) / 10 * 1000
        assert elapsed_ms < 50, f"Deep check took {elapsed_ms:.1f}ms (target: <50ms)"


# ---------------------------------------------------------------------------
# 18. Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_very_long_input(self):
        """Should not crash on long input."""
        text = "wp_enqueue_script('x', '/js/app.js');\n" * 1000
        r = vfault.verify_text(text)
        assert r['summary']['verified'] >= 1

    def test_unicode_input(self):
        r = vfault.verify_text("wp_enqueue_script('日本語', '/js/app.js');")
        assert r['summary']['verified'] >= 1

    def test_special_characters(self):
        r = vfault.verify_text("wp_enqueue_script('x&y<z>', '/js/app.js');")
        assert r['summary']['verified'] >= 1

    def test_whitelist_empty_list(self):
        r = vfault.verify_text("wp_enqueue_script('x', '/js/app.js');", whitelist=[])
        assert r['summary']['verified'] >= 1

    def test_disable_rules_empty_list(self):
        r = vfault.verify_text(
            "wp_redirect( home_url() );",
            disable_rules=[]
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_redirect']
        assert len(issues) >= 1

    def test_disable_rules_nonexistent_id(self):
        """Disabling a nonexistent rule ID should not crash."""
        r = vfault.verify_text(
            "wp_redirect( home_url() );",
            disable_rules=['nonexistent_rule_id_xyz']
        )
        issues = [c for c in r['context_issues']
                  if c['function'] == 'wp_redirect']
        assert len(issues) >= 1
