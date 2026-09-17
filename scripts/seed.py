"""Seed PMS through its HTTP API, including real DummyJSON image files.

Run from your project root:
  uv add --dev httpx pillow python-dotenv
  uv run python -m scripts.seed_via_api --limit 10
  uv run python -m scripts.seed_via_api --limit 10 --images-per-product 1 --apply

Configuration:
  --api-base-url defaults to PMS_API_BASE_URL or http://127.0.0.1:8000/api/v1
  --categories-path defaults to categories; --products-path to products
  PMS_ACCESS_TOKEN is read from your environment/.env or prompted securely.
  Use an access token authorized to list categories and list/create products.
  Categories are read-only by default; required category names must exist.
  Use --create-categories ONLY if your API implements POST categories.

Expected API contract (configure paths to match your Swagger):
  GET categories -> list or data/items envelope, containing name and id.
  Optional POST categories -> JSON {"name": "..."} (or --category-body-format form).
      This request is made only with --create-categories.
  GET products?search=SKU&page=1&page_size=50 -> product list with sku and id.
  POST products -> multipart fields name, sku, category_name, price, stock,
      status, description; repeated images fields contain the actual files.
  POST products may return only a success message (data may be null).
      The seeder then reads GET products by exact SKU to confirm the saved ID.
      Full product objects, directly or in data/product, are also supported.

All UUIDs, S3 object keys, database rows, timestamps, and public image URLs
are owned by YOUR backend. This script does not connect to DB or S3.
Products use only the six selected PMS category names. Default mode verifies
the names needed by the selected products without creating categories.
Missing unused category names do not block seeding. Missing required names
stop the run before any product POST. Your ProductService requires existing
categories; passing category_name does not create them automatically.
With --create-categories, all six category names are ensured through the API.
All four statuses are used; active stock >= 1 and out_of_stock stock = 0.
Timestamps use normal API behavior; this does not backdate products.
Prices remain DummyJSON sample values; no currency conversion is applied.

Default SKU prefix API-DUMMY creates a separate sample set from the earlier
SEED-DUMMY database seeder. Existing exact SKUs in this set are skipped.
Use --sku-prefix SEED-DUMMY to recognize and skip the old seed records instead.
Existing products/images are not patched or repaired.
Reruns rely on server-side SKU/category uniqueness; run one seeder at a time.
No automatic POST retries: a failed response can follow a committed write.
Previously successful API requests remain committed after any later failure.

Source metadata/images use a separate HTTP client with no PMS token.
Downloads are size-limited, format-checked, and deduplicated before POST.
Preview performs only source GET requests, with no calls to the PMS API.

Docs:
https://www.python-httpx.org/quickstart/#sending-multipart-file-uploads
https://fastapi.tiangolo.com/tutorial/request-forms-and-files/
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import re
import sys
import warnings
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000/api/v1"

DUMMYJSON_URL = "https://dummyjson.com/products"


SELECT_FIELDS = "id,title,description,category,price,stock,images"


CATEGORY_NAMES = [
    "Lighting",
    "Apparel",
    "Home",
    "Electronics",
    "Outdoor",
    "Stationery",
]


PRODUCT_STATUSES = ("active", "draft", "out_of_stock", "archived")


CATEGORY_MAPPING = {
    "laptops": "Electronics",
    "smartphones": "Electronics",
    "mobile-accessories": "Electronics",
    "tablets": "Electronics",
    "mens-shirts": "Apparel",
    "mens-shoes": "Apparel",
    "mens-watches": "Apparel",
    "tops": "Apparel",
    "womens-bags": "Apparel",
    "womens-dresses": "Apparel",
    "womens-jewellery": "Apparel",
    "womens-shoes": "Apparel",
    "womens-watches": "Apparel",
    "sunglasses": "Apparel",
    "furniture": "Home",
    "home-decoration": "Home",
    "kitchen-accessories": "Home",
    "sports-accessories": "Outdoor",
    **{name.casefold(): name for name in CATEGORY_NAMES},
}


LIGHTING_PATTERN = re.compile(
    r"\b(lamps?|lights?|lighting|lanterns?|chandeliers?)\b", re.IGNORECASE
)


STATIONERY_PATTERN = re.compile(
    r"\b(pens?|pencils?|notebooks?|notepads?|erasers?|staplers?|stationery)\b",
    re.IGNORECASE,
)


IMAGE_HOSTS = frozenset({"cdn.dummyjson.com", "dummyjson.com"})


IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


MAX_PIXELS = 25_000_000


@dataclass(frozen=True)
class SourceProduct:
    source_id: int
    name: str
    description: str
    category: str
    price: Decimal
    stock: int
    images: tuple[str, ...]


@dataclass(frozen=True)
class DownloadedImage:
    data: bytes
    content_type: str
    extension: str
    content_hash: str


def assign_status_and_stock(
    index: int,
    source_stock: int,
    mixed_statuses: bool = True,
) -> tuple[str, int]:
    """Use a repeatable four-status cycle for the selected sample products."""
    if not mixed_statuses:
        return ("out_of_stock" if source_stock == 0 else "active"), source_stock
    status = PRODUCT_STATUSES[index % len(PRODUCT_STATUSES)]
    stock = source_stock
    if status == "out_of_stock":
        stock = 0
    elif status == "active":
        stock = max(1, stock)
    return status, stock


def validate_image_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in IMAGE_HOSTS
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Image URL is outside the allowed DummyJSON HTTPS hosts")


def map_category(source_category: str, title: str) -> str | None:
    """Map relevant source groups into the six PMS categories."""
    source_category = source_category.strip().casefold()
    target = CATEGORY_MAPPING.get(source_category)
    # Refine household goods only: an electronic notebook stays Electronics.
    if target == "Home":
        if LIGHTING_PATTERN.search(title):
            return "Lighting"
        if STATIONERY_PATTERN.search(title):
            return "Stationery"
    return target


def map_source_product(item: dict[str, Any]) -> SourceProduct | None:
    """Explicit allowlist: reviews, ratings, dimensions, etc. are discarded."""
    source_id, stock = item["id"], item["stock"]
    if type(source_id) is not int or source_id <= 0:
        raise ValueError("Invalid DummyJSON product ID")
    if type(stock) is not int or stock < 0:
        raise ValueError("Invalid stock")
    for field in ("title", "category", "description"):
        if not isinstance(item[field], str):
            raise ValueError(f"Invalid {field}")
    if not item["title"].strip() or not item["category"].strip():
        raise ValueError("Product title and category must not be empty")
    category = map_category(item["category"], item["title"])
    if category is None:
        return None
    try:
        price = Decimal(str(item["price"]))
        if not price.is_finite() or not Decimal(0) <= price < Decimal("10000000000"):
            raise ValueError("Price exceeds PMS Numeric(12,2) bounds")
        price = price.quantize(Decimal("0.01"))
        if price >= Decimal("10000000000"):
            raise ValueError("Rounded price exceeds PMS Numeric(12,2) bounds")
    except InvalidOperation as exc:
        raise ValueError("Invalid price") from exc
    images = item.get("images", [])
    if not isinstance(images, list) or not all(isinstance(x, str) for x in images):
        raise ValueError("Invalid images list")
    for url in images:
        validate_image_url(url)
    return SourceProduct(
        source_id=source_id,
        name=item["title"].strip(),
        description=item["description"],
        category=category,
        price=price,
        stock=stock,
        images=tuple(dict.fromkeys(images)),
    )


async def fetch_products(client: Any, limit: int) -> list[SourceProduct]:
    if limit < 0:
        raise ValueError("--limit must be >= 0")
    response = await client.get(
        DUMMYJSON_URL, params={"limit": 0, "select": SELECT_FIELDS}
    )
    response.raise_for_status()
    payload = json.loads(response.content, parse_float=Decimal)
    items, total = payload.get("products"), payload.get("total")
    if not isinstance(items, list) or type(total) is not int or total < 0:
        raise ValueError("Unexpected DummyJSON response")
    if len(items) != total:
        raise ValueError("Incomplete DummyJSON response; refusing a partial catalog")
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("Duplicate source product IDs")
    products = []
    excluded_categories = set()
    for item in items:
        product = map_source_product(item)
        if product is None:
            excluded_categories.add(item["category"])
        else:
            products.append(product)
    if excluded_categories:
        print(
            f"Excluded {len(items) - len(products)} products from unmapped groups: "
            + ", ".join(sorted(excluded_categories))
        )
    return products if limit == 0 else products[:limit]


def inspect_image(data: bytes) -> DownloadedImage:
    """Inspect actual file bytes; never trust a URL suffix or HTTP MIME alone."""
    from PIL import Image

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as picture:
            image_format = picture.format
            if image_format not in IMAGE_FORMATS:
                raise ValueError("Only JPEG, PNG, and WebP are allowed")
            if picture.width * picture.height > MAX_PIXELS:
                raise ValueError("Image exceeds seed pixel limit")
            picture.verify()
        with Image.open(BytesIO(data)) as picture:
            if getattr(picture, "n_frames", 1) != 1:
                raise ValueError("Animated images are not accepted by this seeder")
            picture.load()
    content_type, extension = IMAGE_FORMATS[image_format]
    return DownloadedImage(
        data, content_type, extension, hashlib.sha256(data).hexdigest()
    )


async def download_image(client: Any, url: str, max_bytes: int) -> DownloadedImage:
    validate_image_url(url)
    data = bytearray()
    # Redirects remain disabled, so an external host cannot bypass the allowlist.
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            if len(data) + len(chunk) > max_bytes:
                raise ValueError("Image exceeds PMS maximum file size")
            data.extend(chunk)
    if not data:
        raise ValueError("Image download is empty")
    return await asyncio.to_thread(inspect_image, bytes(data))


class SeedError(RuntimeError):
    """Actionable failure without dumping credentials or response bodies."""


def generate_sku(source_id: int, prefix: str = "API-DUMMY") -> str:
    return f"{prefix}-{source_id:05d}"


def api_failure(response: httpx.Response, context: str) -> SeedError:
    status = response.status_code
    hints = {
        401: "The access token is missing, expired, or invalid.",
        403: "The token's user does not have the required permission.",
        404: "Check --api-base-url and the endpoint path against Swagger.",
        405: "This route does not support the requested method; check Swagger.",
        409: "Conflict was not verified as an existing category/SKU.",
        413: "The API/proxy upload size limit was exceeded.",
        422: "The request does not match your endpoint schema.",
        429: "The API rate limit was reached; retry later.",
    }
    details = hints.get(status, "Check your backend logs for this request.")
    if status in (301, 302, 307, 308):
        details = "Set the exact route path, including its trailing slash if required."
    if status == 422:
        try:
            errors = response.json().get("detail")
            if isinstance(errors, list):
                fields = [
                    ".".join(str(part) for part in error.get("loc", []))
                    for error in errors
                    if isinstance(error, dict)
                ]
                details += " Fields: " + ", ".join(fields[:10])
        except (ValueError, AttributeError):
            pass
    return SeedError(f"{context}: HTTP {status}. {details}")


async def api_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    context: str,
    **kwargs: Any,
) -> httpx.Response:
    try:
        return await client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        if method == "POST":
            raise SeedError(
                f"{context}: {type(exc).__name__}. The write outcome is unknown. "
            ) from exc
        raise SeedError(
            f"{context}: {type(exc).__name__}. Check the API address and connection."
        ) from exc


def response_items(payload: Any) -> list[dict[str, Any]]:
    current = payload
    for _ in range(5):
        if isinstance(current, list) and all(
            isinstance(item, dict) for item in current
        ):
            return current
        if not isinstance(current, dict):
            break
        key = next(
            (
                key
                for key in ("items", "products", "categories", "results", "data")
                if key in current
            ),
            None,
        )
        if key is None:
            break
        current = current[key]
    raise SeedError(
        "Unexpected list response. Expected a list or a data/items envelope; "
        "adapt response_items() to your GET endpoint response."
    )


async def list_items(
    client: httpx.AsyncClient,
    path: str,
    *,
    search: str | None = None,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    for page in range(1, 1001):
        params: dict[str, Any] = {"page": page, "page_size": 50}
        if search is not None:
            params["search"] = search
        response = await api_request(
            client, "GET", path, context="List endpoint", params=params
        )
        if not response.is_success:
            raise api_failure(response, "List endpoint")
        try:
            items = response_items(response.json())
        except ValueError as exc:
            raise SeedError("GET endpoint returned invalid JSON.") from exc
        if not items:
            return collected
        fingerprint = json.dumps(items, sort_keys=True)
        if fingerprint in seen_pages:
            raise SeedError("GET pagination repeated a page; check its parameter names")
        seen_pages.add(fingerprint)
        collected.extend(items)
        if len(items) < 50:
            return collected
    raise SeedError("GET pagination exceeded 1,000 pages; check endpoint parameters.")


async def find_product(
    client: httpx.AsyncClient,
    products_path: str,
    sku: str,
) -> dict[str, Any] | None:
    # Never treat the first fuzzy search result as an exact SKU match.
    items = await list_items(client, products_path, search=sku)
    matches = [item for item in items if item.get("sku") == sku]
    if len(matches) > 1:
        raise SeedError(f"API returned duplicate exact SKUs: {sku}.")
    return matches[0] if matches else None


def created_object(
    response: httpx.Response,
    context: str,
    *,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    """Read a returned object; product POSTs may legitimately omit one."""
    try:
        record = response.json()
    except ValueError:
        record = None
    for _ in range(5):
        if not isinstance(record, dict):
            break
        # Prefer object envelopes to an outer request/operation ID. Do not
        # search images, category relationships, or arbitrary nested objects.
        key = next(
            (
                key
                for key in ("data", "product", "category", "item", "result")
                if isinstance(record.get(key), dict)
            ),
            None,
        )
        if record.get("id") and ("sku" in record or "name" in record):
            return record
        if key is None:
            if record.get("id"):
                return record
            break
        record = record[key]
    if allow_missing:
        return None
    raise SeedError(
        f"{context} returned success without an object id. "
        "Check the created record and adapt created_object() to your response."
    )


async def seed_categories(
    client: httpx.AsyncClient,
    path: str,
    body_format: str,
    existing: list[dict[str, Any]],
    *,
    required_names: set[str] | None = None,
    create_missing: bool = False,
) -> None:
    """Validate existing categories; create only when explicitly configured."""
    known_names = {item.get("name") for item in existing}
    required_names = set(CATEGORY_NAMES) if required_names is None else required_names
    if not required_names.issubset(CATEGORY_NAMES):
        raise SeedError("Products must use one of the six allowed PMS category names.")
    target_names = [
        name for name in CATEGORY_NAMES if create_missing or name in required_names
    ]
    missing_names = [name for name in target_names if name not in known_names]
    if missing_names and not create_missing:
        raise SeedError(
            "Required categories are missing from your API: "
            + ", ".join(missing_names)
            + ". ProductService requires these exact names to exist first. "
            "No category or product POST was sent. Use your supported category "
            "creation workflow; --create-categories is only for an API with POST route."
        )
    if not create_missing:
        print("Category mode: read existing categories; no category POST requests.")
    for name in target_names:
        if name in known_names:
            print(f"Category exists: {name}")
            continue
        request_body = {"json" if body_format == "json" else "data": {"name": name}}
        response = await api_request(
            client, "POST", path, context=f"Create category {name}", **request_body
        )
        if response.status_code == 409:
            refreshed = await list_items(client, path)
            if any(item.get("name") == name for item in refreshed):
                known_names.add(name)
                print(f"Category exists: {name}")
                continue
        if not response.is_success:
            raise api_failure(response, f"Create category {name}")
        record = created_object(response, f"Create category {name}")
        if record.get("name") != name:
            raise SeedError(f"Category response did not match {name}; check your API.")
        known_names.add(name)
        print(f"Category created: {name}")


async def product_files(
    source_client: httpx.AsyncClient,
    product: SourceProduct,
    count: int,
    max_file_bytes: int,
) -> list[tuple[str, tuple[str, bytes, str]]]:
    if not product.images:
        raise SeedError(f"DummyJSON product {product.source_id} has no image URLs.")
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    seen_hashes: set[str] = set()
    for url in product.images:
        image = await download_image(source_client, url, max_file_bytes)
        if image.content_hash in seen_hashes:
            continue
        seen_hashes.add(image.content_hash)
        files.append(
            (
                "images",
                (
                    f"dummy-{product.source_id}-{len(files) + 1}.{image.extension}",
                    image.data,
                    image.content_type,
                ),
            )
        )
        if len(files) == count:
            break
    return files


async def seed_products(
    api_client: httpx.AsyncClient,
    source_client: httpx.AsyncClient,
    products: list[SourceProduct],
    args: argparse.Namespace,
    existing_by_sku: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    created = skipped = 0
    for index, product in enumerate(products):
        sku = generate_sku(product.source_id, args.sku_prefix)
        if sku in existing_by_sku:
            skipped += 1
            print(f"Product exists; skipped: {sku}")
            continue
        status, stock = assign_status_and_stock(index, product.stock)
        # Download/validate ALL selected files before creating this product.
        files = await product_files(
            source_client,
            product,
            args.images_per_product,
            args.max_image_mb * 1024 * 1024,
        )
        if not files:
            raise SeedError(f"No usable image files for {sku}; product was not posted.")
        fields = {
            "name": product.name,
            "sku": sku,
            "category_name": product.category,
            "price": str(product.price),
            "stock": str(stock),
            "status": status,
            "description": product.description,
        }
        response = await api_request(
            api_client,
            "POST",
            args.products_path,
            context=f"Create product {sku}",
            data=fields,
            files=files,
        )
        if response.status_code == 409:
            existing = await find_product(api_client, args.products_path, sku)
            if existing is not None:
                skipped += 1
                existing_by_sku[sku] = existing
                print(f"Product exists; skipped after conflict: {sku}")
                continue
        if not response.is_success:
            raise api_failure(response, f"Create product {sku}")
        record = created_object(
            response,
            f"Create product {sku}",
            allow_missing=True,
        )
        if record is not None and record.get("sku") not in (None, sku):
            raise SeedError(f"Create product {sku} returned a different SKU;")
        if record is None or record.get("sku") != sku:
            # PMS create_product returns ApiResponse(message=...) without data.
            # The POST can already be committed: verify with GET, never repost.
            print(f"Product accepted; verifying saved record by SKU: {sku}")
            record = await find_product(api_client, args.products_path, sku)
            if record is None or not record.get("id"):
                raise SeedError(
                    f"Create product {sku}:HTTP {response.status_code},but GET products"
                    "did not return an exact SKU match with an id.The write may already"
                    "be committed; no POST was retried. Check this SKU in your API "
                    "and ensure the list endpoint returns newly created products."
                )
        existing_by_sku[sku] = record
        created += 1
        print(
            f"Product created: {sku}; id={record['id']}; status={status}; "
            f"images sent={len(files)}"
        )
    return created, skipped


def normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        not parsed.hostname
        or parsed.scheme not in ("http", "https")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SeedError("Set --api-base-url to your API URL without credentials.")
    if parsed.scheme == "http" and parsed.hostname not in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        raise SeedError("Use HTTPS for a remote API carrying an access token.")
    return value.strip().rstrip("/") + "/"


def normalize_route(value: str) -> str:
    if (
        not value
        or "://" in value
        or "?" in value
        or "#" in value
        or any(part in (".", "..") for part in value.split("/"))
    ):
        raise SeedError(
            "Endpoint paths must be relative paths such as products or categories."
        )
    return value.lstrip("/")


async def seed(args: argparse.Namespace, token: str | None = None) -> int:
    # This client never receives an API Authorization header.
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as source_client:
        products = await fetch_products(source_client, args.limit)
        print(f"Selected DummyJSON products: {len(products)}")
        print("Allowed categories: " + ", ".join(CATEGORY_NAMES))
        for index, product in enumerate(products):
            status, stock = assign_status_and_stock(index, product.stock)
            print(
                f"{generate_sku(product.source_id, args.sku_prefix)}|{product.name}|"
                f"{product.category} | {status} | stock={stock} | "
                f"images requested={min(len(product.images), args.images_per_product)}"
            )
        if not args.apply:
            print("PREVIEW ONLY. No PMS API requests were made.")
            return 0
        if not token:
            raise SeedError(
                "Set PMS_ACCESS_TOKEN locally to an authorized access token."
            )
        print(f"API target: {args.api_base_url}")
        async with httpx.AsyncClient(
            base_url=args.api_base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(120.0, connect=10.0),
            follow_redirects=False,
        ) as api_client:
            # Validate category requirements before product checks/downloads.
            existing_categories = await list_items(api_client, args.categories_path)
            await seed_categories(
                api_client,
                args.categories_path,
                args.category_body_format,
                existing_categories,
                required_names={product.category for product in products},
                create_missing=args.create_categories,
            )
            existing_products: dict[str, dict[str, Any]] = {}
            for product in products:
                sku = generate_sku(product.source_id, args.sku_prefix)
                existing = await find_product(api_client, args.products_path, sku)
                if existing is not None:
                    existing_products[sku] = existing
            created, skipped = await seed_products(
                api_client, source_client, products, args, existing_products
            )
            print(
                f"Seed completed: products created={created};"
                "existing skipped={skipped}."
            )
            return 0


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(override=False)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--api-base-url", default=os.getenv("PMS_API_BASE_URL", DEFAULT_API_BASE_URL)
    )
    parser.add_argument("--categories-path", default="categories")
    parser.add_argument("--products-path", default="products")
    parser.add_argument(
        "--category-body-format", choices=("json", "form"), default="json"
    )
    parser.add_argument(
        "--create-categories",
        action="store_true",
        help="Create missing categories only if your API supports POST categories;",
    )
    parser.add_argument(
        "--sku-prefix",
        default="API-DUMMY",
        help="Stable SKU namespace; default creates a fresh API-seeded set",
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="0 = all matching source products"
    )
    parser.add_argument("--images-per-product", type=int, default=1)
    parser.add_argument("--max-image-mb", type=int, default=5)
    parser.add_argument(
        "--apply", action="store_true", help="Create data through your PMS API"
    )
    args = parser.parse_args()
    if args.limit < 0 or not 1 <= args.images_per_product <= 6 or args.max_image_mb < 1:
        parser.error("--limit >= 0; --images-per-product 1..6; --max-image-mb >= 1")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,32}", args.sku_prefix):
        parser.error("--sku-prefix must contain 1..32 letters, numbers, or hyphens")
    try:
        args.api_base_url = normalize_base_url(args.api_base_url)
        args.categories_path = normalize_route(args.categories_path)
        args.products_path = normalize_route(args.products_path)
        token = os.getenv("PMS_ACCESS_TOKEN", "").strip()
        if args.apply and not token:
            if not sys.stdin.isatty():
                raise SeedError(
                    "Set PMS_ACCESS_TOKEN locally, or run in an interactive terminal."
                )
            token = getpass.getpass("PMS access token (hidden): ").strip()
        if token and any(character.isspace() for character in token):
            raise SeedError(
                "PMS_ACCESS_TOKEN must contain only the token, without 'Bearer '."
            )
        if args.apply and not token:
            raise SeedError("An access token is required for --apply.")
        return asyncio.run(seed(args, token))
    except SeedError as exc:
        print(f"Seed stopped: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Interrupted. Previously completed API requests remain committed.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        print(
            f"Seed stopped: {type(exc).__name__}."
            "Previously completed API requests remain committed. "
            "Check the last product/category and your backend logs.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
