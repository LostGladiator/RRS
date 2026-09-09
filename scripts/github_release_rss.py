#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on runtime version
    tomllib = None


ATOM_NS = "http://www.w3.org/2005/Atom"
GITHUB_API = "https://api.github.com/repos/{owner}/{repo}/releases?per_page={per_page}"


@dataclass(frozen=True)
class FeedItem:
    title: str
    link: str
    guid: str
    published_at: dt.datetime
    description_html: str
    categories: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an RSS feed from filtered GitHub releases."
    )
    parser.add_argument(
        "--config",
        default="config/projects.toml",
        help="Path to the TOML configuration.",
    )
    parser.add_argument(
        "--output",
        help="Override feed.output_file from the configuration.",
    )
    parser.add_argument(
        "--fixture",
        action="append",
        default=[],
        help="Read local GitHub release JSON instead of calling GitHub. Can be used multiple times.",
    )
    parser.add_argument(
        "--ignore-lookback",
        action="store_true",
        help="Do not drop releases outside the configured lookback window.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matching release titles without writing the feed.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=20,
        help="Number of releases to request per GitHub project.",
    )
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise RuntimeError("Python 3.11 or newer is required for TOML support.")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def base_dir_for(config_path: Path) -> Path:
    resolved = config_path.resolve()
    if resolved.parent.name == "config":
        return resolved.parent.parent
    return resolved.parent


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_github_datetime(value: str | None) -> dt.datetime:
    if not value:
        return utc_now()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def format_rfc822(value: dt.datetime) -> str:
    return email.utils.format_datetime(value.astimezone(dt.timezone.utc), usegmt=True)


def fetch_project_releases(project: dict[str, Any], per_page: int) -> list[dict[str, Any]]:
    url = GITHUB_API.format(
        owner=project["owner"],
        repo=project["repo"],
        per_page=max(1, min(per_page, 100)),
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "rss-release-feed/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API failed for {project['owner']}/{project['repo']}: "
            f"HTTP {exc.code} {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"GitHub API could not be reached for {project['owner']}/{project['repo']}: {exc}"
        ) from exc

    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected GitHub API response for {project['id']}: {payload!r}")
    return payload


def load_fixture_releases(
    fixture_paths: list[str], projects: list[dict[str, Any]], base_dir: Path
) -> dict[str, list[dict[str, Any]]]:
    releases_by_project: dict[str, list[dict[str, Any]]] = {
        project["id"]: [] for project in projects
    }
    for fixture in fixture_paths:
        fixture_path = resolve_path(base_dir, fixture)
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError(f"Fixture entry must be an object: {entry!r}")
            project_id = entry.get("project_id")
            if project_id not in releases_by_project:
                raise RuntimeError(f"Fixture references unknown project_id: {project_id!r}")
            release = dict(entry)
            release.pop("project_id", None)
            releases_by_project[project_id].append(release)
    return releases_by_project


def resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def release_matches(
    release: dict[str, Any],
    project: dict[str, Any],
    now: dt.datetime,
    ignore_lookback: bool,
) -> bool:
    if release.get("draft") and not project.get("include_drafts", False):
        return False
    if release.get("prerelease") and not project.get("include_prereleases", False):
        return False

    haystack = release_haystack(release)
    for keyword in project.get("exclude_keywords", []):
        if keyword.lower() in haystack:
            return False

    include_keywords = [keyword.lower() for keyword in project.get("include_keywords", [])]
    if include_keywords and not any(keyword in haystack for keyword in include_keywords):
        return False

    if not ignore_lookback:
        published_at = parse_github_datetime(
            release.get("published_at") or release.get("created_at")
        )
        lookback_days = int(project.get("lookback_days") or 0)
        if lookback_days > 0 and published_at < now - dt.timedelta(days=lookback_days):
            return False

    return True


def release_haystack(release: dict[str, Any]) -> str:
    parts = [
        release.get("tag_name") or "",
        release.get("name") or "",
        release.get("body") or "",
    ]
    return "\n".join(parts).lower()


def strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def keyword_haystack(release: dict[str, Any]) -> str:
    text = release_haystack(release)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"@\S+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def clean_release_point(text: str) -> str:
    text = re.sub(r"\s+by\s+@\S+(\s+in\s+\S+)?", " ", text)
    text = re.sub(r"\s+in\s+https?://\S+", " ", text)
    text = re.sub(r"\s+in\s+#\d+", " ", text)
    text = strip_markdown(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .")


def extract_release_points(body: str, max_points: int = 4) -> list[str]:
    points: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not re.match(r"^[-*+]\s+", stripped):
            continue
        point = clean_release_point(re.sub(r"^[-*+]\s+", "", stripped))
        lower = point.lower()
        if not point:
            continue
        if lower.startswith(("full changelog", "new contributors", "contributors")):
            continue
        points.append(point)
        if len(points) >= max_points:
            break
    return points


def short_text(text: str, max_chars: int) -> str:
    clean = strip_markdown(text)
    if not clean:
        return "Die Release Notes enthalten keine weiteren technischen Details."
    if len(clean) <= max_chars:
        return clean

    sentence_end = max(clean.rfind(". ", 0, max_chars), clean.rfind("; ", 0, max_chars))
    if sentence_end >= 120:
        return clean[: sentence_end + 1].strip()
    return clean[: max_chars - 1].rstrip() + "..."


def classify_release(project: dict[str, Any], release: dict[str, Any]) -> str:
    haystack = keyword_haystack(release)
    repo = project.get("repo", "")
    has_security = any(keyword in haystack for keyword in ("cve", "vulnerability", "security"))
    if (repo.endswith("helm") or "helm" in haystack or "chart" in haystack) and has_security:
        return "Helm/Deployment Release mit Security-Bezug"
    if has_security:
        return "Security oder Vulnerability Fix"
    if repo.endswith("helm") or "helm" in haystack or "chart" in haystack:
        return "Helm/Deployment Release"
    if any(keyword in haystack for keyword in ("breaking", "migration", "upgrade")):
        return "Upgrade- oder Migrationshinweis"
    if any(keyword in haystack for keyword in ("fix", "bug")):
        return "Bugfix Release"
    if any(keyword in haystack for keyword in ("feature", "feat")):
        return "Feature Release"
    return "Release"


def relevance_text(project: dict[str, Any], release: dict[str, Any]) -> str:
    haystack = keyword_haystack(release)
    hits = [
        keyword
        for keyword in project.get("severity_keywords", [])
        if keyword.lower() in haystack
    ]
    if hits:
        return "Treffer: " + ", ".join(hits[:5])
    interests = project.get("interests", [])
    if interests:
        return "Relevant gemaess Projektinteressen: " + "; ".join(interests[:2])
    return "Keine besondere Relevanzmarkierung gefunden."


def release_type(release: dict[str, Any]) -> str:
    if release.get("draft"):
        return "Draft"
    if release.get("prerelease"):
        return "Pre-Release"
    return "Offizielles GitHub Release"


def render_local_summary(
    project: dict[str, Any],
    release: dict[str, Any],
    max_chars: int,
) -> str:
    product = project.get("product", project.get("name", "Das Projekt"))
    release_name = release.get("name") or release.get("tag_name") or "Das Release"
    classification = classify_release(project, release)
    intro = f"{release_name} wurde fuer {product} veroeffentlicht."

    if classification.startswith("Security"):
        intro += " Die Release Notes weisen auf Security- oder Vulnerability-Fixes hin."
    elif "Security-Bezug" in classification:
        intro += " Die Aenderung betrifft Deployment/Helm und hat einen Security-Bezug."
    else:
        intro += f" Einordnung: {classification}."

    body = release.get("body") or ""
    points = extract_release_points(body, max_points=4)
    if points:
        return short_text(f"{intro} Wichtige Punkte: {'; '.join(points)}.", max_chars)

    detail = short_text(body, max_chars)
    if detail == "Die Release Notes enthalten keine weiteren technischen Details.":
        return f"{intro} Die Release Notes enthalten keine weiteren technischen Details."
    return short_text(f"{intro} Release-Notiz: {detail}", max_chars)


def render_external_summary(
    command: list[str],
    base_dir: Path,
    project: dict[str, Any],
    release: dict[str, Any],
    max_chars: int,
) -> str:
    payload = {
        "project": project,
        "release": release,
        "max_chars": max_chars,
    }
    try:
        completed = subprocess.run(
            command,
            cwd=base_dir,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=90,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        fallback = render_local_summary(project, release, max_chars)
        return f"{fallback} Hinweis: Die externe AI-Zusammenfassung konnte nicht erzeugt werden ({exc})."

    output = completed.stdout.strip()
    if not output:
        return render_local_summary(project, release, max_chars)
    return short_text(output, max_chars)


def render_description_html(
    project: dict[str, Any],
    release: dict[str, Any],
    summary_cfg: dict[str, Any],
    base_dir: Path,
) -> str:
    max_chars = int(summary_cfg.get("max_chars", 1200))
    mode = summary_cfg.get("mode", "local-template")
    command = summary_cfg.get("external_command") or []
    if mode == "external-command" and command:
        summary = render_external_summary(command, base_dir, project, release, max_chars)
    else:
        summary = render_local_summary(project, release, max_chars)

    title = release.get("name") or release.get("tag_name") or "Release"
    tag = release.get("tag_name") or "unknown"
    owner = project.get("owner", "")
    repo = project.get("repo", "")
    url = release.get("html_url") or project.get("release_page") or project.get("homepage")
    classification = classify_release(project, release)
    relevance = relevance_text(project, release)

    return "\n".join(
        [
            f"<h2>{escape(project.get('product', project.get('name', 'Projekt')))}: {escape(title)}</h2>",
            f"<p><strong>Kurzfassung:</strong> {escape(summary)}</p>",
            "<ul>",
            f"<li><strong>Version:</strong> {escape(tag)}</li>",
            f"<li><strong>Repository:</strong> {escape(owner)}/{escape(repo)}</li>",
            f"<li><strong>Release-Typ:</strong> {escape(release_type(release))}</li>",
            f"<li><strong>Relevanz:</strong> {escape(relevance)}</li>",
            "</ul>",
            f"<p><strong>Einordnung:</strong> {escape(classification)}</p>",
            f'<p><strong>Quelle:</strong> <a href="{escape(url)}">{escape(url)}</a></p>',
        ]
    )


def escape(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_feed_items(
    config: dict[str, Any],
    releases_by_project: dict[str, list[dict[str, Any]]],
    base_dir: Path,
    ignore_lookback: bool,
) -> list[FeedItem]:
    now = utc_now()
    items: list[FeedItem] = []
    summary_cfg = config.get("summary", {})

    for project in config.get("projects", []):
        if not project.get("enabled", True):
            continue
        for release in releases_by_project.get(project["id"], []):
            if not release_matches(release, project, now, ignore_lookback):
                continue
            published_at = parse_github_datetime(
                release.get("published_at") or release.get("created_at")
            )
            title = f"{project.get('product', project['name'])}: {release.get('name') or release.get('tag_name')}"
            link = release.get("html_url") or project.get("release_page") or project["homepage"]
            guid = release.get("html_url") or str(release.get("id") or link)
            items.append(
                FeedItem(
                    title=title,
                    link=link,
                    guid=guid,
                    published_at=published_at,
                    description_html=render_description_html(
                        project, release, summary_cfg, base_dir
                    ),
                    categories=list(project.get("categories", [])),
                )
            )

    items.sort(key=lambda item: item.published_at, reverse=True)
    max_items = int(config.get("feed", {}).get("max_items", 30))
    return items[:max_items]


def write_rss(config: dict[str, Any], items: list[FeedItem], output_path: Path) -> None:
    feed_cfg = config.get("feed", {})
    ET.register_namespace("atom", ATOM_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    set_text(channel, "title", feed_cfg.get("title", "GitHub Release News"))
    set_text(channel, "link", feed_cfg.get("link", "https://example.invalid/rss/feed.xml"))
    set_text(channel, "description", feed_cfg.get("description", "GitHub Release News"))
    set_text(channel, "language", feed_cfg.get("language", "de-CH"))
    set_text(channel, "lastBuildDate", format_rfc822(utc_now()))
    set_text(channel, "generator", "scripts/github_release_rss.py")
    set_text(channel, "ttl", str(feed_cfg.get("ttl_minutes", 1440)))

    if feed_cfg.get("managing_editor"):
        set_text(channel, "managingEditor", feed_cfg["managing_editor"])
    if feed_cfg.get("webmaster"):
        set_text(channel, "webMaster", feed_cfg["webmaster"])
    if feed_cfg.get("copyright"):
        set_text(channel, "copyright", feed_cfg["copyright"])

    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": feed_cfg.get("self_url", feed_cfg.get("link", "")),
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for feed_item in items:
        item = ET.SubElement(channel, "item")
        set_text(item, "title", feed_item.title)
        set_text(item, "link", feed_item.link)
        guid = set_text(item, "guid", feed_item.guid)
        guid.set("isPermaLink", "true")
        set_text(item, "pubDate", format_rfc822(feed_item.published_at))
        set_text(item, "description", feed_item.description_html)
        for category in feed_item.categories:
            set_text(item, "category", category)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def set_text(parent: ET.Element, name: str, value: Any) -> ET.Element:
    child = ET.SubElement(parent, name)
    child.text = str(value)
    return child


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    config = load_config(config_path)
    base_dir = base_dir_for(config_path)

    projects = [project for project in config.get("projects", []) if project.get("enabled", True)]
    if args.fixture:
        releases_by_project = load_fixture_releases(args.fixture, projects, base_dir)
    else:
        releases_by_project = {
            project["id"]: fetch_project_releases(project, args.per_page)
            for project in projects
        }

    items = build_feed_items(config, releases_by_project, base_dir, args.ignore_lookback)

    if args.dry_run:
        for item in items:
            print(f"{item.published_at.date()} {item.title} -> {item.link}")
        print(f"{len(items)} item(s) matched")
        return 0

    output_path = resolve_path(
        base_dir,
        args.output or config.get("feed", {}).get("output_file", "public/feed.xml"),
    )
    write_rss(config, items, output_path)
    print(f"Wrote {len(items)} item(s) to {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
