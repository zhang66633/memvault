"""Multi-agent / multi-session scope resolution.

One shared store, logically isolated by three scope fields (no need to read
chat transcripts or split databases):

- ``user_id``   a real person (shared across projects; e.g. OS login name)
- ``agent_id``    one coding agent / project role (e.g. project path slug)
- ``run_id``      one session / task execution (optional, env-injected)

Resolution order per field:

1. explicit argument (tool / API call);
2. override env var: ``MEMVAULT_DEFAULT_USER_ID`` /
   ``MEMVAULT_DEFAULT_AGENT_ID`` / ``MEMVAULT_DEFAULT_RUN_ID``;
3. automatic default:
   - user  -> current OS login user (disable with
     ``MEMVAULT_SCOPE_USER_FROM_OS=0``);
   - agent -> project slug from ``MEMVAULT_PROJECT_DIR`` ->
     ``CLAUDE_PROJECT_DIR`` (Claude Code injects this automatically) ->
     server process cwd (disable via
     ``MEMVAULT_SCOPE_AGENT_FROM_PROJECT=0``);
   - run   -> no automatic default (sessions need an explicit run id).
"""
from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass
from typing import Optional

# agent_id must be a stable, filename/URL-safe short token.
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def project_slug(path: Optional[str]) -> Optional[str]:
    """Turn an absolute project path into a stable short agent slug.

    ``D:\\Claude_code\\memory`` -> ``claude-code-memory``
    ``/home/alice/my-proj``   -> ``my-proj``

    A bare home/root folder itself (``/``, ``/home``, ``/home/alice``,
    ``/Users/alice``, ``C:\\Users\\alice``) is not a project -> None.
    """
    if not path:
        return None
    _, tail = os.path.splitdrive(os.path.normpath(path))
    raw = [s for s in re.split(r"[\\/]+", tail) if s and s != "/"]
    if not raw:
        return None
    low = [s.lower() for s in raw]
    # Drop a leading home prefix (home/alice or Users/alice): a deeper
    # path segment is the actual project.
    if low[0] in ("home", "users"):
        if len(low) <= 2:
            return None
        raw = raw[2:]
    if not raw:
        return None
    slug = _SLUG_STRIP_RE.sub("-", "-".join(s.lower() for s in raw)).strip("-")
    return slug or None


@dataclass(frozen=True)
class ResolvedScope:
    """Result of resolving a scope triple, with diagnostics for `whoami`."""

    user_id: Optional[str]
    agent_id: Optional[str]
    run_id: Optional[str]
    os_user: Optional[str]
    cwd: Optional[str]
    project_dir: Optional[str]

    def as_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "os_user": self.os_user,
            "cwd": self.cwd,
            "project_dir": self.project_dir,
        }


class ScopeResolver:
    """Resolve implicit user/agent/run scopes from the process environment."""

    def __init__(
        self,
        env_user_id: str = "",
        env_agent_id: str = "",
        env_run_id: str = "",
        use_os_user: bool = True,
        use_project_agent: bool = True,
        os_user: Optional[str] = None,
        cwd: Optional[str] = None,
        project_dir: Optional[str] = None,
    ) -> None:
        self.env_user_id = env_user_id or None
        self.env_agent_id = env_agent_id or None
        self.env_run_id = env_run_id or None
        self.use_os_user = use_os_user
        self.use_project_agent = use_project_agent
        self.os_user = os_user if os_user is not None else _safe_getuser()
        self.cwd = cwd if cwd is not None else _safe_getcwd()
        self.project_dir = project_dir

    @classmethod
    def from_config(cls, config) -> "ScopeResolver":
        env = os.environ
        truthy = {"1", "true", "yes", "on"}
        project_dir = (
            env.get("MEMVAULT_PROJECT_DIR")
            or env.get("CLAUDE_PROJECT_DIR")
            or None
        )
        return cls(
            env_user_id=getattr(config, "default_user_id", "") or "",
            env_agent_id=getattr(config, "default_agent_id", "") or "",
            env_run_id=getattr(config, "default_run_id", "") or "",
            use_os_user=(
                os.environ.get("MEMVAULT_SCOPE_USER_FROM_OS", "1").lower()
                in truthy
            ),
            use_project_agent=(
                os.environ.get("MEMVAULT_SCOPE_AGENT_FROM_PROJECT", "1").lower()
                in truthy
            ),
            project_dir=project_dir,
        )

    def resolve(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (user_id, agent_id, run_id), filling defaults left to right."""
        user = user_id or self.env_user_id
        if user is None and self.use_os_user:
            user = self.os_user

        agent = agent_id or self.env_agent_id
        if agent is None and self.use_project_agent:
            project = self.project_dir or self.cwd
            agent = project_slug(project)

        run = run_id or self.env_run_id
        return user, agent, run

    def describe(self) -> ResolvedScope:
        user, agent, run = self.resolve()
        return ResolvedScope(
            user_id=user,
            agent_id=agent,
            run_id=run,
            os_user=self.os_user,
            cwd=self.cwd,
            project_dir=self.project_dir,
        )


def _safe_getuser() -> Optional[str]:
    try:
        return getpass.getuser()
    except Exception:
        return None


def _safe_getcwd() -> Optional[str]:
    try:
        return os.getcwd()
    except Exception:
        return None
