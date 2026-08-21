"""Local search provider — one provider, one name, no implementation details.

Routes to the engines inside the search-kata microVM container:
- Search + regular web extraction  -> DonSeTch (Rust engine, stdio bridge)
- PDF extraction                  -> Hound (ODL-first PDF engine, HTTP MCP)

Agents only ever see provider="local"; the engine split is an internal
detail. Both engines run inside the same Kata sandbox, so PDF content
never leaves the microVM.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from wsp_sdk import ProviderSpec, source_result

_DIR = pathlib.Path(__file__).resolve().parent


def _load_impl(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, _DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_donsetch = _load_impl("donsetch_impl", "_donsetch_impl.py")
_hound = _load_impl("hound_impl", "_hound_impl.py")


# ---------------------------------------------------------------------------
# Provider readiness — one knob for the whole local stack
# ---------------------------------------------------------------------------

def _local_binary(config: Dict[str, Any]) -> str:
    """DonSeTch binary path: config.local.api_key / config.local.binary / DONSETCH_BIN."""
    section = config.get("local", {}) if isinstance(config, dict) else {}
    if not isinstance(section, dict):
        section = {}
    for key in ("binary", "api_key", "apiKey"):
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.environ.get("DONSETCH_BIN", "")


def _donsetch_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Project the `local` config section onto the DonSeTch engine section."""
    if not isinstance(config, dict):
        return {}
    out = dict(config)
    local_section = config.get("local", {})
    if isinstance(local_section, dict):
        out["donsetch"] = dict(local_section)
    return out


