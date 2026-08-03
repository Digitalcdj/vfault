#!/usr/bin/env python3
"""
VFault test suite
==============
Runs all verification tests and produces a benchmark report.

Usage:
    python3 tests/run_tests.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from vfault import load_hot_cache, verify_text, verify_claim, compare_params, HOT_CACHE

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'shards', 'wordpress', 'wordpress.db')


def setup():
    conn = sqlite3.connect(DB_PATH)
    load_hot_cache(conn)
    conn.close()
    print(f"Store loaded: {len(HOT_CACHE)} subjects")
    print()


def test_known_functions():
    """Test that core WordPress functions are found and verified."""
    print("TEST 1: Known functions exist")
    print("-" * 50)

    must_exist = [
        'wp_enqueue_script', 'wp_enqueue_style', 'wp_register_script',
        'add_action', 'add_filter', 'wp_nonce_field', 'wp_verify_nonce',
        'wp_create_nonce', 'wp_get_current_user', 'get_current_user_id',
        'register_post_type', 'register_taxonomy', 'get_option',
        'update_option', 'get_post_meta', 'update_post_meta',
        'wp_insert_post', 'wp_delete_post', 'wp_mail',
        'current_user_can', 'sanitize_text_field', 'esc_html',
        'esc_attr', 'esc_url', 'wp_redirect', 'wp_safe_redirect',
        'register_rest_route', 'rest_ensure_response',
        'wp_remote_get', 'wp_remote_retrieve_body',
        'register_block_type', 'register_block_pattern',
        'wp_schedule_event', 'wp_next_scheduled',
    ]

    passed = 0
    failed = 0
    for func in must_exist:
        r = verify_claim(func)
        if r['exists']:
            passed += 1
        else:
            print(f"  FAIL: {func} not found")
            failed += 1

    print(f"  {passed}/{len(must_exist)} passed")
    if failed:
        print(f"  {failed} FAILED")
    print()
    return failed == 0


def test_hallucinations_caught():
    """Test that fake function names are caught."""
    print("TEST 2: Hallucinations caught")
    print("-" * 50)

    must_not_exist = [
        'wp_register_scripts', 'wp_enqueue_javascripts',
        'wp_add_styles', 'wp_sanitize_input',
        'create_post_type', 'add_custom_taxonomy',
        'wp_cron_schedule', 'check_nonce',
        'wp_rest_response', 'wp_localize_scripts',
    ]

    passed = 0
    failed = 0
    for func in must_not_exist:
        r = verify_claim(func)
        if not r['exists']:
            passed += 1
        else:
            print(f"  FAIL: {func} should not exist but was found")
            failed += 1

    print(f"  {passed}/{len(must_not_exist)} caught")
    if failed:
        print(f"  {failed} FAILED")
    print()
    return failed == 0


def test_deprecated_functions():
    """Test that deprecated functions are correctly flagged."""
    print("TEST 3: Deprecated functions flagged")
    print("-" * 50)

    must_be_deprecated = {
        'get_currentuserinfo': 'wp_get_current_user',
    }

    passed = 0
    failed = 0
    for func, replacement in must_be_deprecated.items():
        r = verify_claim(func)
        if r.get('status') == 'deprecated':
            passed += 1
        else:
            print(f"  FAIL: {func} should be deprecated but status is {r.get('status')}")
            failed += 1

    print(f"  {passed}/{len(must_be_deprecated)} passed")
    if failed:
        print(f"  {failed} FAILED")
    print()
    return failed == 0


def test_not_deprecated():
    """Test that active functions are NOT marked deprecated."""
    print("TEST 4: Active functions not marked deprecated")
    print("-" * 50)

    must_be_active = [
        'current_user_can', 'wp_get_current_user', 'wp_enqueue_script',
        'add_action', 'register_post_type', 'wp_create_nonce',
    ]

    passed = 0
    failed = 0
    for func in must_be_active:
        r = verify_claim(func)
        if r.get('status') == 'verified':
            passed += 1
        else:
            print(f"  FAIL: {func} should be active but status is {r.get('status')}")
            failed += 1

    print(f"  {passed}/{len(must_be_active)} passed")
    if failed:
        print(f"  {failed} FAILED")
    print()
    return failed == 0


def test_suggestions():
    """Test that hallucinated functions get useful suggestions."""
    print("TEST 5: Correction suggestions provided")
    print("-" * 50)

    should_suggest = {
        'wp_register_scripts': 'wp_register_script',
        'wp_enqueue_javascripts': 'wp_enqueue_script',
        'create_post_type': 'register_post_type',
    }

    passed = 0
    failed = 0
    for fake, expected in should_suggest.items():
        r = verify_claim(fake)
        suggestions = r.get('suggestions', [])
        if expected in suggestions:
            passed += 1
        else:
            print(f"  FAIL: {fake} should suggest {expected}, got {suggestions}")
            failed += 1

    print(f"  {passed}/{len(should_suggest)} passed")
    if failed:
        print(f"  {failed} FAILED")
    print()
    return failed == 0


def test_parameter_comparison():
    """Test parameter mismatch detection."""
    print("TEST 6: Parameter mismatch detection")
    print("-" * 50)

    # Old params (pre-6.3)
    r1 = compare_params('wp_enqueue_script',
        'string $handle, string $src, string[] $deps, string|bool|null $ver, bool $in_footer')
    old_caught = r1 and r1['status'] == 'param_mismatch'

    # Current params
    r2 = compare_params('wp_enqueue_script',
        'string $handle, string $src, string[] $deps, string|bool|null $ver, array|bool $args')
    current_ok = r2 and r2['status'] == 'params_verified'

    results = [
        ('Old params detected as mismatch', old_caught),
        ('Current params verified', current_ok),
    ]

    passed = sum(1 for _, ok in results if ok)
    for label, ok in results:
        if not ok:
            print(f"  FAIL: {label}")

    print(f"  {passed}/{len(results)} passed")
    print()
    return passed == len(results)


def test_class_methods():
    """Test that class methods are recognized."""
    print("TEST 7: Class methods recognized")
    print("-" * 50)

    methods = {
        'get_param': 'WP_REST_Request',
        'get_results': 'wpdb',
        'have_posts': 'WP_Query',
        'prepare': 'wpdb',
        'get_body': 'WP_REST_Request',
    }

    passed = 0
    failed = 0
    for method, expected_class in methods.items():
        r = verify_claim(method)
        if r['exists']:
            passed += 1
        else:
            print(f"  FAIL: {method} ({expected_class}) not found")
            failed += 1

    print(f"  {passed}/{len(methods)} passed")
    if failed:
        print(f"  {failed} FAILED")
    print()
    return failed == 0


def test_false_positive_resistance():
    """Test that capability strings and user hooks don't trigger false positives."""
    print("TEST 8: False positive resistance")
    print("-" * 50)

    text_with_caps = """
    'capability_type' => array( 'book', 'books' ),
    'capabilities' => array(
        'edit_post' => 'edit_book',
        'read_post' => 'read_book',
        'delete_post' => 'delete_book',
        'edit_posts' => 'edit_books',
    ),

    add_action( 'wp_ajax_my_custom_action', 'my_handler' );
    add_action( 'my_hourly_cron', 'my_cron_callback' );

    if ( ! wp_next_scheduled( 'my_hourly_event' ) ) {
        wp_schedule_event( time(), 'hourly', 'my_hourly_event' );
    }
    """

    r = verify_text(text_with_caps)
    fp = r['summary']['not_found']

    if fp == 0:
        print(f"  PASSED — 0 false positives")
    else:
        print(f"  FAIL — {fp} false positives:")
        for v in r['not_found']:
            print(f"    {v['name']}")

    print()
    return fp == 0


