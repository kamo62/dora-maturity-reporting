"""Policies-related API client extracted from AzureDevOpsClient."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import quote

from .errors import ADORequestError

if TYPE_CHECKING:
    from .ado_client import AzureDevOpsClient


class PoliciesClient:
    """Subset client to fetch policy configurations and types."""

    def __init__(self, parent: "AzureDevOpsClient") -> None:
        self._parent = parent

    async def get_policy_configurations(self, project: str) -> List[Dict[str, Any]]:
        try:
            url = f"{self._parent.base_url}/{quote(project, safe='')}/_apis/policy/configurations"
            params: Dict[str, Any] = {"api-version": "7.1"}
            session = await self._parent._get_aiohttp_session()
            return await self._parent.get_paginated_results(url, session, params)
        except Exception as e:
            raise ADORequestError(f"Failed to get policy configurations for {project}: {e}") from e

    async def get_policy_types(self, project: str) -> List[Dict[str, Any]]:
        try:
            url = f"{self._parent.base_url}/{quote(project, safe='')}/_apis/policy/types"
            params: Dict[str, Any] = {"api-version": "7.1"}
            session = await self._parent._get_aiohttp_session()
            return await self._parent.get_paginated_results(url, session, params)
        except Exception as e:
            raise ADORequestError(f"Failed to get policy types for {project}: {e}") from e