def inspect_local_readiness(
    *,
    binary: Optional[str] = None,
    key: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = config or {}
    resolved = binary or key or _local_binary(cfg)
    if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        return {
            "state": "missing",
            "version": None,
            "binary_configured": bool(resolved),
            "engines": {
                "donsetch": False,
                "hound_pdf": _hound_endpoint(cfg) is not None,
            },
        }
    try:
        report = _donsetch.inspect_donsetch_readiness(binary=resolved, config=_donsetch_config(cfg))
    except Exception:
        report = {"state": "error"}
    report["binary_configured"] = True
    report["engines"] = {
        "donsetch": report.get("state") == "ready",
        "hound_pdf": _hound_endpoint(cfg) is not None,
    }
    return report


def _hound_endpoint(config: Dict[str, Any]) -> Optional[str]:
    if not isinstance(config, dict):
        return None
    section = config.get("hound", {})
    if not isinstance(section, dict):
        return None
    endpoint = str(section.get("endpoint") or "").strip()
    return endpoint or None


# ---------------------------------------------------------------------------
# Search — DonSeTch engine
# ---------------------------------------------------------------------------

def execute_search(search_module, prov, args, key, config, routing_info):
    cfg = _donsetch_config(config)
    if key is None:
        key = _local_binary(cfg)
    return _donsetch.execute_search(search_module, "local", args, key, cfg, routing_info)


# ---------------------------------------------------------------------------
# Extract — PDF to Hound (ODL), everything else to DonSeTch
# ---------------------------------------------------------------------------

def _is_pdf_url(url: str) -> bool:
    try:
        parts = urlsplit(str(url))
        path = parts.path.lower()
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if path.endswith(".pdf"):
        return True
    # arXiv-style PDF endpoints without a .pdf suffix
    if host in ("arxiv.org", "export.arxiv.org") and path.startswith("/pdf/"):
        return True
    return False


def _split_urls(urls: List[str]) -> tuple[List[str], List[str]]:
    pdf: List[str] = []
    web: List[str] = []
    for url in urls or []:
        (pdf if _is_pdf_url(url) else web).append(str(url))
    return web, pdf


def _run_donsetch_extract(extract_module, urls, key, output_format, include_images,
                          include_raw_html, render_js, config, keyless_allowed) -> Dict[str, Any]:
    cfg = _donsetch_config(config)
    if key is None:
        key = _local_binary(cfg)
    return _donsetch.execute_extract(
        extract_module, "local", urls, key, output_format,
        include_images, include_raw_html, render_js, cfg, keyless_allowed,
    )


def _run_hound_pdf_extract(extract_module, urls, key, output_format, include_images,
                           include_raw_html, render_js, config, keyless_allowed) -> Dict[str, Any]:
    return _hound.execute_extract(
        extract_module, "local", urls, key, output_format,
        include_images, include_raw_html, render_js, config, keyless_allowed,
    )


def execute_extract(
    extract_module,
    prov,
    urls,
    key,
    output_format,
    include_images,
    include_raw_html,
    render_js,
    config,
    keyless_allowed,
):
    web_urls, pdf_urls = _split_urls(urls)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    if web_urls:
        try:
            resp = _run_donsetch_extract(
                extract_module, web_urls, key, output_format,
                include_images, include_raw_html, render_js, config, keyless_allowed,
            )
            results.extend(resp.get("results", []))
            errors.extend(resp.get("errors", []) or [])
        except Exception as exc:  # engine failure must not kill the whole call
            errors.append({"provider": "local", "urls": web_urls, "error": str(exc)})

        # JS-shell fallback: when a non-rendered fetch came back failed or
        # empty, retry those URLs with browser rendering (tier=2) once.
        # Agents never need to know about render_js — the engine escalates
        # automatically. Controlled by config local.js_retry (default on).
        if not render_js:
            section = config.get("local", {}) if isinstance(config, dict) else {}
            js_retry = section.get("js_retry", True)
            if isinstance(js_retry, str):
                js_retry = js_retry.strip().lower() not in ("0", "false", "no", "off")
            if js_retry:
                failed = [
                    r for r in results
                    if r.get("error") or not str(r.get("content") or "").strip()
                ]
                retry_urls = [r.get("url") for r in failed if r.get("url")]
                if retry_urls:
                    try:
                        resp2 = _run_donsetch_extract(
                            extract_module, retry_urls, key, output_format,
                            include_images, include_raw_html, True, config, keyless_allowed,
                        )
                        ok2 = {
                            r.get("url"): r
                            for r in resp2.get("results", [])
                            if r.get("url")
                            and not r.get("error")
                            and str(r.get("content") or "").strip()
                        }
                        if ok2:
                            for i, r in enumerate(results):
                                if r.get("url") in ok2:
                                    results[i] = ok2[r["url"]]
                        errors.extend(resp2.get("errors", []) or [])
                    except Exception as exc:
                        errors.append({"provider": "local", "urls": retry_urls, "error": f"js_retry: {exc}"})

    if pdf_urls:
        try:
            resp = _run_hound_pdf_extract(
                extract_module, pdf_urls, key, output_format,
                include_images, include_raw_html, render_js, config, keyless_allowed,
            )
            results.extend(resp.get("results", []))
            errors.extend(resp.get("errors", []) or [])
        except Exception as exc:
            errors.append({"provider": "local", "urls": pdf_urls, "error": str(exc)})

    payload: Dict[str, Any] = {
        "provider": "local",
        "results": results,
        "requested_provider": "local",
    }
    if errors:
        payload["errors"] = errors
    if not results and errors:
        payload["error"] = errors[0].get("error", "local_extract_failed")
    return payload


# ---------------------------------------------------------------------------
# Provider declaration
# ---------------------------------------------------------------------------

PROVIDER = ProviderSpec(
    id="local",
    kind="both",
    env_var="DONSETCH_BIN",
    display_name="Local (search-kata)",
    description=(
        "Local search/extract inside the search-kata Kata microVM: DonSeTch "
        "for search and web extraction, Hound ODL engine for PDF. No API keys."
    ),
    config_section="local",
    capability_labels=("search", "extract", "local", "pdf", "ocr"),
    upstream_capabilities=("web_search", "web_fetch", "web_crawl", "pdf"),
    auto_allowed_by_default=False,
    recommended=False,
    keyless=True,
    supports_freshness=False,
    free_tier="Free local; no API key",
    signup_url="https://github.com/dondai44423/donsetch",
    execute_search=execute_search,
    execute_extract=execute_extract,
)