def test_full_verification():
    """Test end-to-end verification on realistic input."""
    print("TEST 9: Full verification on realistic AI output")
    print("-" * 50)

    realistic = """
    To add custom scripts in WordPress, use the wp_enqueue_scripts hook
    with add_action. Inside your callback, call wp_register_script()
    to register your JavaScript file, then wp_enqueue_script() to load it.
    For styles, use wp_enqueue_style() and wp_register_style().
    You can check if a user is logged in with is_user_logged_in()
    and get their data with wp_get_current_user().
    """

    r = verify_text(realistic)
    all_verified = r['summary']['not_found'] == 0
    found_some = r['summary']['verified'] > 0

    print(f"  Claims: {r['summary']['total_claims']}")
    print(f"  Verified: {r['summary']['verified']}")
    print(f"  Not found: {r['summary']['not_found']}")
    print(f"  Result: {'PASSED' if all_verified and found_some else 'FAIL'}")
    print()
    return all_verified and found_some


def main():
    print("=" * 60)
    print("VFAULT WORDPRESS — TEST SUITE")
    print("=" * 60)
    print()

    setup()

    tests = [
        test_known_functions,
        test_hallucinations_caught,
        test_deprecated_functions,
        test_not_deprecated,
        test_suggestions,
        test_parameter_comparison,
        test_class_methods,
        test_false_positive_resistance,
        test_full_verification,
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
