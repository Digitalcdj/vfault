#!/usr/bin/env python3
"""
VFault WordPress store builder
======================================
Parses WordPress source code and extracts every public function,
hook (action + filter), parameter, return type, version introduced,
and deprecation status into structured triples stored in SQLite.

v2 fixes:
- Docblock-to-function attribution: only matches the IMMEDIATELY
  preceding docblock, preventing filter/hook docblocks inside
  function bodies from being attributed to the next function.
- Separates functions from filters that share the same name.

Usage:
    python3 shards/wordpress/parser.py /path/to/wordpress-source
"""

import re
import os
import sys
import json
import sqlite3


def create_database(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            domain TEXT DEFAULT 'wordpress',
            status TEXT DEFAULT 'verified',
            source_file TEXT,
            since_version TEXT,
            deprecated_version TEXT,
            replacement TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_subject ON triples(subject)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_predicate ON triples(predicate)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_subject_predicate ON triples(subject, predicate)")
    conn.commit()
    return conn


def find_preceding_docblock(content, func_pos):
    """Find the docblock immediately before a function declaration.
    Returns None if there's code between the docblock and function.
    Handles both unindented and tab-indented (pluggable) functions."""
    # Look backwards from function position for */
    before = content[:func_pos].rstrip()
    if not before.endswith('*/'):
        return None

    # Find the start of this docblock
    doc_end = len(before)
    doc_start = before.rfind('/**')
    if doc_start == -1:
        return None

    # Check there's nothing but whitespace between docblock end and function
    between = content[doc_end:func_pos]
    # Allow only whitespace and tabs
    if between.strip():
        return None

    return before[doc_start + 3:doc_end - 2]


def extract_functions(content, filepath):
    """Extract functions with their immediately preceding docblocks."""
    functions = []

    # Find all function declarations (including tab-indented pluggable functions)
    func_pattern = re.compile(
        r'^\s*function\s+(\w+)\s*\((.*?)\)\s*(?::\s*(\S+)\s*)?\{',
        re.MULTILINE | re.DOTALL
    )

    for match in func_pattern.finditer(content):
        func_name = match.group(1)
        params_raw = match.group(2).strip()
        return_type = match.group(3) if match.group(3) else None

        # Get the immediately preceding docblock
        docblock = find_preceding_docblock(content, match.start())

        since = None
        deprecated = None
        replacement = None
        description = None
        param_annotations = {}

        if docblock:
            # Only use @deprecated if the docblock describes THIS function
            # (not a filter that happens to share the name)
            desc_match = re.search(r'^\s*\*\s+([^@*\n][^\n]*)', docblock, re.MULTILINE)
            if desc_match:
                description = desc_match.group(1).strip()

            # Extract @since (use the first one — that's when the function was introduced)
            since_match = re.search(r'@since\s+([\d.]+)', docblock)
            since = since_match.group(1) if since_match else None

            # Only mark as deprecated if the docblock is for the function itself
            # Filter docblocks say "Filters..." — function docblocks don't
            is_filter_doc = description and (
                description.startswith('Filters ') or
                description.startswith('Fires ')
            )

            if not is_filter_doc:
                dep_match = re.search(r'@deprecated\s+([\d.]+)', docblock)
                deprecated = dep_match.group(1) if dep_match else None

                see_match = re.search(r'@see\s+(\S+)', docblock)
                replacement = see_match.group(1) if see_match else None

            # Extract @return
            if not return_type:
                ret_match = re.search(r'@return\s+(\S+)', docblock)
                return_type = ret_match.group(1) if ret_match else None

            # Extract @param annotations
            for pm in re.finditer(r'@param\s+(\S+)\s+\$(\w+)', docblock):
                param_annotations[pm.group(2)] = pm.group(1)

        # Parse parameters
        params = []
        if params_raw:
            for p in params_raw.split(','):
                p = p.strip()
                if not p:
                    continue
                param_match = re.match(
                    r'(?:(\?\w+|\w+(?:\|[\w\\]+)*)\s+)?'
                    r'(\.\.\.)?\$(\w+)'
                    r'(?:\s*=\s*(.+))?',
                    p
                )
                if param_match:
                    ptype = param_match.group(1)
                    variadic = param_match.group(2)
                    pname = param_match.group(3)
                    default = param_match.group(4)
                    if not ptype and pname in param_annotations:
                        ptype = param_annotations[pname]
                    params.append({
                        'name': pname,
                        'type': ptype,
                        'default': default.strip() if default else None,
                        'variadic': variadic is not None
                    })

        functions.append({
            'name': func_name,
            'params': params,
            'return_type': return_type,
            'since': since,
            'deprecated': deprecated,
            'replacement': replacement,
            'description': description,
            'file': filepath
        })

    return functions


def extract_hooks(content, filepath):
    """Extract action and filter hooks."""
    hooks = []

    action_pattern = r'do_action(?:_ref_array)?\s*\(\s*[\'\"]([\w\-/{}.]+)[\'\"](.*?)\)'
    for match in re.finditer(action_pattern, content, re.DOTALL):
        hook_name = match.group(1)
        params_section = match.group(2)
        param_count = len([p for p in params_section.split(',')[1:] if p.strip()]) if params_section.strip() else 0
        hooks.append({'name': hook_name, 'type': 'action', 'param_count': param_count, 'file': filepath})

    filter_pattern = r'apply_filters(?:_ref_array)?\s*\(\s*[\'\"]([\w\-/{}.]+)[\'\"](.*?)\)'
    for match in re.finditer(filter_pattern, content, re.DOTALL):
        hook_name = match.group(1)
        params_section = match.group(2)
        param_count = len([p for p in params_section.split(',')[1:] if p.strip()]) if params_section.strip() else 0
        hooks.append({'name': hook_name, 'type': 'filter', 'param_count': param_count, 'file': filepath})

    return hooks


def build_triples(functions, hooks):
    triples = []

    for func in functions:
        name = func['name']
        source = func['file']
        since = func['since']
        deprecated = func['deprecated']
        replacement = func['replacement']

        triples.append({
            'subject': name, 'predicate': 'type', 'object': 'function',
            'source_file': source, 'since_version': since,
            'deprecated_version': deprecated, 'replacement': replacement,
            'status': 'deprecated' if deprecated else 'verified'
        })

        param_sig = ', '.join([
            f"{'...' if p['variadic'] else ''}"
            f"{'(' + p['type'] + ') ' if p['type'] else ''}"
            f"${p['name']}"
            f"{' = ' + p['default'] if p['default'] else ''}"
            for p in func['params']
        ])
        if param_sig:
            triples.append({
                'subject': name, 'predicate': 'parameters', 'object': param_sig,
                'source_file': source, 'since_version': since,
                'deprecated_version': deprecated, 'replacement': replacement,
                'status': 'verified'
            })

        triples.append({
            'subject': name, 'predicate': 'parameter_count',
            'object': str(len(func['params'])),
            'source_file': source, 'since_version': since,
            'deprecated_version': deprecated, 'replacement': replacement,
            'status': 'verified'
        })

        if func['return_type']:
            triples.append({
                'subject': name, 'predicate': 'return_type', 'object': func['return_type'],
                'source_file': source, 'since_version': since,
                'deprecated_version': deprecated, 'replacement': replacement,
                'status': 'verified'
            })

        if func['description']:
            triples.append({
                'subject': name, 'predicate': 'description', 'object': func['description'],
                'source_file': source, 'since_version': since,
                'deprecated_version': deprecated, 'replacement': replacement,
                'status': 'verified'
            })

        if deprecated:
            triples.append({
                'subject': name, 'predicate': 'deprecated_in', 'object': deprecated,
                'source_file': source, 'since_version': since,
                'deprecated_version': deprecated, 'replacement': replacement,
                'status': 'verified'
            })
            if replacement:
                triples.append({
                    'subject': name, 'predicate': 'replaced_by', 'object': replacement,
                    'source_file': source, 'since_version': since,
                    'deprecated_version': deprecated, 'replacement': replacement,
                    'status': 'verified'
                })

    seen_hooks = set()
    for hook in hooks:
        key = (hook['name'], hook['type'])
        if key in seen_hooks:
            continue
        seen_hooks.add(key)
        triples.append({
            'subject': hook['name'], 'predicate': 'type',
            'object': f"hook_{hook['type']}", 'source_file': hook['file'],
            'since_version': None, 'deprecated_version': None,
            'replacement': None, 'status': 'verified'
        })
        triples.append({
            'subject': hook['name'], 'predicate': 'hook_type',
            'object': hook['type'], 'source_file': hook['file'],
            'since_version': None, 'deprecated_version': None,
            'replacement': None, 'status': 'verified'
        })

    return triples


def store_triples(conn, triples):
    c = conn.cursor()
    for t in triples:
        c.execute("""
            INSERT INTO triples
            (subject, predicate, object, source_file, since_version,
             deprecated_version, replacement, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (t['subject'], t['predicate'], t['object'],
              t['source_file'], t['since_version'],
              t['deprecated_version'], t['replacement'], t['status']))
    conn.commit()


def parse_wordpress(wp_path):
    all_functions = []
    all_hooks = []
    files_parsed = 0

    scan_dirs = [
        os.path.join(wp_path, 'wp-includes'),
        os.path.join(wp_path, 'wp-admin'),
    ]

    for scan_dir in scan_dirs:
        if not os.path.exists(scan_dir):
            continue
        for root, dirs, files in os.walk(scan_dir):
            for fname in files:
                if not fname.endswith('.php'):
                    continue
                filepath = os.path.join(root, fname)
                rel_path = os.path.relpath(filepath, wp_path)
                try:
                    with open(filepath, 'r', errors='ignore') as f:
                        content = f.read()
                except Exception:
                    continue
                functions = extract_functions(content, rel_path)
                hooks = extract_hooks(content, rel_path)
                all_functions.extend(functions)
                all_hooks.extend(hooks)
                files_parsed += 1

    return all_functions, all_hooks, files_parsed


def generate_summary(functions, hooks, triples, db_path):
    deprecated_count = sum(1 for f in functions if f['deprecated'])
    unique_hooks = len(set(h['name'] for h in hooks))
    actions = len(set(h['name'] for h in hooks if h['type'] == 'action'))
    filters = len(set(h['name'] for h in hooks if h['type'] == 'filter'))

    return {
        'store': {'database': db_path, 'total_triples': len(triples), 'domain': 'wordpress'},
        'functions': {'total': len(functions), 'active': len(functions) - deprecated_count, 'deprecated': deprecated_count},
        'hooks': {'unique_total': unique_hooks, 'actions': actions, 'filters': filters},
        'coverage': {
            'has_since_version': sum(1 for f in functions if f['since']),
            'has_return_type': sum(1 for f in functions if f['return_type']),
            'has_description': sum(1 for f in functions if f['description']),
            'has_parameters': sum(1 for f in functions if f['params'])
        }
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 parser.py /path/to/wordpress-source")
        print("       Clone WordPress first: git clone --depth 1 https://github.com/WordPress/WordPress.git")
        sys.exit(1)
    wp_path = sys.argv[1]
    db_path = 'wordpress.db'

    print("=" * 60)
    print("VFault WordPress store builder")
    print("=" * 60)
    print()

    print(f"Parsing WordPress source at {wp_path}...")
    functions, hooks, files_parsed = parse_wordpress(wp_path)
    print(f"  Parsed {files_parsed} PHP files")
    print(f"  Found {len(functions)} functions")
    print(f"  Found {len(hooks)} hook invocations")
    print()

    print("Building structured triples...")
    triples = build_triples(functions, hooks)
    print(f"  Generated {len(triples)} triples")
    print()

    print(f"Writing to {db_path}...")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = create_database(db_path)
    store_triples(conn, triples)
    print("  Done")
    print()

    summary = generate_summary(functions, hooks, triples, db_path)
    with open('summary.json', 'w') as f:
        json.dump(summary, f, indent=2)

    print("=" * 60)
    print("STORE SUMMARY")
    print("=" * 60)
    print(f"  Total triples:      {summary['store']['total_triples']}")
    print(f"  Functions:          {summary['functions']['total']}")
    print(f"    Active:           {summary['functions']['active']}")
    print(f"    Deprecated:       {summary['functions']['deprecated']}")
    print(f"  Hooks:              {summary['hooks']['unique_total']}")
    print(f"    Actions:          {summary['hooks']['actions']}")
    print(f"    Filters:          {summary['hooks']['filters']}")
    print()

    # Verification tests
    c = conn.cursor()
    print("=" * 60)
    print("VERIFICATION TESTS")
    print("=" * 60)

    # Test 1: current_user_can should NOT be deprecated
    print()
    print("Test: current_user_can() — should NOT be deprecated")
    c.execute("SELECT predicate, object, deprecated_version FROM triples WHERE subject = 'current_user_can'")
    rows = c.fetchall()
    for row in rows:
        dep_status = f" [DEPRECATED {row[2]}]" if row[2] else " [ACTIVE]"
        print(f"  {row[0]}: {row[1]}{dep_status}")

    # Test 2: wp_register_scripts should not exist
    print()
    print("Test: wp_register_scripts — should NOT exist (hallucination)")
    c.execute("SELECT subject FROM triples WHERE subject = 'wp_register_scripts'")
    rows = c.fetchall()
    print(f"  {'FOUND (BUG)' if rows else 'NOT FOUND — HALLUCINATION CAUGHT'}")

    # Test 3: get_currentuserinfo should be deprecated
    print()
    print("Test: get_currentuserinfo() — SHOULD be deprecated")
    c.execute("SELECT predicate, object, deprecated_version FROM triples WHERE subject = 'get_currentuserinfo'")
    rows = c.fetchall()
    for row in rows:
        dep_status = f" [DEPRECATED {row[2]}]" if row[2] else " [ACTIVE]"
        print(f"  {row[0]}: {row[1]}{dep_status}")

    # Test 4: wp_enqueue_script params
    print()
    print("Test: wp_enqueue_script() parameters")
    c.execute("SELECT object FROM triples WHERE subject = 'wp_enqueue_script' AND predicate = 'parameters'")
    rows = c.fetchall()
    for row in rows:
        print(f"  {row[0]}")

    conn.close()
    print()
    print("Store ready.")


if __name__ == '__main__':
    main()
