"""Website Modifier agent — analyzes existing sites and applies targeted changes."""

import logging
import re
from app.services.llm_client import call_llm

logger = logging.getLogger(__name__)


def _structural_summary(content: str, path: str) -> str:
    """Produce a compact structural outline of a file to help the LLM reason about it."""
    if path.endswith(".html") or "<html" in content[:200].lower():
        headings = re.findall(r'<(h[1-6])[^>]*>(.*?)</\1>', content, re.IGNORECASE | re.DOTALL)
        sections = re.findall(r'<section[^>]*(?:id="([^"]+)"|class="([^"]+)")', content, re.IGNORECASE)
        ids = re.findall(r'\bid="([^"]+)"', content)
        lines = []
        lines.append(f"headings: {len(headings)}")
        for tag, text in headings[:30]:
            text_clean = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', text)).strip()[:80]
            lines.append(f"  {tag}: {text_clean}")
        lines.append(f"section ids: {[s[0] or s[1] for s in sections[:30]]}")
        lines.append(f"notable ids: {ids[:40]}")
        return "\n".join(lines)
    if path.endswith(".css"):
        rules = re.findall(r'([^{}]+)\s*\{', content)
        return "selectors: " + ", ".join(r.strip()[:60] for r in rules[:60])
    if path.endswith(".js"):
        funcs = re.findall(r'function\s+(\w+)|(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>', content)
        return "functions: " + ", ".join(f[0] or f[1] for f in funcs[:40])
    return f"file size: {len(content)} chars"

ANALYSIS_SYSTEM_PROMPT = """You are a senior web developer analyzing an existing website codebase to plan modifications.

Given the current project files and a change request, produce a modification plan.

Output format:
{
  "change_request_summary": "concise restatement of what was asked",
  "analysis": {
    "current_structure": "brief description of current site structure",
    "current_design": "brief description of current design approach",
    "current_pages": ["list of pages"],
    "current_sections": ["list of key sections"]
  },
  "planned_changes": [
    {
      "file": "path/to/file",
      "action": "modify|create|delete",
      "description": "what will change in this file",
      "impact": "low|medium|high",
      "details": "specific changes to make"
    }
  ],
  "affected_files": ["list of files that will be touched"],
  "risk_assessment": "low|medium|high — overall risk of the changes",
  "requires_full_rebuild": false,
  "rebuild_reason": null,
  "acceptance_criteria": ["list of verifiable criteria for the change"]
}

Rules:
- Prefer targeted edits over full rewrites
- Preserve existing working code and design consistency
- If a change requires touching > 70% of files, set requires_full_rebuild to true and explain why
- Be specific about what will change in each file
- Consider side effects (navigation, responsive behavior, color consistency)"""

APPLY_SYSTEM_PROMPT = """You are a senior web developer applying targeted modifications to an existing website.

Given the current file contents and a modification plan, produce the updated files.

You MUST use EXACTLY this output format. Start immediately with the first <<<FILE block, no preamble:

<<<FILE: index.html>>>
<!DOCTYPE html>
<html>...full file content...</html>
<<<ENDFILE>>>

<<<FILE: css/style.css>>>
body { ... }
<<<ENDFILE>>>

Repeat a <<<FILE: path>>> ... <<<ENDFILE>>> block for each file that needs to change.
Only output files that actually changed.
Do NOT output anything before the first <<<FILE: or after the last <<<ENDFILE>>>.
Do NOT use JSON. Do NOT use markdown code fences. Only the delimiters shown above.

Rules:
- Output the COMPLETE updated file content for each modified file — never truncate or use placeholders
- Preserve all existing functionality that is not being changed
- Maintain design consistency with the rest of the site
- Do not output files that don't need changes
- Keep the same coding style as the existing files
- Ensure all internal links still work after changes"""


