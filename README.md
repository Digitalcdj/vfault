# VFault - Verification at the Fault Lines

**Catches AI hallucinations in code. Deterministic. Zero false positives.**

AI coding tools hallucinate function names, deprecated APIs, and wrong parameters across every language. VFault checks against parsed source code. No AI in the verification layer.

Website: [vfault.com](https://vfault.com) | Live API: [exceedweb.pythonanywhere.com](https://exceedweb.pythonanywhere.com) | Docs: [vfault.com/docs.html](https://vfault.com/docs.html) | VS Code: [Marketplace](https://marketplace.visualstudio.com/items?itemName=exceed-web-services.vfault) | GitHub Action: [vfault-action](https://github.com/Digitalcdj/vfault-action)

```
$ vfault verify "Use Array.contains() to check values"
NOT FOUND - HALLUCINATION CAUGHT
  Array.contains > did you mean: Array.includes

$ vfault verify "Use useState() and useEffect()"
VERIFIED
  useState (since 16.8.0) - React
  useEffect (since 16.8.0) - React
```

## Quick start

### GitHub Action (check PRs automatically)

Add this to `.github/workflows/vfault.yml` in your repo:

```yaml
name: VFault Check
on: [pull_request]

jobs:
  vfault:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0

      - uses: Digitalcdj/vfault-action@v1
        with:
          api-key: ${{ secrets.VFAULT_API_KEY }}
```

Hallucinated functions show as inline errors on the PR. Configurable fail modes, whitelist, and rule controls. See [vfault-action](https://github.com/Digitalcdj/vfault-action) for full docs.

### VS Code extension

Open VS Code, search for **VFault** in the Extensions panel, and click Install. Or run:

```
ext install exceed-web-services.vfault
```

Works with **VS Code** and **Cursor**:
- Red underline: function doesn't exist (hallucinated). Hover for suggestions.
- Yellow underline: function is deprecated. Hover for the replacement.
- Green dotted: function verified. Hover for version, parameters, source.
- Quick fix: click the lightbulb, pick the correct function, one-click replace.

### CLI

```
git clone https://github.com/Digitalcdj/vfault.git
cd vfault
pip install -r requirements.txt

python3 vfault.py --demo
python3 vfault.py --check "your AI output here"
python3 vfault.py --serve
```

## 7 ecosystem shards - 201,066 verified triples

| Shard | Functions | Classes | Methods | Triples | Tier |
|-------|-----------|---------|---------|---------|------|
| **WordPress** 6.7.2 | 4,591 | - | 27 | 25,306 | Free |
| **WooCommerce** 11.x | 819 | 473 | 4,239 | 32,451 | Pro |
| **Python** 3.12 stdlib | 2,386 | 1,617 | 6,590 | 36,558 | Pro |
| **JavaScript** ES2024 + Web APIs + Node.js | 540 | 1,225 | 10,284 | 31,605 | Pro |
| **Laravel** 11.x | 79 | 2,414 | 15,547 | 41,570 | Pro |
| **React + Next.js** React 19 + Next.js 15 | 87 | 14 | 12 | 335 | Pro |
| **Django** 6.1 | 994 | 1,357 | 6,020 | 33,149 | Pro |

WordPress includes 2,346 hooks. WooCommerce includes 2,533 hooks. Django includes 57 template filters, 24 template tags, 15 signals, 19 decorators, and 28 management commands. More shards coming soon.

## What it catches

```
Hallucinated:   wp_register_scripts > wp_register_script
Hallucinated:   Array.contains > Array.includes
Hallucinated:   useFetch > useState, useEffect (not a React hook)
Deprecated:     get_currentuserinfo > wp_get_current_user() (WP 4.5)
Deprecated:     ReactDOM.render > ReactDOM.createRoot (React 18)
Deprecated:     getServerSideProps > Server Components (Next.js 13)
Deprecated:     componentWillMount > componentDidMount (React 16.3)
Param mismatch: wp_enqueue_script($in_footer) > $in_footer was renamed to $args in WP 6.3.0
Class mismatch: WC_Product::get_total > belongs to WC_Abstract_Order
Class mismatch: WC_Order::get_price > belongs to WC_Product
Unknown:        useShoppingCart - custom hook, not flagged as hallucination
Unknown:        usePaymentFlow - outside shard scope, may be private code
Whitelisted:    get_my_custom_data - skipped (matches whitelist prefix)
Context:        wp_enqueue_script - should be inside add_action('wp_enqueue_scripts') callback
Context:        wp_redirect - must be followed by exit; or die;
Context:        django.shortcuts.render - verified Django shortcut
Verified:       wp_enqueue_script (since 2.1.0) - WordPress
Verified:       useState (since 16.8.0) - React
Verified:       wc_get_product (since 2.2.0) - WooCommerce
Verified:       json.dumps - Python
Verified:       django.urls.path - Django
```

## How it's different

|                           | VFault                           | Every competitor    |
| ------------------------- | -------------------------------- | ------------------- |
| Method                    | Deterministic source code lookup | AI judges AI        |
| Full pipeline speed       | <5ms / 300 lines                 | 152ms+              |
| False positives           | 0%                               | 5-10%               |
| Parameter checking        | Automatic                        | Manual config       |
| Class/method pairing      | Automatic                        | With types only     |
| Usage context rules       | 30 rules, 7 ecosystems           | Custom rules        |
| Corrections               | Yes + closest match + source     | No                  |
| Can verifier hallucinate? | Impossible                       | Yes                 |
| Test suite                | 103 tests passing                | Varies              |

## API

Base URL: `https://exceedweb.pythonanywhere.com`

All endpoints accept an optional `X-API-Key` header. Without a key, you're on the free tier (100 requests/day, WordPress shard only).

```bash
# Verify text across all shards
curl -X POST https://exceedweb.pythonanywhere.com/verify \
  -H "Content-Type: application/json" \
  -d '{"text": "Use useState() and wp_register_scripts()"}'

# Verify with whitelist (skip your private functions)
curl -X POST https://exceedweb.pythonanywhere.com/verify \
  -H "Content-Type: application/json" \
  -d '{"text": "get_my_custom_data() and wp_enqueue_script()", "whitelist": ["get_my_custom_"]}'

# Look up a function
curl https://exceedweb.pythonanywhere.com/lookup/useState

# Search by prefix
curl https://exceedweb.pythonanywhere.com/search/use

# Check usage
curl https://exceedweb.pythonanywhere.com/usage

# Health check
curl https://exceedweb.pythonanywhere.com/health

# List all context rules
curl https://exceedweb.pythonanywhere.com/rules

# Verify with rules disabled
curl -X POST https://exceedweb.pythonanywhere.com/verify \
  -H "Content-Type: application/json" \
  -d '{"text": "eval(user_input)", "disable_rules": ["eval"]}'
```

## Pricing

| Tier | Developers | Daily requests | Price |
|------|-----------|---------------|-------|
| **Free** | 1 | 100 | £0 forever |
| **Pro** | 1 | 5,000 | £19/month |
| **Team** | 5 | 15,000 | £49/month |
| **Business** | 15 | 50,000 | £99/month |
| **Enterprise** | Unlimited | 100,000 | From £199/month |

WordPress shard is free forever. All paid shards included with Pro and above.

## Performance

Full 8-layer verification across all 7 ecosystems on real-world code.

| Lines of code | Claims extracted | Time |
|--------------|-----------------|------|
| 100 | 100 | 1.78ms |
| 300 | 150 | 4.43ms |
| 500 | 150 | 5.75ms |
| 1,000 | 150 | 9.72ms |

## Testing

103 automated tests covering all 8 verification layers. Run against real shard data, no mocks.

```
VFAULT_SHARDS_DIR=shards pytest tests/test_vfault.py -v
```

Tests cover: existence checking, deprecation, parameter mismatch, class/method pairing, context rules (all 7 ecosystems), unknown vs not_found, whitelist, disable_rules, shard gating, third-party detection, edge cases, and performance (full deep check under 50ms).

CI runs automatically on push and PR to main via GitHub Actions.

## Author

Ian Fraser - [Exceed Web Services](https://www.exceedwebservices.com) - Ceredigion, Wales

*VFault: verification at the fault lines.*
