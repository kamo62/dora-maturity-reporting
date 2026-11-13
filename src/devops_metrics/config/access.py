"""Fail closed for explicitly excluded targets, without inspecting their contents."""

import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit


def validate_target(value: str) -> str:
    decoded = unicodedata.normalize("NFKC", unquote(unquote(value))).casefold()
    # Do not join unrelated URL/path components, e.g. '.com/local'.
    for component in re.split(r"[/\\.:?&#=@]+", decoded):
        normalised = re.sub(r"[^a-z0-9]", "", component)
        if any(marker in normalised for marker in ("oml", "omengineering", "oldmutual")):
            raise ValueError("Excluded company target is not permitted")
    return value


def validate_org(value: str) -> str:
    value = validate_target(value.strip())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("Provide an organisation name, not a URL or path")
    return value


def checked_path(value: str | Path) -> Path:
    path = Path(validate_target(str(value)))
    validate_target(str(path.resolve()))
    return path


def validate_database_url(value: str) -> str:
    # Check destinations, not opaque passwords which may coincidentally match.
    from sqlalchemy.engine import make_url

    url = make_url(value)
    for target in (url.host, url.database, *url.query.values()):
        if target:
            validate_target(str(target))
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        checked_path(url.database)
    return value


def validate_source_url(value: str, base_url: str) -> None:
    validate_target(value)
    url = urlsplit(unquote(unquote(value)))
    base = urlsplit(base_url)
    path_parts = url.path.split("/")
    if (
        url.scheme != "https"
        or url.netloc not in {"dev.azure.com", "vsaex.dev.azure.com"}
        or not url.path.startswith(base.path + "/")
        or any(part in {".", ".."} for part in path_parts)
        or "\\" in url.path
    ):
        raise ValueError("Request must remain within the configured Azure DevOps organisation")
