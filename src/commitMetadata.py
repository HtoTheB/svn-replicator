from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Sequence

from git import Repo, Actor
from git.objects import Commit


@dataclass
class CommitMetadata:
    """
    Alle Metadaten, die wir für einen Commit explizit setzen wollen.
    """

    message: str

    # Autor (SVN-Committer)
    author_name: str
    author_email: str

    # Committer (kann vom Autor abweichen; default = Autor)
    committer_name: Optional[str] = None
    committer_email: Optional[str] = None

    # Zeitstempel
    author_date: Optional[datetime] = None
    commit_date: Optional[datetime] = None

    # Eltern-Commits
    parent_commits: Optional[Sequence[Commit]] = None

    # HEAD-Update?
    update_head: bool = True

    # -------------------------------------------------------------
    # Hilfsfunktionen
    # -------------------------------------------------------------

    @staticmethod
    def to_git_date(dt: datetime) -> str:
        """
        Wandelt ein datetime in das von Git erwartete Datumsformat um:
        'unix_timestamp ±HHMM'.

        Naive Datetimes werden als UTC interpretiert.
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        ts = int(dt.timestamp())
        offset = dt.utcoffset() or timedelta(0)
        total_minutes = int(offset.total_seconds() // 60)

        sign = "+" if total_minutes >= 0 else "-"
        total_minutes = abs(total_minutes)
        hh, mm = divmod(total_minutes, 60)

        return f"{ts} {sign}{hh:02d}{mm:02d}"

    @classmethod
    def from_svn(
        cls,
        *,
        message: str,
        svn_author: str,
        svn_author_email: str,
        svn_date: datetime,
    ) -> "CommitMetadata":
        """
        Convenience-Factory, um direkt aus SVN-Daten eine CommitMetadata
        zu erzeugen. Du kannst später weitere Felder ergänzen.
        """
        return cls(
            message=message,
            author_name=svn_author,
            author_email=svn_author_email,
            committer_name=svn_author,
            committer_email=svn_author_email,
            author_date=svn_date,
            commit_date=svn_date,
        )