async def analyze_for_modification(
    current_files: list[dict],
    change_request: str,
    project_spec: dict | None = None,
    revision_metadata: dict | None = None,
) -> dict:
    """Analyze current site and produce a modification plan."""
    logger.info("Running modifier agent — analysis phase")

    # Build a representation of current files. Give full content for small/medium
    # files so the model sees them entirely. For large files, include the head and
    # tail (useful sections tend to be at the top of HTML / end of CSS) plus a
    # structural summary (headings / ids / classes).
    file_summaries = []
    for f in current_files:
        content = f["content"]
        if len(content) <= 20000:
            file_summaries.append(f"--- {f['path']} ---\n{content}")
        else:
            head = content[:10000]
            tail = content[-4000:]
            summary = _structural_summary(content, f.get("path", ""))
            file_summaries.append(
                f"--- {f['path']} (LARGE FILE — head+tail+structural summary) ---\n"
                f"{head}\n"
                f"...\n[STRUCTURAL SUMMARY]\n{summary}\n\n"
                f"...\n{tail}"
            )

    files_text = "\n\n".join(file_summaries)

    user_prompt = (
        f"Change request: {change_request}\n\n"
        f"Current project files:\n{files_text}"
    )
    if project_spec:
        user_prompt += f"\n\nOriginal project spec:\n{project_spec}"
    if revision_metadata:
        user_prompt += f"\n\nCurrent revision metadata:\n{revision_metadata}"

    result = await call_llm(
        system_prompt=ANALYSIS_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_format="json",
        temperature=0.3,
        max_tokens=4096,
    )

    logger.info(
        f"Modification plan: {len(result.get('planned_changes', []))} changes, "
        f"risk={result.get('risk_assessment', 'unknown')}, "
        f"rebuild={result.get('requires_full_rebuild', False)}"
    )
    return result


def _parse_apply_response(text: str) -> dict:
    """Parse <<<FILE: path>>> ... <<<ENDFILE>>> format into the changed_files manifest."""
    changed_files = []
    # Primary: explicit ENDFILE delimiter
    pattern = re.compile(r'<<<FILE:\s*([^>]+?)>>>\s*\n?(.*?)<<<ENDFILE>>>', re.DOTALL)
    matches = list(pattern.finditer(text))

    if not matches:
        # Fallback: response truncated — no ENDFILE. Treat next <<<FILE: or end-of-string as boundary.
        pattern2 = re.compile(r'<<<FILE:\s*([^>]+?)>>>\s*\n?(.*?)(?=\n?<<<FILE:|\Z)', re.DOTALL)
        matches = list(pattern2.finditer(text))
        if matches:
            logger.warning("Modifier: ENDFILE delimiters missing (response likely truncated) — using EOF fallback")

    for match in matches:
        path = match.group(1).strip()
        content = match.group(2)
        if not path:
            continue
        changed_files.append({
            "path": path,
            "action": "modify",
            "content": content,
            "diff_summary": f"Updated {path}",
        })

    if not changed_files:
        logger.warning("Modifier: no delimited files found in apply response. Raw (first 1000 chars): %s", text[:1000])

    return {
        "changed_files": changed_files,
        "new_files": [],
        "deleted_files": [],
        "migration_notes": [],
        "summary": f"Modified {len(changed_files)} file(s)",
    }


async def apply_modification(
    current_files: list[dict],
    modification_plan: dict,
    change_request: str,
) -> dict:
    """Apply the modification plan to produce updated files."""
    logger.info("Running modifier agent — apply phase")

    # Include full file contents only for files listed in the modification plan (to limit input tokens)
    affected = set(modification_plan.get("affected_files", []))
    relevant_files = []
    for f in current_files:
        if f["path"] in affected:
            relevant_files.append(f"<<<CURRENT: {f['path']}>>>\n{f['content']}\n<<<ENDCURRENT>>>")

    files_text = "\n\n".join(relevant_files)

    user_prompt = (
        f"Original change request: {change_request}\n\n"
        f"Modification plan:\n{modification_plan}\n\n"
        f"Current files to modify:\n{files_text}"
    )

    raw = await call_llm(
        system_prompt=APPLY_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_format=None,
        temperature=0.2,
        max_tokens=16384,
    )

    result = _parse_apply_response(raw if isinstance(raw, str) else str(raw))
    changed_count = len(result.get("changed_files", []))
    logger.info(f"Modifier applied {changed_count} file changes")
    return result
