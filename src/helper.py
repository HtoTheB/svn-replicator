from pathlib import Path

def ensure_slashes(s: str) -> str:
    if not s.startswith('/'):
        s = '/' + s
    if not s.endswith('/'):
        s = s + '/'
    return s
 

def substrUntilFirstOccurance(s: str, pattern: str, includePattern=False) -> str:
    x = s.find(pattern)
    if x == -1:
        return s
    else:
        return s[:x + includePattern]


def findFirstFolderAfterPrefix(s: str, prefix: str) -> str:
    s = s.strip("/")
    prefix = prefix.strip("/")

    if not s.startswith(prefix):
        raise ValueError(f"Path {s} doesnt start with {prefix}")

    s.removeprefix(prefix)
    s.removeprefix("/")
    return substrUntilFirstOccurance(s, "/")


def getSubstringAfterPrefix(s: str, prefix: str) -> str:
    # Find the first / after the prefix
    end = s.find("/", len(prefix))
    # If end <0 there is no other /
    return s + "/" if end < 0 else s[: end + 1]




def _append(self: Path, s: Path) -> Path:
    if s.is_absolute():
        s = s.relative_to(s.anchor)
    return self / s

# Monkey-Patch
Path.append = _append  # type: ignore[attr-defined]