"""QA Runner — Playwright-based test execution with optional axe-core + Lighthouse."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ARTIFACTS_PATH = os.environ.get("ARTIFACTS_PATH", "/data/artifacts")
AGENT_API_URL = os.environ.get("AGENT_API_URL", "http://agent-api:8000")
AGENT_API_SECRET = os.environ.get("AGENT_API_SECRET", "")

AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"


async def run_qa_checks(
    job_id: str,
    revision_id: str,
    preview_url: str,
    viewports: list[dict],
    callback_url: str | None = None,
    run_lighthouse: bool = True,
    run_axe: bool = True,
) -> dict:
    """Run full QA checks on a preview URL using Playwright."""
    from playwright.async_api import async_playwright

    logger.info(f"Starting QA for revision {revision_id} at {preview_url}")

    report = {
        "job_id": job_id,
        "revision_id": revision_id,
        "overall_status": "pending",
        "desktop_score": 0,
        "mobile_score": 0,
        "broken_links": [],
        "console_errors": [],
        "accessibility_issues": [],
        "screenshots": {},
        "visual_diff": {},
        "lighthouse": None,
        "details": {
            "tested_at": datetime.utcnow().isoformat(),
            "preview_url": preview_url,
            "viewports_tested": [],
        },
    }

    artifacts_dir = Path(ARTIFACTS_PATH) / "qa" / revision_id
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            for viewport in viewports:
                vp_name = viewport["name"]
                vp_width = viewport["width"]
                vp_height = viewport["height"]

                logger.info(f"Testing viewport: {vp_name} ({vp_width}x{vp_height})")

                context = await browser.new_context(
                    viewport={"width": vp_width, "height": vp_height},
                    user_agent="Mozilla/5.0 (AI-Site-System QA Runner)",
                )
                page = await context.new_page()

                console_errors = []
                page.on("console", lambda msg: (
                    console_errors.append({
                        "type": msg.type,
                        "text": msg.text,
                        "url": preview_url,
                        "viewport": vp_name,
                    }) if msg.type == "error" else None
                ))

                try:
                    response = await page.goto(preview_url, wait_until="networkidle", timeout=30000)
                    status_code = response.status if response else 0
                except Exception as e:
                    logger.error(f"Failed to load {preview_url}: {e}")
                    report["overall_status"] = "fail"
                    report["details"]["load_error"] = str(e)
                    await context.close()
                    continue

                screenshot_path = artifacts_dir / f"screenshot_{vp_name}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)
                report["screenshots"][vp_name] = f"/qa/{revision_id}/screenshot_{vp_name}.png"

                # Broken link check (internal only)
                links = await page.eval_on_selector_all(
                    "a[href]",
                    "elements => elements.map(e => ({href: e.href, text: e.textContent.trim()}))"
                )
                for link in links:
                    href = link.get("href", "")
                    if href.startswith("http") and not href.startswith(preview_url):
                        continue
                    if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:") or href.startswith("tel:"):
                        continue
                    try:
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            resp = await client.head(href, follow_redirects=True)
                            if resp.status_code >= 400:
                                report["broken_links"].append({
                                    "url": href,
                                    "text": link.get("text", ""),
                                    "status": resp.status_code,
                                    "viewport": vp_name,
                                })
                    except Exception:
                        report["broken_links"].append({
                            "url": href,
                            "text": link.get("text", ""),
                            "status": "timeout",
                            "viewport": vp_name,
                        })

                # Accessibility checks — axe-core if enabled, otherwise heuristics
                if run_axe:
                    axe_issues = await _run_axe_core(page, vp_name)
                    report["accessibility_issues"].extend(axe_issues)
                else:
                    a11y_issues = await _check_accessibility(page, vp_name)
                    report["accessibility_issues"].extend(a11y_issues)

                report["console_errors"].extend(console_errors)

                score = 100
                score -= len(console_errors) * 10
                score -= len([l for l in report["broken_links"] if l.get("viewport") == vp_name]) * 15
                # Weight axe violations by impact
                for issue in report["accessibility_issues"]:
                    if issue.get("viewport") != vp_name:
                        continue
                    impact = issue.get("impact") or issue.get("severity") or "minor"
                    score -= {"critical": 10, "serious": 6, "moderate": 3, "minor": 1, "warning": 3, "info": 1}.get(impact, 2)
                score = max(0, score)

                if vp_name == "desktop":
                    report["desktop_score"] = score
                elif vp_name == "mobile":
                    report["mobile_score"] = score

                report["details"]["viewports_tested"].append({
                    "name": vp_name,
                    "width": vp_width,
                    "height": vp_height,
                    "status_code": status_code,
                    "score": score,
                })

                await context.close()

            await browser.close()

    except Exception as e:
        logger.exception(f"QA run failed: {e}")
        report["overall_status"] = "fail"
        report["details"]["error"] = str(e)

    # Optional Lighthouse audit (desktop only, against the preview_url)
    if run_lighthouse:
        lh = await _run_lighthouse(preview_url, artifacts_dir)
        if lh:
            report["lighthouse"] = lh
            # Fold the performance score into desktop_score if available
            perf = lh.get("performance")
            if isinstance(perf, (int, float)):
                report["details"]["lighthouse_performance"] = perf

    # Determine overall status
    if report["overall_status"] != "fail":
        min_score = min(report["desktop_score"], report["mobile_score"] or report["desktop_score"])
        if min_score >= 80:
            report["overall_status"] = "pass"
        elif min_score >= 50:
            report["overall_status"] = "warn"
        else:
            report["overall_status"] = "fail"

    report_path = artifacts_dir / "qa_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str))

    # Submit report to agent API (for async /run path)
    if callback_url:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(callback_url, json=report)
        except Exception as e:
            logger.error(f"Failed to call callback URL: {e}")

    logger.info(f"QA complete for {revision_id}: {report['overall_status']}")
    return report


async def _run_axe_core(page, viewport_name: str) -> list[dict]:
    """Inject axe-core from CDN and run accessibility checks."""
    try:
        await page.add_script_tag(url=AXE_CDN)
        results = await page.evaluate(
            """async () => {
                try {
                    const r = await axe.run(document, {resultTypes: ['violations']});
                    return r.violations.map(v => ({
                        id: v.id,
                        impact: v.impact,
                        description: v.description,
                        help: v.help,
                        helpUrl: v.helpUrl,
                        nodes: v.nodes.slice(0, 3).map(n => ({
                            html: (n.html || '').slice(0, 200),
                            target: n.target,
                            failureSummary: (n.failureSummary || '').slice(0, 300),
                        })),
                    }));
                } catch (e) { return {__error: String(e)}; }
            }"""
        )
        if isinstance(results, dict) and results.get("__error"):
            logger.info("axe-core error: %s", results["__error"])
            return []
        flat: list[dict] = []
        for v in results or []:
            flat.append({
                "type": v.get("id"),
                "impact": v.get("impact") or "minor",
                "severity": _impact_to_severity(v.get("impact")),
                "description": v.get("help") or v.get("description"),
                "help_url": v.get("helpUrl"),
                "nodes": v.get("nodes") or [],
                "viewport": viewport_name,
                "source": "axe-core",
            })
        return flat
    except Exception as exc:
        logger.warning("Failed to run axe-core: %s", exc)
        return []


def _impact_to_severity(impact: str | None) -> str:
    return {
        "critical": "critical",
        "serious": "major",
        "moderate": "warning",
        "minor": "info",
    }.get((impact or "").lower(), "info")


async def _run_lighthouse(preview_url: str, artifacts_dir: Path) -> dict | None:
    """Run Lighthouse CLI (if present) against the preview URL. Returns category scores."""
    lh_bin = shutil.which("lighthouse")
    if not lh_bin:
        logger.info("Lighthouse binary not found — skipping")
        return None

    out_file = artifacts_dir / "lighthouse.json"
    cmd = [
        lh_bin,
        preview_url,
        "--quiet",
        "--output=json",
        f"--output-path={out_file}",
        "--chrome-flags=--headless=new --no-sandbox --disable-gpu",
        "--only-categories=performance,accessibility,best-practices,seo",
        "--max-wait-for-load=30000",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            logger.info("Lighthouse exited %d: %s", proc.returncode, (err or b"").decode()[:500])
            return None
    except asyncio.TimeoutError:
        logger.warning("Lighthouse timeout")
        try:
            proc.kill()
        except Exception:
            pass
        return None
    except Exception as exc:
        logger.warning("Lighthouse failed to run: %s", exc)
        return None

    try:
        data = json.loads(out_file.read_text())
    except Exception as exc:
        logger.info("Cannot parse Lighthouse JSON: %s", exc)
        return None

    categories = data.get("categories") or {}
    def _pct(key: str) -> int | None:
        node = categories.get(key) or {}
        s = node.get("score")
        return round(s * 100) if isinstance(s, (int, float)) else None

    return {
        "performance": _pct("performance"),
        "accessibility": _pct("accessibility"),
        "best_practices": _pct("best-practices"),
        "seo": _pct("seo"),
        "report_path": f"/qa/{artifacts_dir.name}/lighthouse.json",
    }


async def _check_accessibility(page, viewport_name: str) -> list[dict]:
    """Basic accessibility heuristics as a fallback when axe-core can't run."""
    issues = []

    images_without_alt = await page.eval_on_selector_all(
        "img",
        """elements => elements
            .filter(e => !e.alt || e.alt.trim() === '')
            .map(e => ({src: e.src, tag: 'img'}))"""
    )
    for img in images_without_alt:
        issues.append({
            "type": "missing_alt_text",
            "element": f"img[src={img.get('src', '?')}]",
            "viewport": viewport_name,
            "severity": "warning",
            "source": "heuristic",
        })

    headings = await page.eval_on_selector_all(
        "h1, h2, h3, h4, h5, h6",
        "elements => elements.map(e => ({tag: e.tagName, text: e.textContent.trim()}))"
    )
    prev_level = 0
    for h in headings:
        level = int(h["tag"][1])
        if level > prev_level + 1 and prev_level > 0:
            issues.append({
                "type": "heading_hierarchy_skip",
                "element": f"{h['tag']}: {h['text'][:50]}",
                "viewport": viewport_name,
                "severity": "info",
                "source": "heuristic",
            })
        prev_level = level

    inputs_without_labels = await page.eval_on_selector_all(
        "input:not([type='hidden']):not([type='submit']):not([type='button'])",
        """elements => elements
            .filter(e => !e.id || !document.querySelector(`label[for="${e.id}"]`))
            .filter(e => !e.getAttribute('aria-label') && !e.getAttribute('aria-labelledby'))
            .map(e => ({type: e.type, name: e.name}))"""
    )
    for inp in inputs_without_labels:
        issues.append({
            "type": "missing_form_label",
            "element": f"input[name={inp.get('name', '?')}]",
            "viewport": viewport_name,
            "severity": "warning",
            "source": "heuristic",
        })

    return issues
