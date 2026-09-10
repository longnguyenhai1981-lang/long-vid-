from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class RemoteUpdateError(RuntimeError):
    """Base error for development update-channel operations."""


class UpdateChannelNotConfiguredError(RemoteUpdateError):
    pass


class UpdateChannelRequestError(RemoteUpdateError):
    pass


class UpdateDownloadError(RemoteUpdateError):
    pass


@dataclass(frozen=True)
class RemoteUpdateInfo:
    repository: str
    branch: str
    revision: str
    short_revision: str
    commit_url: str
    committed_at: str | None
    checked_at: str
    available: bool
    current_revision: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "branch": self.branch,
            "revision": self.revision,
            "short_revision": self.short_revision,
            "commit_url": self.commit_url,
            "committed_at": self.committed_at,
            "checked_at": self.checked_at,
            "available": self.available,
            "current_revision": self.current_revision,
        }


class GitHubDevUpdateChannel:
    """Development update channel backed by one explicit GitHub branch."""

    API_ROOT = "https://api.github.com"
    DEFAULT_TIMEOUT_SECONDS = 20.0
    MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024

    def __init__(
        self,
        repository: str | None = None,
        branch: str | None = None,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self._config_path = Path(config_path).resolve() if config_path else None
        stored: dict[str, str] = {}
        if self._config_path and self._config_path.is_file():
            try:
                import json
                raw = json.loads(self._config_path.read_text(encoding="utf-8"))
                stored = {
                    "repository": str(raw.get("repository") or ""),
                    "branch": str(raw.get("branch") or "main"),
                }
            except (OSError, ValueError, TypeError):
                stored = {}
        self.repository = (repository or os.environ.get("MOTILY_UPDATE_REPOSITORY") or stored.get("repository") or "").strip()
        self.branch = (branch or os.environ.get("MOTILY_UPDATE_BRANCH") or stored.get("branch") or "main").strip()
        self._token = token if token is not None else os.environ.get("MOTILY_GITHUB_TOKEN")
        self._client = client
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        if not self.repository:
            return
        if not _REPOSITORY_RE.fullmatch(self.repository):
            raise UpdateChannelNotConfiguredError("Update repository must be in 'owner/repository' form")
        if not self.branch or not _BRANCH_RE.fullmatch(self.branch) or ".." in self.branch.split("/"):
            raise UpdateChannelNotConfiguredError("Update branch is invalid")

    @property
    def configured(self) -> bool:
        return bool(self.repository)

    def configure(self, repository: str, branch: str = "main") -> dict[str, Any]:
        previous_repository, previous_branch = self.repository, self.branch
        self.repository = repository.strip()
        self.branch = branch.strip() or "main"
        try:
            self._validate_configuration()
        except Exception:
            self.repository, self.branch = previous_repository, previous_branch
            raise
        if self._config_path is not None:
            import json
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._config_path.with_suffix(".tmp")
            temp.write_text(json.dumps({"repository": self.repository, "branch": self.branch}, indent=2, sort_keys=True), encoding="utf-8")
            temp.replace(self._config_path)
        return self.status()

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "provider": "github-branch",
            "repository": self.repository or None,
            "branch": self.branch if self.configured else None,
            "private_access_configured": bool(self._token),
        }

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "motily-studio-dev-updater",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _owned_client(self) -> tuple[httpx.Client, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.Client(timeout=self.DEFAULT_TIMEOUT_SECONDS, follow_redirects=True, headers=self._headers()), True

    def _require_configured(self) -> None:
        if not self.configured:
            raise UpdateChannelNotConfiguredError("Online updates are not configured. Set MOTILY_UPDATE_REPOSITORY=owner/repository.")

    def check(self, *, current_revision: str | None = None) -> RemoteUpdateInfo:
        self._require_configured()
        client, owned = self._owned_client()
        try:
            try:
                response = client.get(f"{self.API_ROOT}/repos/{self.repository}/commits/{self.branch}", headers=self._headers())
            except httpx.HTTPError as exc:
                raise UpdateChannelRequestError(f"Could not reach the GitHub update channel: {type(exc).__name__}") from exc
            if response.status_code in {401, 403}:
                raise UpdateChannelRequestError("GitHub update access was denied. Check MOTILY_GITHUB_TOKEN permissions.")
            if response.status_code == 404:
                raise UpdateChannelRequestError("Configured GitHub update repository or branch was not found.")
            try:
                response.raise_for_status()
                payload = response.json()
                revision = str(payload["sha"])
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                raise UpdateChannelRequestError("GitHub returned an invalid update-channel response") from exc
            committed_at = None
            try:
                committed_at = str(payload["commit"]["committer"]["date"])
            except (KeyError, TypeError):
                pass
            commit_url = str(payload.get("html_url") or "")
            normalized_current = current_revision.lower() if current_revision else None
            return RemoteUpdateInfo(
                repository=self.repository,
                branch=self.branch,
                revision=revision,
                short_revision=revision[:12],
                commit_url=commit_url,
                committed_at=committed_at,
                checked_at=datetime.now(timezone.utc).isoformat(),
                available=normalized_current != revision.lower(),
                current_revision=current_revision,
            )
        finally:
            if owned:
                client.close()

    def download(self, revision: str, destination: Path | str | None = None) -> Path:
        self._require_configured()
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", revision or ""):
            raise UpdateDownloadError("Remote update revision is invalid")
        if destination is None:
            fd, name = tempfile.mkstemp(prefix="motily_remote_update_", suffix=".zip")
            os.close(fd)
            target = Path(name)
        else:
            target = Path(destination).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
        client, owned = self._owned_client()
        written = 0
        try:
            try:
                with client.stream("GET", f"{self.API_ROOT}/repos/{self.repository}/zipball/{revision}", headers=self._headers(), follow_redirects=True) as response:
                    if response.status_code in {401, 403}:
                        raise UpdateDownloadError("GitHub update download was denied. Check MOTILY_GITHUB_TOKEN permissions.")
                    if response.status_code == 404:
                        raise UpdateDownloadError("The selected GitHub update revision is no longer available")
                    response.raise_for_status()
                    with target.open("wb") as fh:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            written += len(chunk)
                            if written > self.MAX_DOWNLOAD_BYTES:
                                raise UpdateDownloadError("Remote development update exceeds the 512 MiB download limit")
                            fh.write(chunk)
            except UpdateDownloadError:
                raise
            except httpx.HTTPError as exc:
                raise UpdateDownloadError(f"Could not download the GitHub update: {type(exc).__name__}") from exc
            if written == 0:
                raise UpdateDownloadError("Downloaded development update is empty")
            return target
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            if owned:
                client.close()


class PublicManifestUpdateChannel:
    """Public no-token update channel backed by a small JSON manifest."""

    RAW_ROOT = "https://raw.githubusercontent.com"
    DEFAULT_TIMEOUT_SECONDS = 20.0
    MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024

    def __init__(
        self,
        repository: str = "longnguyenhai1981-lang/long-vid-",
        branch: str = "main",
        *,
        manifest_path: str = "updates/dev/latest.json",
        client: httpx.Client | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self.repository = repository.strip()
        self.branch = branch.strip() or "main"
        self.manifest_path = manifest_path.strip().lstrip("/")
        self._client = client
        self._config_path = Path(config_path).resolve() if config_path else None
        if self._config_path and self._config_path.is_file():
            try:
                import json
                raw = json.loads(self._config_path.read_text(encoding="utf-8"))
                self.repository = str(raw.get("repository") or self.repository).strip()
                self.branch = str(raw.get("branch") or self.branch).strip()
            except (OSError, ValueError, TypeError):
                pass
        self._validate_configuration()
        self._last_manifest: dict[str, Any] | None = None

    def _validate_configuration(self) -> None:
        if not _REPOSITORY_RE.fullmatch(self.repository):
            raise UpdateChannelNotConfiguredError("Update repository must be in 'owner/repository' form")
        if not self.branch or not _BRANCH_RE.fullmatch(self.branch) or ".." in self.branch.split("/"):
            raise UpdateChannelNotConfiguredError("Update branch is invalid")
        if not self.manifest_path or ".." in Path(self.manifest_path).parts:
            raise UpdateChannelNotConfiguredError("Update manifest path is invalid")

    @property
    def configured(self) -> bool:
        return True

    def configure(self, repository: str, branch: str = "main") -> dict[str, Any]:
        old_repo, old_branch = self.repository, self.branch
        self.repository, self.branch = repository.strip(), branch.strip() or "main"
        try:
            self._validate_configuration()
        except Exception:
            self.repository, self.branch = old_repo, old_branch
            raise
        if self._config_path is not None:
            import json
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._config_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"repository": self.repository, "branch": self.branch}, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self._config_path)
        return self.status()

    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "provider": "github-public-manifest",
            "repository": self.repository,
            "branch": self.branch,
            "manifest_path": self.manifest_path,
            "private_access_configured": False,
        }

    def _owned_client(self) -> tuple[httpx.Client, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.Client(timeout=self.DEFAULT_TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": "motily-studio-public-updater"}), True

    @property
    def manifest_url(self) -> str:
        return f"{self.RAW_ROOT}/{self.repository}/{self.branch}/{self.manifest_path}"

    def _fetch_manifest(self) -> dict[str, Any]:
        client, owned = self._owned_client()
        try:
            try:
                response = client.get(self.manifest_url)
            except httpx.HTTPError as exc:
                raise UpdateChannelRequestError(f"Could not reach the public update channel: {type(exc).__name__}") from exc
            if response.status_code == 404:
                raise UpdateChannelRequestError("Public update manifest was not found")
            try:
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise UpdateChannelRequestError("Public update manifest is invalid") from exc
            required = {"revision", "download_url", "sha256"}
            if not isinstance(payload, dict) or not required.issubset(payload):
                raise UpdateChannelRequestError("Public update manifest is missing required fields")
            revision = str(payload["revision"]).strip().lower()
            sha256 = str(payload["sha256"]).strip().lower()
            url = str(payload["download_url"]).strip()
            if not re.fullmatch(r"[0-9a-f]{12,64}", revision):
                raise UpdateChannelRequestError("Public update manifest revision is invalid")
            if not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise UpdateChannelRequestError("Public update manifest SHA-256 is invalid")
            prefix = f"https://raw.githubusercontent.com/{self.repository}/"
            if not url.startswith(prefix):
                raise UpdateChannelRequestError("Public update download URL is outside the configured repository")
            self._last_manifest = payload
            return payload
        finally:
            if owned:
                client.close()

    def check(self, *, current_revision: str | None = None) -> RemoteUpdateInfo:
        payload = self._fetch_manifest()
        revision = str(payload["revision"]).lower()
        current = current_revision.lower() if current_revision else None
        return RemoteUpdateInfo(
            repository=self.repository,
            branch=self.branch,
            revision=revision,
            short_revision=revision[:12],
            commit_url=str(payload.get("release_url") or f"https://github.com/{self.repository}"),
            committed_at=str(payload.get("published_at")) if payload.get("published_at") else None,
            checked_at=datetime.now(timezone.utc).isoformat(),
            available=current != revision,
            current_revision=current_revision,
        )

    def download(self, revision: str, destination: Path | str | None = None) -> Path:
        payload = self._last_manifest if self._last_manifest and str(self._last_manifest.get("revision", "")).lower() == revision.lower() else self._fetch_manifest()
        if str(payload["revision"]).lower() != revision.lower():
            raise UpdateDownloadError("The public update revision changed; check for updates again")
        expected_sha = str(payload["sha256"]).lower()
        url = str(payload["download_url"])
        if destination is None:
            fd, name = tempfile.mkstemp(prefix="motily_public_update_", suffix=".zip")
            os.close(fd)
            target = Path(name)
        else:
            target = Path(destination).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
        client, owned = self._owned_client()
        written = 0
        try:
            import hashlib
            digest = hashlib.sha256()
            try:
                with client.stream("GET", url, follow_redirects=True) as response:
                    if response.status_code == 404:
                        raise UpdateDownloadError("Public update package was not found")
                    response.raise_for_status()
                    with target.open("wb") as fh:
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            written += len(chunk)
                            if written > self.MAX_DOWNLOAD_BYTES:
                                raise UpdateDownloadError("Remote development update exceeds the 512 MiB download limit")
                            digest.update(chunk)
                            fh.write(chunk)
            except UpdateDownloadError:
                raise
            except httpx.HTTPError as exc:
                raise UpdateDownloadError(f"Could not download the public update: {type(exc).__name__}") from exc
            if written == 0:
                raise UpdateDownloadError("Downloaded development update is empty")
            if digest.hexdigest().lower() != expected_sha:
                raise UpdateDownloadError("Downloaded update failed SHA-256 verification")
            return target
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            if owned:
                client.close()
