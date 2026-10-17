"""Trigger two existing application defects; never edit code or submit an order."""
import argparse
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['all', 'page', 'checkout'], default='all')
    parser.add_argument('--expect-fixed', action='store_true', help='Check safe responses instead of HTTP 500')
    args = parser.parse_args()
    cases = [
        ('page', '/market?page=training-page', {200, 400}),
        ('checkout', '/cart/order', {302, 303, 401, 403}),
    ]
    passed = True
    for name, path, fixed_statuses in cases:
        if args.case not in {'all', name}:
            continue
        url = 'http://127.0.0.1:8085' + path
        # A fresh, anonymous request, with no cookies and no followed redirects.
        opener = build_opener(NoRedirect())
        try:
            with opener.open(url, timeout=15) as response:
                status = response.status
        except HTTPError as response:
            status = response.code
            response.close()
        except (URLError, TimeoutError) as exc:
            print(f'FAIL {name}: application unavailable ({type(exc).__name__})')
            passed = False
            continue
        expected = fixed_statuses if args.expect_fixed else {500}
        ok = status in expected
        passed = passed and ok
        print(f'{"OK" if ok else "FAIL"} {name}: HTTP {status} — {url}')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
