from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath


class SvnAction(Enum):
    ADD = 'A'
    COPY = 'C'
    MODIFY = 'M'
    DELETE = 'D'
    REPLACE = 'R'
    UNKNOWN = '?'

    @classmethod
    def fromString(cls, s: str):
        match s.upper():
            case 'A': return cls.ADD
            case 'C': return cls.COPY
            case 'M', 'R': return cls.MODIFY
            case 'D': return cls.DELETE
        return cls.UNKNOWN

    def __str__(self) -> str:
        return self.value  # for nice output


class SvnNodeKind(Enum):
    FILE = "file"
    DIR = "dir"
    UNKNOWN = "?"

    @classmethod
    def fromString(cls, s: str):
        if s.lower() in ["file", "dir", "?"]:
            return cls(s.lower())
        else:
            return cls.UNKNOWN

    def __str__(self) -> str:
        return self.value  # for nice output


@dataclass
class PathChange:
    path: PurePath
    action: SvnAction
    kind: SvnNodeKind
