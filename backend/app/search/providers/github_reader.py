"""
GitHub reader provider for structured repo/file reads via GitHub REST API.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, Iterable, List, Sequence
from urllib.parse import quote, unquote, urlparse

import httpx

from ..models import SearchResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitHubTarget:
    owner: str
    repo: str
    kind: str
    tail_segments: tuple[str, ...] = ()


@dataclass(frozen=True)
class GitHubFileExcerpt:
    path: str
    text: str
    truncated: bool = False


class GitHubReaderProvider:
    """Reads public GitHub repos, directories, and files via the GitHub API."""

    IMPORTANT_FILENAMES = {
        "readme.md": 140,
        "readme.rst": 140,
        "package.json": 120,
        "pyproject.toml": 118,
        "requirements.txt": 116,
        "setup.py": 114,
        "go.mod": 112,
        "cargo.toml": 112,
        "pom.xml": 110,
        "build.gradle": 108,
        "build.gradle.kts": 108,
        "dockerfile": 104,
        "docker-compose.yml": 102,
        "docker-compose.yaml": 102,
        "makefile": 100,
        "main.py": 98,
        "app.py": 96,
        "server.py": 96,
        "index.ts": 96,
        "index.js": 96,
        "index.tsx": 94,
        "index.jsx": 94,
        "main.ts": 94,
        "main.js": 94,
        "main.tsx": 92,
        "main.jsx": 92,
        "__init__.py": 88,
    }
    IMPORTANT_DIRECTORIES = {
        "src": 30,
        "app": 28,
        "lib": 24,
        "backend": 24,
        "frontend": 24,
        "server": 22,
        "client": 22,
        "cmd": 22,
        "packages": 20,
    }
    TEXT_EXTENSIONS = {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".m",
        ".md",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scss",
        ".sh",
        ".sql",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
    CODE_EXTENSIONS = {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".m",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".swift",
        ".ts",
        ".tsx",
    }
    NON_TEXT_EXTENSIONS = {
        ".avif",
        ".bin",
        ".bmp",
        ".class",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".so",
        ".tar",
        ".wasm",
        ".webp",
        ".zip",
    }
    MAX_DIRECTORY_ENTRIES = 18
    MAX_EXPLORED_DIRECTORIES = 2
    DEFAULT_MAX_SELECTED_FILES = 4
    DEFAULT_MAX_FILE_CHARS = 1800
    DEFAULT_MAX_README_CHARS = 1800
    DEFAULT_MAX_RENDER_CHARS = 7200
    GITHUB_RESERVED_ROOTS = {
        "about",
        "account",
        "contact",
        "customer-stories",
        "enterprise",
        "events",
        "explore",
        "features",
        "issues",
        "login",
        "marketplace",
        "notifications",
        "organizations",
        "orgs",
        "pricing",
        "pulls",
        "search",
        "security",
        "settings",
        "signup",
        "site",
        "sponsors",
        "team",
        "topics",
        "trending",
        "users",
    }

    def __init__(
        self,
        enabled: bool = True,
        api_endpoint: str = "https://api.github.com",
        token: str = "",
        timeout_seconds: float = 18.0,
        max_selected_files: int = DEFAULT_MAX_SELECTED_FILES,
        max_file_chars: int = DEFAULT_MAX_FILE_CHARS,
        max_render_chars: int = DEFAULT_MAX_RENDER_CHARS,
    ) -> None:
        normalized_endpoint = (api_endpoint or "https://api.github.com").strip().rstrip("/")
        self.enabled = bool(enabled)
        self.api_endpoint = normalized_endpoint or "https://api.github.com"
        self.token = (token or "").strip()
        self.timeout_seconds = max(4.0, float(timeout_seconds or 18.0))
        self.max_selected_files = max(1, min(6, int(max_selected_files or self.DEFAULT_MAX_SELECTED_FILES)))
        self.max_file_chars = max(400, min(5000, int(max_file_chars or self.DEFAULT_MAX_FILE_CHARS)))
        self.max_render_chars = max(1200, min(12000, int(max_render_chars or self.DEFAULT_MAX_RENDER_CHARS)))
        self._cache_ttl_seconds = 600.0
        self._cache: dict[str, tuple[float, SearchResult]] = {}

    @property
    def is_enabled(self) -> bool:
        return self.enabled

    def supports_url(self, target_url: str) -> bool:
        return self._parse_target(target_url) is not None

    async def read(self, target_url: str) -> SearchResult | None:
        if not self.is_enabled:
            return None

        target = self._parse_target(target_url)
        if target is None:
            return None

        cached_result = self._cache_get(target_url)
        if cached_result is not None:
            return cached_result

        timeout = httpx.Timeout(
            connect=min(10.0, self.timeout_seconds),
            read=self.timeout_seconds,
            write=min(10.0, self.timeout_seconds),
            pool=min(10.0, self.timeout_seconds),
        )

        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                async with httpx.AsyncClient(
                    base_url=self.api_endpoint,
                    timeout=timeout,
                    headers=self._build_headers(),
                    follow_redirects=True,
                ) as client:
                    repo_data = await self._get_json(client, f"/repos/{target.owner}/{target.repo}")
                    if not isinstance(repo_data, dict):
                        if attempt < 2:
                            await asyncio.sleep(0.25 * attempt)
                            continue
                        return None

                    if target.kind == "repo":
                        result = await self._read_repo(client, target, repo_data, target_url)
                    elif target.kind == "tree":
                        result = await self._read_directory(client, target, repo_data, target_url)
                    else:
                        result = await self._read_file(client, target, repo_data, target_url)

                    if result is not None:
                        self._cache_set(target_url, result)
                        return result
                    if attempt >= 2:
                        return result
            except Exception as e:
                last_error = e
                logger.warning(
                    "GitHub reader failed url=%s attempt=%s error_type=%s error=%s",
                    target_url,
                    attempt,
                    type(e).__name__,
                    e,
                )
            if attempt < 2:
                await asyncio.sleep(0.25 * attempt)

        if last_error is not None:
            logger.debug("GitHub reader exhausted retries url=%s last_error=%r", target_url, last_error)
        return None

    def _cache_get(self, target_url: str) -> SearchResult | None:
        record = self._cache.get(target_url)
        if not record:
            return None
        created_at, result = record
        if perf_counter() - created_at > self._cache_ttl_seconds:
            self._cache.pop(target_url, None)
            return None
        return replace(result)

    def _cache_set(self, target_url: str, result: SearchResult) -> None:
        self._cache[target_url] = (perf_counter(), replace(result))
        if len(self._cache) > 128:
            oldest_key = min(self._cache.items(), key=lambda item: item[1][0])[0]
            self._cache.pop(oldest_key, None)

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "PigTex/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _parse_target(self, target_url: str) -> GitHubTarget | None:
        normalized_url = (target_url or "").strip()
        if not normalized_url.lower().startswith(("http://", "https://")):
            return None

        parsed = urlparse(normalized_url)
        host = (parsed.netloc or "").strip().lower()
        segments = [unquote(part) for part in (parsed.path or "").split("/") if part]
        if not segments:
            return None

        if host == "raw.githubusercontent.com":
            if len(segments) < 4:
                return None
            owner = segments[0].strip()
            repo = segments[1].strip().removesuffix(".git")
            if not owner or not repo:
                return None
            return GitHubTarget(owner=owner, repo=repo, kind="blob", tail_segments=tuple(segments[2:]))

        if host not in {"github.com", "www.github.com"}:
            return None
        if len(segments) < 2:
            return None

        if segments[0].lower() in self.GITHUB_RESERVED_ROOTS:
            return None

        owner = segments[0].strip()
        repo = segments[1].strip().removesuffix(".git")
        if not owner or not repo:
            return None

        if len(segments) == 2:
            return GitHubTarget(owner=owner, repo=repo, kind="repo")

        route = segments[2].strip().lower()
        if route in {"blob", "tree"}:
            return GitHubTarget(owner=owner, repo=repo, kind=route, tail_segments=tuple(segments[3:]))

        return None

    async def _read_repo(
        self,
        client: httpx.AsyncClient,
        target: GitHubTarget,
        repo_data: dict[str, Any],
        source_url: str,
    ) -> SearchResult | None:
        ref = str(repo_data.get("default_branch") or "HEAD").strip() or "HEAD"
        root_entries = await self._get_contents(client, target.owner, target.repo, "", ref)
        readme_item = await self._get_json(
            client,
            f"/repos/{target.owner}/{target.repo}/readme",
            params={"ref": ref},
        )
        readme_text = await self._extract_text_from_content_item(client, readme_item)
        selected_files = await self._select_repo_files(
            client,
            owner=target.owner,
            repo=target.repo,
            ref=ref,
            root_entries=root_entries if isinstance(root_entries, list) else [],
        )

        full_content = self._compose_repo_document(
            repo_data=repo_data,
            ref=ref,
            root_entries=root_entries if isinstance(root_entries, list) else [],
            readme_text=readme_text,
            selected_files=selected_files,
        )
        snippet = self._compose_repo_snippet(repo_data, ref=ref, selected_files=selected_files)
        return SearchResult(
            title=f"{repo_data.get('full_name') or f'{target.owner}/{target.repo}'} repository",
            url=source_url,
            snippet=snippet,
            full_content=full_content,
            relevance_score=0.94,
            source_provider="github_api",
            published_at=str(repo_data.get("pushed_at") or repo_data.get("updated_at") or "").strip() or None,
            domain="github.com",
        )

    async def _read_directory(
        self,
        client: httpx.AsyncClient,
        target: GitHubTarget,
        repo_data: dict[str, Any],
        source_url: str,
    ) -> SearchResult | None:
        ref, repo_path = await self._resolve_ref_and_path(client, target, repo_data)
        contents = await self._get_contents(client, target.owner, target.repo, repo_path, ref)
        if isinstance(contents, dict):
            return await self._build_file_result(
                client,
                repo_data=repo_data,
                owner=target.owner,
                repo=target.repo,
                ref=ref,
                repo_path=repo_path,
                item=contents,
                source_url=source_url,
            )
        if not isinstance(contents, list):
            return None

        selected_files = await self._select_directory_files(
            client,
            owner=target.owner,
            repo=target.repo,
            ref=ref,
            directory_entries=contents,
        )
        full_content = self._compose_directory_document(
            repo_data=repo_data,
            ref=ref,
            repo_path=repo_path,
            directory_entries=contents,
            selected_files=selected_files,
        )
        snippet = self._compose_directory_snippet(repo_data, repo_path=repo_path, ref=ref, directory_entries=contents)
        path_label = repo_path or "/"
        return SearchResult(
            title=f"{repo_data.get('full_name') or f'{target.owner}/{target.repo}'}:{path_label}",
            url=source_url,
            snippet=snippet,
            full_content=full_content,
            relevance_score=0.96,
            source_provider="github_api",
            published_at=str(repo_data.get("pushed_at") or repo_data.get("updated_at") or "").strip() or None,
            domain="github.com",
        )

    async def _read_file(
        self,
        client: httpx.AsyncClient,
        target: GitHubTarget,
        repo_data: dict[str, Any],
        source_url: str,
    ) -> SearchResult | None:
        ref, repo_path = await self._resolve_ref_and_path(client, target, repo_data)
        if not repo_path:
            return None
        contents = await self._get_contents(client, target.owner, target.repo, repo_path, ref)
        if not isinstance(contents, dict):
            return None
        return await self._build_file_result(
            client,
            repo_data=repo_data,
            owner=target.owner,
            repo=target.repo,
            ref=ref,
            repo_path=repo_path,
            item=contents,
            source_url=source_url,
        )

    async def _build_file_result(
        self,
        client: httpx.AsyncClient,
        repo_data: dict[str, Any],
        owner: str,
        repo: str,
        ref: str,
        repo_path: str,
        item: dict[str, Any],
        source_url: str,
    ) -> SearchResult | None:
        content_text = await self._extract_text_from_content_item(client, item)
        if not content_text:
            fallback_title = f"{repo_data.get('full_name') or f'{owner}/{repo}'}:{repo_path}"
            summary = f"{fallback_title} at ref {ref}. The file appears to be binary or could not be decoded as text."
            return SearchResult(
                title=fallback_title,
                url=source_url,
                snippet=summary,
                full_content=summary,
                relevance_score=0.97,
                source_provider="github_api",
                published_at=str(repo_data.get("pushed_at") or repo_data.get("updated_at") or "").strip() or None,
                domain="github.com",
            )

        clipped_text, truncated = self._truncate_text(
            content_text,
            limit=min(self.max_render_chars - 800, self.max_file_chars * 2),
        )
        full_content = self._compose_file_document(
            repo_data=repo_data,
            ref=ref,
            repo_path=repo_path,
            content_text=clipped_text,
            truncated=truncated,
        )
        snippet = self._compose_file_snippet(repo_data, repo_path=repo_path, ref=ref, content_text=content_text)
        return SearchResult(
            title=f"{repo_data.get('full_name') or f'{owner}/{repo}'}:{repo_path}",
            url=source_url,
            snippet=snippet,
            full_content=full_content,
            relevance_score=0.97,
            source_provider="github_api",
            published_at=str(repo_data.get("pushed_at") or repo_data.get("updated_at") or "").strip() or None,
            domain="github.com",
        )

    async def _resolve_ref_and_path(
        self,
        client: httpx.AsyncClient,
        target: GitHubTarget,
        repo_data: dict[str, Any],
    ) -> tuple[str, str]:
        default_branch = str(repo_data.get("default_branch") or "HEAD").strip() or "HEAD"
        tail_segments = list(target.tail_segments)
        if not tail_segments:
            return default_branch, ""

        ref_candidates = await self._get_ref_candidates(
            client,
            owner=target.owner,
            repo=target.repo,
            default_branch=default_branch,
        )
        return self._resolve_ref_path_from_candidates(tail_segments, ref_candidates, default_branch=default_branch)

    async def _get_ref_candidates(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        default_branch: str,
    ) -> List[str]:
        candidates: List[str] = []
        seen: set[str] = set()

        def _add(value: str) -> None:
            normalized = (value or "").strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append(normalized)

        _add(default_branch)
        branches = await self._get_json(
            client,
            f"/repos/{owner}/{repo}/branches",
            params={"per_page": 100},
        )
        if isinstance(branches, list):
            for item in branches:
                if isinstance(item, dict):
                    _add(str(item.get("name") or ""))

        tags = await self._get_json(
            client,
            f"/repos/{owner}/{repo}/tags",
            params={"per_page": 50},
        )
        if isinstance(tags, list):
            for item in tags:
                if isinstance(item, dict):
                    _add(str(item.get("name") or ""))

        candidates.sort(key=lambda value: len(value.split("/")), reverse=True)
        return candidates

    @staticmethod
    def _resolve_ref_path_from_candidates(
        tail_segments: Sequence[str],
        ref_candidates: Sequence[str],
        default_branch: str,
    ) -> tuple[str, str]:
        normalized_segments = [segment.strip() for segment in tail_segments if segment.strip()]
        if not normalized_segments:
            return default_branch, ""

        for ref in ref_candidates:
            ref_parts = [part.strip() for part in str(ref or "").split("/") if part.strip()]
            if not ref_parts or len(ref_parts) > len(normalized_segments):
                continue
            if normalized_segments[: len(ref_parts)] == ref_parts:
                repo_path = "/".join(normalized_segments[len(ref_parts):])
                return "/".join(ref_parts), repo_path

        ref = normalized_segments[0]
        repo_path = "/".join(normalized_segments[1:])
        return ref or default_branch, repo_path

    async def _get_contents(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        repo_path: str,
        ref: str,
    ) -> Any:
        encoded_path = quote(repo_path.strip("/"), safe="/._-")
        endpoint = f"/repos/{owner}/{repo}/contents"
        if encoded_path:
            endpoint = f"{endpoint}/{encoded_path}"
        return await self._get_json(client, endpoint, params={"ref": ref})

    async def _select_repo_files(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        ref: str,
        root_entries: Sequence[dict[str, Any]],
    ) -> List[GitHubFileExcerpt]:
        root_files = [item for item in root_entries if isinstance(item, dict) and str(item.get("type") or "") == "file"]
        candidates = [item for item in root_files if not str(item.get("name") or "").lower().startswith("readme")]

        directory_entries = [item for item in root_entries if isinstance(item, dict) and str(item.get("type") or "") == "dir"]
        directory_entries.sort(key=self._score_directory_item, reverse=True)
        for directory_item in directory_entries[: self.MAX_EXPLORED_DIRECTORIES]:
            child_items = await self._get_contents(
                client,
                owner=owner,
                repo=repo,
                repo_path=str(directory_item.get("path") or ""),
                ref=ref,
            )
            if not isinstance(child_items, list):
                continue
            candidates.extend(
                item
                for item in child_items
                if isinstance(item, dict) and str(item.get("type") or "") == "file"
            )

        return await self._load_ranked_file_excerpts(client, candidates)

    async def _select_directory_files(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        ref: str,
        directory_entries: Sequence[dict[str, Any]],
    ) -> List[GitHubFileExcerpt]:
        candidates = [
            item
            for item in directory_entries
            if isinstance(item, dict) and str(item.get("type") or "") == "file"
        ]
        return await self._load_ranked_file_excerpts(client, candidates)

    async def _load_ranked_file_excerpts(
        self,
        client: httpx.AsyncClient,
        candidates: Sequence[dict[str, Any]],
    ) -> List[GitHubFileExcerpt]:
        scored_candidates: List[tuple[int, dict[str, Any]]] = []
        for item in candidates:
            score = self._score_file_item(item)
            if score <= 0:
                continue
            scored_candidates.append((score, item))

        scored_candidates.sort(
            key=lambda pair: (
                pair[0],
                -int(pair[1].get("size") or 0),
                str(pair[1].get("path") or ""),
            ),
            reverse=True,
        )

        outputs: List[GitHubFileExcerpt] = []
        seen_paths: set[str] = set()
        remaining_budget = max(self.max_file_chars, self.max_render_chars - 1800)

        for _, item in scored_candidates:
            if len(outputs) >= self.max_selected_files or remaining_budget < 240:
                break
            path = str(item.get("path") or "").strip()
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)

            file_text = await self._extract_text_from_content_item(client, item)
            if not file_text:
                continue

            char_limit = min(self.max_file_chars, remaining_budget)
            clipped_text, truncated = self._truncate_text(file_text, limit=char_limit)
            if not clipped_text:
                continue
            outputs.append(GitHubFileExcerpt(path=path, text=clipped_text, truncated=truncated))
            remaining_budget -= len(clipped_text)

        return outputs

    def _score_directory_item(self, item: dict[str, Any]) -> int:
        path = str(item.get("path") or "").strip().lower()
        if not path:
            return 0
        score = 0
        for segment in path.split("/"):
            score += self.IMPORTANT_DIRECTORIES.get(segment, 0)
        return score

    def _score_file_item(self, item: dict[str, Any]) -> int:
        path = str(item.get("path") or "").strip()
        if not path:
            return 0

        lowered_path = path.lower()
        filename = lowered_path.rsplit("/", 1)[-1]
        extension = ""
        if "." in filename:
            extension = f".{filename.rsplit('.', 1)[-1]}"

        if extension in self.NON_TEXT_EXTENSIONS:
            return -10
        if filename.endswith((".lock", ".min.js", ".min.css")):
            return -5

        score = self.IMPORTANT_FILENAMES.get(filename, 0)
        if extension in self.TEXT_EXTENSIONS:
            score += 24
        if extension in self.CODE_EXTENSIONS:
            score += 24
        if any(part in lowered_path for part in ("/test", "/tests", "/spec", "__tests__")):
            score -= 12
        if any(part in lowered_path for part in ("/dist/", "/build/", "/vendor/", "/coverage/")):
            score -= 20

        has_source_dir = False
        for segment in lowered_path.split("/"):
            score += self.IMPORTANT_DIRECTORIES.get(segment, 0)
            if segment in self.IMPORTANT_DIRECTORIES:
                has_source_dir = True

        if has_source_dir and extension in self.CODE_EXTENSIONS:
            score += 56

        size = int(item.get("size") or 0)
        if size > 250_000:
            score -= 28
        elif size <= 24_000:
            score += 8
        elif size <= 80_000:
            score += 3

        return score

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await client.get(endpoint, params=params)
            if response.status_code in {403, 404}:
                logger.info("GitHub API request returned status=%s endpoint=%s", response.status_code, endpoint)
                return None
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.info("GitHub API request failed endpoint=%s error=%s", endpoint, e)
            return None

    async def _extract_text_from_content_item(
        self,
        client: httpx.AsyncClient,
        item: Any,
    ) -> str:
        if not isinstance(item, dict):
            return ""

        if str(item.get("type") or "") != "file":
            return ""

        raw_content = str(item.get("content") or "")
        encoding = str(item.get("encoding") or "").strip().lower()
        if raw_content and encoding == "base64":
            decoded = self._decode_base64_text(raw_content)
            if decoded:
                return decoded

        download_url = str(item.get("download_url") or "").strip()
        if download_url:
            downloaded = await self._get_text(client, download_url)
            if downloaded:
                return downloaded

        endpoint = str(item.get("url") or "").strip()
        if endpoint.startswith(self.api_endpoint):
            relative_endpoint = endpoint[len(self.api_endpoint) :]
            nested_item = await self._get_json(client, relative_endpoint)
            if nested_item and nested_item is not item:
                return await self._extract_text_from_content_item(client, nested_item)

        return ""

    async def _get_text(self, client: httpx.AsyncClient, url: str) -> str:
        try:
            response = await client.get(url, headers={"Accept": "text/plain"})
            if response.status_code in {403, 404}:
                return ""
            response.raise_for_status()
            return response.text
        except Exception:
            return ""

    def _decode_base64_text(self, raw_content: str) -> str:
        cleaned = (raw_content or "").strip()
        if not cleaned:
            return ""
        try:
            decoded = base64.b64decode(cleaned, validate=False)
        except Exception:
            return ""

        if b"\x00" in decoded:
            return ""

        try:
            return decoded.decode("utf-8")
        except UnicodeDecodeError:
            return decoded.decode("utf-8", errors="replace")

    def _compose_repo_snippet(
        self,
        repo_data: dict[str, Any],
        ref: str,
        selected_files: Sequence[GitHubFileExcerpt],
    ) -> str:
        full_name = str(repo_data.get("full_name") or "GitHub repo").strip()
        description = str(repo_data.get("description") or "").strip()
        language = str(repo_data.get("language") or "").strip()
        summary_parts = [full_name]
        if description:
            summary_parts.append(description)
        summary_parts.append(f"default branch {ref}")
        if language:
            summary_parts.append(f"primary language {language}")
        if selected_files:
            preview_paths = ", ".join(file.path for file in selected_files[:3])
            summary_parts.append(f"selected files: {preview_paths}")
        return ". ".join(part for part in summary_parts if part).strip()

    def _compose_directory_snippet(
        self,
        repo_data: dict[str, Any],
        repo_path: str,
        ref: str,
        directory_entries: Sequence[dict[str, Any]],
    ) -> str:
        full_name = str(repo_data.get("full_name") or "GitHub repo").strip()
        entry_count = len(directory_entries)
        path_label = repo_path or "/"
        return f"{full_name} directory {path_label} at ref {ref}. {entry_count} visible item(s)."

    def _compose_file_snippet(
        self,
        repo_data: dict[str, Any],
        repo_path: str,
        ref: str,
        content_text: str,
    ) -> str:
        full_name = str(repo_data.get("full_name") or "GitHub repo").strip()
        preview = self._compact_text(content_text, limit=240)
        return f"{full_name} file {repo_path} at ref {ref}. {preview}".strip()

    def _compose_repo_document(
        self,
        repo_data: dict[str, Any],
        ref: str,
        root_entries: Sequence[dict[str, Any]],
        readme_text: str,
        selected_files: Sequence[GitHubFileExcerpt],
    ) -> str:
        lines = self._render_repo_metadata(repo_data, ref=ref)
        if selected_files:
            lines.extend(["", "## Selected Files"])
            for excerpt in selected_files:
                lines.extend(self._render_file_excerpt(excerpt))

        if root_entries:
            lines.extend(["", "## Root Contents"])
            for item in root_entries[: self.MAX_DIRECTORY_ENTRIES]:
                item_type = str(item.get("type") or "").strip() or "file"
                path = str(item.get("path") or "").strip()
                size = int(item.get("size") or 0)
                size_label = f" ({size} bytes)" if size and item_type == "file" else ""
                lines.append(f"- {item_type}: {path}{size_label}")

        if readme_text:
            clipped_readme, truncated = self._truncate_text(
                readme_text,
                limit=min(self.max_file_chars, self.DEFAULT_MAX_README_CHARS),
            )
            lines.extend(["", "## README", clipped_readme])
            if truncated:
                lines.append("[README truncated]")

        return self._join_and_limit(lines)

    def _compose_directory_document(
        self,
        repo_data: dict[str, Any],
        ref: str,
        repo_path: str,
        directory_entries: Sequence[dict[str, Any]],
        selected_files: Sequence[GitHubFileExcerpt],
    ) -> str:
        lines = self._render_repo_metadata(repo_data, ref=ref)
        if selected_files:
            lines.extend(["", "## Selected Files"])
            for excerpt in selected_files:
                lines.extend(self._render_file_excerpt(excerpt))

        lines.extend(["", f"## Directory: {repo_path or '/'}"])
        for item in directory_entries[: self.MAX_DIRECTORY_ENTRIES]:
            item_type = str(item.get("type") or "").strip() or "file"
            path = str(item.get("path") or "").strip()
            size = int(item.get("size") or 0)
            size_label = f" ({size} bytes)" if size and item_type == "file" else ""
            lines.append(f"- {item_type}: {path}{size_label}")

        return self._join_and_limit(lines)

    def _compose_file_document(
        self,
        repo_data: dict[str, Any],
        ref: str,
        repo_path: str,
        content_text: str,
        truncated: bool,
    ) -> str:
        lines = self._render_repo_metadata(repo_data, ref=ref)
        lines.extend(["", f"## File: {repo_path}"])
        fence = self._markdown_fence(content_text)
        language = self._language_hint(repo_path)
        lines.append(f"{fence}{language}")
        lines.append(content_text.rstrip())
        lines.append(fence)
        if truncated:
            lines.append("[File content truncated]")
        return self._join_and_limit(lines)

    def _render_repo_metadata(self, repo_data: dict[str, Any], ref: str) -> List[str]:
        full_name = str(repo_data.get("full_name") or "GitHub repo").strip()
        html_url = str(repo_data.get("html_url") or "").strip()
        description = str(repo_data.get("description") or "").strip()
        language = str(repo_data.get("language") or "").strip()
        topics = repo_data.get("topics") or []
        stars = int(repo_data.get("stargazers_count") or 0)
        forks = int(repo_data.get("forks_count") or 0)
        open_issues = int(repo_data.get("open_issues_count") or 0)
        pushed_at = str(repo_data.get("pushed_at") or repo_data.get("updated_at") or "").strip()

        lines = [f"# GitHub Repository: {full_name}"]
        if html_url:
            lines.append(f"URL: {html_url}")
        if description:
            lines.append(f"Description: {description}")
        lines.append(f"Ref: {ref}")
        stats = [f"Stars: {stars}", f"Forks: {forks}", f"Open issues: {open_issues}"]
        if language:
            stats.append(f"Primary language: {language}")
        if pushed_at:
            stats.append(f"Updated at: {pushed_at}")
        lines.append(" | ".join(stats))
        if isinstance(topics, list) and topics:
            normalized_topics = [str(topic).strip() for topic in topics if str(topic).strip()]
            if normalized_topics:
                lines.append(f"Topics: {', '.join(normalized_topics[:8])}")
        return lines

    def _render_file_excerpt(self, excerpt: GitHubFileExcerpt) -> List[str]:
        fence = self._markdown_fence(excerpt.text)
        language = self._language_hint(excerpt.path)
        lines = [f"### {excerpt.path}", f"{fence}{language}", excerpt.text.rstrip(), fence]
        if excerpt.truncated:
            lines.append("[File excerpt truncated]")
        return lines

    def _compact_text(self, text: str, limit: int = 240) -> str:
        normalized = " ".join((text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 3)].rstrip() + "..."

    def _truncate_text(self, text: str, limit: int) -> tuple[str, bool]:
        normalized_text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(normalized_text) <= limit:
            return normalized_text, False

        clipped = normalized_text[: max(0, limit - 3)].rstrip()
        return f"{clipped}...", True

    def _join_and_limit(self, lines: Iterable[str]) -> str:
        text = "\n".join(line for line in lines if line is not None)
        clipped, _ = self._truncate_text(text, limit=self.max_render_chars)
        return clipped

    def _markdown_fence(self, text: str) -> str:
        return "````" if "```" in (text or "") else "```"

    def _language_hint(self, path: str) -> str:
        lowered = (path or "").strip().lower()
        if lowered.endswith(".py"):
            return "python"
        if lowered.endswith(".ts") or lowered.endswith(".tsx"):
            return "ts"
        if lowered.endswith(".js") or lowered.endswith(".jsx"):
            return "javascript"
        if lowered.endswith(".json"):
            return "json"
        if lowered.endswith(".toml"):
            return "toml"
        if lowered.endswith(".yml") or lowered.endswith(".yaml"):
            return "yaml"
        if lowered.endswith(".md"):
            return "markdown"
        if lowered.endswith(".go"):
            return "go"
        if lowered.endswith(".rs"):
            return "rust"
        if lowered.endswith(".java"):
            return "java"
        if lowered.endswith(".sh"):
            return "bash"
        if lowered.endswith(".html"):
            return "html"
        if lowered.endswith(".css") or lowered.endswith(".scss"):
            return "css"
        if lowered.endswith(".xml"):
            return "xml"
        if lowered.endswith("dockerfile"):
            return "dockerfile"
        return ""
