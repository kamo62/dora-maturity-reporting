from typing import List, TypedDict


class ValidationIssue(TypedDict):
    code: str
    message: str
    path: List[str]


class ValidationReport(TypedDict):
    ok: bool
    issues: List[ValidationIssue]
