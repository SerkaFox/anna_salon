#!/usr/bin/env python3
"""Scrape all Treatwell reviews for Brimoon Nails → treatwell_reviews.json"""

import json
import re
import time
import urllib.request
import urllib.error

BASE_URL = "https://www.treatwell.es/establecimiento/brimoon-nails/"
PAGE_URL = BASE_URL + "reviews/pagina-{page}/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_state(html):
    """Pull the window.__state__ JSON out of the page."""
    m = re.search(r'window\.__state__\s*=\s*(\{.+)', html, re.DOTALL)
    if not m:
        return {}
    raw = m.group(1)
    # Find balanced closing brace
    depth = 0
    end = 0
    for i, ch in enumerate(raw):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        return json.loads(raw[:end])
    except Exception:
        return {}


def find_reviews_in_state(state):
    """Recursively locate the first list that looks like Treatwell reviews."""
    if isinstance(state, dict):
        for k, v in state.items():
            if k == "reviews" and isinstance(v, list) and v and isinstance(v[0], dict) and "rating" in v[0]:
                return v
            result = find_reviews_in_state(v)
            if result is not None:
                return result
    elif isinstance(state, list):
        for item in state:
            result = find_reviews_in_state(item)
            if result is not None:
                return result
    return None


def parse_review(r):
    reviewer = r.get("reviewer", {})
    content_obj = r.get("content", {})
    treatments = r.get("treatmentNames", [])
    employee = r.get("employeeDescription", "")
    created = r.get("createdAt", r.get("visitedAt", ""))[:10]

    return {
        "id": r.get("id", 0),
        "name": reviewer.get("name", "Anónimo"),
        "date": created,
        "rating": r.get("rating", 5),
        "text": content_obj.get("content", ""),
        "services": treatments,
        "employee": employee,
        "verified": r.get("verified", False),
    }


def get_max_page(html):
    pages = [int(p) for p in re.findall(r'pagina-(\d+)', html)]
    return max(pages) if pages else 1


def main():
    print("Fetching page 1...")
    html1 = fetch(BASE_URL)

    # Page 1 pagination only shows page 2; fetch page 2 to discover max_page
    html2 = fetch(PAGE_URL.format(page=2))
    max_page = get_max_page(html2)
    print(f"Max page: {max_page}")

    all_reviews = {}

    def process_html(html, label):
        state = extract_state(html)
        reviews_raw = find_reviews_in_state(state)
        if not reviews_raw:
            # Fallback: parse JSON-LD
            for block in re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL,
            ):
                try:
                    data = json.loads(block.strip())
                    nodes = data.get("@graph", [data])
                    for node in nodes:
                        for r in node.get("review", []):
                            rid = hash((r.get("author", {}).get("name", ""), r.get("datePublished", ""), r.get("reviewBody", "")))
                            if rid not in all_reviews:
                                all_reviews[rid] = {
                                    "id": rid,
                                    "name": r.get("author", {}).get("name", ""),
                                    "date": r.get("datePublished", ""),
                                    "rating": r.get("reviewRating", {}).get("ratingValue", 5),
                                    "text": r.get("reviewBody", ""),
                                    "services": [],
                                    "employee": "",
                                    "verified": False,
                                }
                except Exception:
                    pass
            return

        new = 0
        for r in reviews_raw:
            rid = r.get("id") or hash((r.get("reviewer", {}).get("name", ""), r.get("createdAt", ""), r.get("content", {}).get("content", "")))
            if rid not in all_reviews:
                all_reviews[rid] = parse_review(r)
                new += 1
        print(f"  {label}: +{new} reviews (total {len(all_reviews)})")

    process_html(html1, "Page 1")
    process_html(html2, "Page 2")

    for page in range(3, max_page + 1):
        url = PAGE_URL.format(page=page)
        try:
            html = fetch(url)
            process_html(html, f"Page {page}/{max_page}")
        except urllib.error.HTTPError as e:
            print(f"  Page {page}: HTTP {e.code} — stopping")
            break
        except Exception as e:
            print(f"  Page {page}: ERROR {e}")
        time.sleep(0.8)

    result = sorted(all_reviews.values(), key=lambda r: r["date"], reverse=True)
    print(f"\nTotal unique reviews scraped: {len(result)}")

    with open("treatwell_reviews.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("Saved → treatwell_reviews.json")

    # Preview a few
    for r in result[:5]:
        services = ", ".join(r["services"]) if r["services"] else "—"
        print(f"\n  {r['name']}  {r['date']}  ⭐{r['rating']}")
        print(f"  Servicios: {services}")
        print(f"  {r['text'][:120]}")


if __name__ == "__main__":
    main()
