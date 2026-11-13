"""
Azure DevOps API Client Module
"""

import requests # type: ignore
import base64
import pandas as pd
import json
from datetime import datetime
from typing import Dict, List, Any, Optional, cast
import logging
from ...utils.logging import get_logger
from ratelimit import limits, sleep_and_retry # type: ignore
from requests.adapters import HTTPAdapter # type: ignore
from requests.packages.urllib3.util.retry import Retry # type: ignore
from tqdm import tqdm # type: ignore
from pathlib import Path
import asyncio
import aiohttp
from .errors import ADORequestError
from .builds_client import BuildsClient
from .policies_client import PoliciesClient
from ...config.access import validate_org, validate_source_url, validate_target

# Setup logging via central logger utility
logger = get_logger(__name__)

# Disable tqdm output to stderr to prevent mixing with logs
tqdm.monitor_interval = 0

# Rate limiting configuration
CALLS_PER_SECOND = 5
SECONDS_PER_CALL = 1 / CALLS_PER_SECOND


def initialize_headers(settings: Any) -> Dict[str, str]:
    """Initialize HTTP headers for Azure DevOps API calls.

    Only the explicitly configured PAT is used. Ambient pipeline tokens are ignored.
    """
    # Explicit PAT only
    raw_pat = settings.azure_devops_pat.get_secret_value()
    pat_token = (raw_pat or "").strip().strip('"').strip("'")
    if raw_pat != pat_token and raw_pat:
        logger.debug("PAT contained surrounding quotes or whitespace; sanitized before use")
    if not pat_token:
        raise ADORequestError("Set ADO_AZURE_DEVOPS_PAT explicitly before collecting data.")
    encoded_pat = base64.b64encode(f":{pat_token}".encode()).decode()
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Basic {encoded_pat}",
    }


def setup_http_session() -> requests.Session:
    """Configure HTTP session with retry logic"""
    session = requests.Session()
    session.trust_env = False
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


@sleep_and_retry # type: ignore
@limits(calls=CALLS_PER_SECOND, period=SECONDS_PER_CALL) # type: ignore
def make_request(url: str, session: requests.Session) -> Any:
    """Make a rate-limited request to the Azure DevOps API"""
    validate_target(url)
    try:
        logger.debug(f"Making request to: {url}")
        response = session.get(url, allow_redirects=False)
        if 300 <= response.status_code < 400:
            raise ADORequestError("Redirects are not permitted")
        response.raise_for_status()
        data = response.json()
        logger.debug(f"Response status: {response.status_code}")
        return data
    except requests.exceptions.RequestException as e:
        error_msg = f"Error accessing {url}: {str(e)}"
        if response := getattr(e, "response", None):
            if response.status_code == 404:
                # Debug level for 404s as they're often expected
                logger.debug(error_msg)
            else:
                # Warning level for other errors
                logger.warning(error_msg)
        raise ADORequestError(error_msg)


def source_request_trace(base_url: str) -> aiohttp.TraceConfig:
    """Validate every real async request, including those made by subclients."""
    trace = aiohttp.TraceConfig()

    async def check_request(session: Any, context: Any, params: Any) -> None:
        validate_source_url(str(params.url), base_url)

    async def reject_redirect(session: Any, context: Any, params: Any) -> None:
        raise ADORequestError("Redirects are not permitted")

    trace.on_request_start.append(check_request)
    trace.on_request_redirect.append(reject_redirect)
    return trace


class AzureDevOpsClient:
    """Azure DevOps API client wrapper"""

    def __init__(self, settings: Any):
        """Initialize the Azure DevOps client with settings"""
        self.settings = settings
        self.base_url = f"https://dev.azure.com/{validate_org(settings.azure_devops_org)}"
        logger.debug(f"Initializing ADO client with base URL: {self.base_url}")
        self.session = setup_http_session()
        self.headers = initialize_headers(settings)
        # Debug (safe): show whether a PAT is configured without revealing it
        try:
            token_len = len(settings.azure_devops_pat.get_secret_value() or "")
        except Exception:
            token_len = 0
        # Don’t log bearer presence explicitly, just PAT metadata (length only)
        logger.debug(f"ADO org: {settings.azure_devops_org}; PAT configured: {bool(token_len)}; PAT length: {token_len}")
        self.session.headers.update(self.headers)
        # Async HTTP settings: timeout, connector, and concurrency guard
        try:
            timeout_total = float(getattr(self.settings, "http_timeout_total", 60))
        except Exception:
            timeout_total = 60.0
        self._timeout = aiohttp.ClientTimeout(total=timeout_total)
        self._connector_limit = int(getattr(self.settings, "concurrency_expensive", 10) or 10)
        self._connector: Optional[aiohttp.TCPConnector] = None
        self._semaphore_expensive: Optional[asyncio.Semaphore] = asyncio.Semaphore(max(1, self._connector_limit))
        self._aiohttp_session: Optional[aiohttp.ClientSession] = None
        # Lightweight runtime counters for progress heartbeats
        self.stats: Dict[str, int] = {
            "pages": 0,
            "prs": 0,
            "builds": 0,
            "repos": 0,
            "projects": 0,
        }
        # Expose logger on instance for subclients
        self.logger = logger
        # Subclients
        self.builds = BuildsClient(self)
        self.policies = PoliciesClient(self)

    async def __aenter__(self) -> "AzureDevOpsClient":
        """Async context manager entry"""
        await self._get_aiohttp_session()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit"""
        await self.close()

    async def close(self) -> None:
        """Close the aiohttp session"""
        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()
            self._aiohttp_session = None

    async def _get_aiohttp_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session"""
        if self._aiohttp_session is None or self._aiohttp_session.closed:
            if self._connector is None:
                self._connector = aiohttp.TCPConnector(limit=max(1, self._connector_limit))
            self._aiohttp_session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=self._timeout,
                connector=self._connector,
                trace_configs=[source_request_trace(self.base_url)],
                trust_env=False,
            )
        return self._aiohttp_session

    async def get_paginated_results(
        self, url: str, session: Optional[aiohttp.ClientSession] = None, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Get paginated results from Azure DevOps API

        Args:
            url: The API endpoint URL
            session: Optional aiohttp session. If not provided, will use/create internal session.

        Returns:
            List of results from all pages

        Raises:
            ADORequestError: If API request fails
        """
        try:
            validate_source_url(url, self.base_url)
            results: List[Dict[str, Any]] = []
            current_session = session or await self._get_aiohttp_session()
            page_count = 0

            while True:
                async with current_session.get(url, params=params) as response:
                    if response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", "60"))
                        logger.warning(f"Rate limited. Waiting {retry_after} seconds")
                        await asyncio.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    data = await response.json()
                    page_count += 1

                    # Handle different response formats
                    if "value" in data:
                        page_results = data["value"]
                        results.extend(page_results)
                        logger.debug(f"Got {len(page_results)} results from page {page_count}")
                        # Increment global page counter for progress heartbeat
                        try:
                            self.stats["pages"] += 1
                        except Exception:
                            pass
                    else:
                        results.extend([data])
                        logger.debug(f"Got single result from page {page_count}")

                    # Check for continuation token (case-insensitive header)
                    token = response.headers.get("x-ms-continuationtoken") or response.headers.get("continuationToken")
                    if token:
                        params = dict(params or {})
                        params["continuationToken"] = token
                    else:
                        break

            if page_count > 1:
                logger.debug(f"Retrieved {len(results)} total results from {page_count} pages")
            return results

        except aiohttp.ClientError as e:
            error_msg = f"API request failed for {url}: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error during pagination: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e

    async def get_projects(self) -> List[Dict[str, Any]]:
        """Get all projects

        Returns:
            List of project dictionaries

        Raises:
            ADORequestError: If API request fails
        """
        url = f"{self.base_url}/_apis/projects"
        params = {"api-version": "7.1"}

        try:
            session = await self._get_aiohttp_session()
            projs = await self.get_paginated_results(url, session, params)
            try:
                self.stats["projects"] = len(projs)
            except Exception:
                pass
            return projs
        except Exception as e:
            error_msg = f"Failed to get projects: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e

    async def get_security_scan_results(self, project: str, build_id: int) -> List[Dict[str, Any]]:
        return await self.builds.get_security_scan_results(project, build_id)

    async def get_project(self, project_name: str) -> Dict[str, Any]:
        """Get a single project by name

        Args:
            project_name: Project name or ID

        Returns:
            Dictionary containing project information

        Raises:
            ADORequestError: If API request fails
        """
        try:
            session = await self._get_aiohttp_session()

            url = f"{self.base_url}/_apis/projects/{project_name}"
            params = {"api-version": "7.1"}

            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                logger.debug(f"Project response data: {data}")
                return cast(Dict[str, Any], data)

        except aiohttp.ClientError as e:
            error_msg = f"Failed to get project {project_name}: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e
        except Exception as e:
            error_msg = f"Unexpected error getting project: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e

    async def get_repositories(self, project: str) -> List[Dict[str, Any]]:
        """Get all repositories in a project

        Args:
            project: Project name or ID

        Returns:
            List of repository dictionaries

        Raises:
            ADORequestError: If API request fails
        """
        url = f"{self.base_url}/{project}/_apis/git/repositories"
        params = {
            "api-version": "7.1",
            "includeAllUrls": "true",  # Include all URLs
            "includeLinks": "true",    # Include reference links 
            "includeHidden": "true"    # Include hidden repositories
        }

        try:
            logger.debug(f"Getting repositories for project: {project}")
            session = await self._get_aiohttp_session()
            repos = await self.get_paginated_results(url, session, params)
            
            # Log a sample repository to examine its fields
            if repos and len(repos) > 0:
                logger.debug(f"Repository example fields: {list(repos[0].keys())}")
            try:
                self.stats["repos"] += len(repos)
            except Exception:
                pass
            return repos
        except Exception as e:
            error_msg = f"Failed to get repositories for project {project}: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e

    async def get_commits(
        self, repository_id: str, project_name: str, from_date: Optional[str] = None, to_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get commits for a repository

        Args:
            repository_id: Repository ID
            project_name: Project name or ID
            from_date: Optional start date for commit history
            to_date: Optional end date for commit history

        Returns:
            List of commit dictionaries

        Raises:
            ADORequestError: If API request fails
        """
        url = f"{self.base_url}/{project_name}/_apis/git/repositories/{repository_id}/commits"
        params = {"api-version": "7.1"}

        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date

        try:
            session = await self._get_aiohttp_session()
            return await self.get_paginated_results(url, session, params)
        except Exception as e:
            error_msg = f"Failed to get commits for repository {repository_id}: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e

    async def get_branches(self, repository_id: str, project_name: str) -> List[Dict[str, Any]]:
        """Get branches for a repository

        Args:
            repository_id: Repository ID
            project_name: Project name or ID

        Returns:
            List of branch dictionaries

        Raises:
            ADORequestError: If API request fails
        """
        url = f"{self.base_url}/{project_name}/_apis/git/repositories/{repository_id}/refs"
        params = {"api-version": "7.1"}

        try:
            session = await self._get_aiohttp_session()
            return await self.get_paginated_results(url, session, params)
        except Exception as e:
            error_msg = f"Failed to get branches for repository {repository_id}: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e

    async def get_branch_policies(self, project_name: str, repository_id: str) -> List[Dict[str, Any]]:
        """Get branch policies for a repository

        Args:
            project_name: Project name or ID
            repository_id: Repository ID

        Returns:
            List of policy configuration dictionaries

        Raises:
            ADORequestError: If API request fails
        """
        url = f"{self.base_url}/{project_name}/_apis/policy/configurations"
        params = {"api-version": "7.1", "repositoryId": repository_id}

        try:
            session = await self._get_aiohttp_session()
            return await self.get_paginated_results(url, session, params)
        except Exception as e:
            error_msg = f"Failed to get branch policies for repository {repository_id}: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e

    def _get_repository_id(self, repo: Any) -> str:
        """Extract repository ID from repository object"""
        if isinstance(repo, dict):
            rid = repo.get("id")
            if rid is None:
                raise ValueError("Repository object missing 'id'")
            return str(rid)
        return str(repo)

    async def get_pull_requests(
        self, project: str, repository_id: str, min_time: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get pull requests for a repository

        Args:
            project: Project name or ID
            repository_id: Repository ID
            min_time: Optional ISO format date to filter pull requests from

        Returns:
            List of pull request dictionaries with detailed information

        Raises:
            ADORequestError: If API request fails
        """
        try:
            session = await self._get_aiohttp_session()

            # Get base pull request list
            url = f"{self.base_url}/{project}/_apis/git/repositories/{repository_id}/pullrequests"
            params: Dict[str, Any] = { # Explicitly type params
                "api-version": "7.1",
                "searchCriteria.status": "all",
                "searchCriteria.includeLinks": "true",
                "$top": 1000,
                "$skip": 0,
                "$orderby": "creationDate desc"
            }
            
            # Add time-based filtering if min_time is provided
            if min_time:
                params["searchCriteria.minTime"] = min_time
                params["searchCriteria.queryTimeRangeType"] = "created"
                logger.info(f"Filtering pull requests created after {min_time}")

            logger.info(f"Fetching pull requests with params: {params}")
            
            # Get all pages of results
            all_prs: List[Dict[str, Any]] = []
            while True:
                async with session.get(url, params=params) as response:
                    response.raise_for_status()
                    data = await response.json()
                    
                    if "value" in data:
                        page_results = data["value"]
                        all_prs.extend(page_results)
                        # Ensure params['$skip'] is int before arithmetic
                        current_skip = int(params.get("$skip", 0))
                        logger.debug(f"Got {len(page_results)} results from page {current_skip // 1000 + 1}")
                        
                        # Check if we need to get more pages
                        if len(page_results) == 1000:  # We might have more pages
                            params["$skip"] = current_skip + 1000
                        else:
                            break
                    else:
                        break

            logger.info(f"Found {len(all_prs)} total pull requests")
            try:
                self.stats["prs"] += len(all_prs)
            except Exception:
                pass

            # Get details for each PR with limited concurrency
            detailed_prs: List[Dict[str, Any]] = []

            async def _fetch_pr_details(pr: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                async with self._semaphore_expensive:
                    try:
                        pr_id = pr.get("pullRequestId")
                        if not pr_id:
                            logger.warning(f"PR missing pullRequestId: {pr.get('title', 'unknown')} in {project}/{repository_id}")
                            return None
                        logger.debug(f"Processing PR {pr_id} for {project}/{repository_id}")

                        # Get PR details including reviewers
                        details_url = f"{self.base_url}/{project}/_apis/git/repositories/{repository_id}/pullrequests/{pr_id}"
                        details_params = {
                            "api-version": "7.1",
                            "includeWorkItemRefs": "true",
                            "$expand": "reviewers,commits,changes"
                        }

                        async with session.get(details_url, params=details_params) as response:
                            response.raise_for_status()
                            pr_details = await response.json()

                        # Get PR commits
                        commits_url = f"{details_url}/commits"
                        try:
                            pr_details["commits"] = await self.get_paginated_results(
                                commits_url, session, {"api-version": "7.1"}
                            )
                        except ADORequestError:
                            pr_details["commits"] = []
                            logger.debug(f"No commits found for PR {pr_id}")

                        # Get PR threads (comments and code review feedback)
                        threads_url = f"{details_url}/threads"
                        try:
                            threads = await self.get_paginated_results(
                                threads_url, session, {"api-version": "7.1"}
                            )
                            pr_details["threads"] = threads

                            # Update reviewer vote dates from thread comments
                            reviewers = pr_details.get("reviewers", [])
                            for thread in threads:
                                for comment in thread.get("comments", []):
                                    if comment.get("commentType") == "system" and "voted" in str(comment.get("content", "")).lower():
                                        for reviewer in reviewers:
                                            if reviewer.get("id") and reviewer["id"] in str(comment.get("content", "")):
                                                reviewer["voteDate"] = comment.get("publishedDate")
                                                break
                        except ADORequestError:
                            pr_details["threads"] = []
                            logger.debug(f"No threads found for PR {pr_id}")
                        return pr_details
                    except Exception as e:
                        logger.error(
                            f"Unexpected error processing PR {pr.get('pullRequestId', 'unknown')}: {e}"
                        )
                        return None

            if self._semaphore_expensive is None:
                self._semaphore_expensive = asyncio.Semaphore(max(1, self._connector_limit))
            tasks = [_fetch_pr_details(pr) for pr in all_prs]
            results = await asyncio.gather(*tasks, return_exceptions=False)
            for res in results:
                if isinstance(res, dict):
                    detailed_prs.append(res)
            return detailed_prs

        except ADORequestError as e:
            error_msg = f"Failed to get pull requests for {project}/{repository_id}: {e}"
            logger.error(error_msg)
            raise
        except Exception as e:
            error_msg = f"Unexpected error getting pull requests: {str(e)}"
            logger.error(error_msg)
            raise ADORequestError(error_msg) from e

    async def get_builds(self, project: str, project_name: Optional[str] = None, from_date: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.builds.get_builds(project, project_name, from_date)

    async def get_code_coverage(self, project: str, build_id: int, flags: int = 7) -> Dict[str, Any]:
        return await self.builds.get_code_coverage(project, build_id, flags)

    async def process_data_to_excel(self, excel_data: Dict[str, Any], all_data: Dict[str, Any], timestamp: str) -> None:
        """Process data and export it to Excel and JSON files"""
        # Create exports directory with date subdirectory
        exports_dir = Path("exports") / datetime.now().strftime("%Y-%m-%d")
        exports_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Exporting data to files...")

        # Export to Excel
        try:
            excel_filename = exports_dir / f"ADO_Detailed_Report_{timestamp}.xlsx"
            with pd.ExcelWriter(excel_filename) as writer:
                for sheet_name, data in excel_data.items():
                    if data:  # Only create sheet if we have data
                        logger.info(f"Writing {sheet_name} sheet with {len(data)} rows")
                        df = pd.DataFrame(data)
                        df.to_excel(writer, sheet_name=sheet_name, index=False)
            logger.info(f"Data exported to {excel_filename}")
        except Exception as e:
            logger.error(f"Failed to export Excel file: {e}")

        # Export raw data to separate JSON files
        try:
            for data_type, data in all_data.items():
                if data:  # Only create file if we have data
                    json_filename = exports_dir / f"ADO_{data_type}_{timestamp}.json"
                    logger.info(f"Writing {data_type} JSON with {len(data)} items")
                    with open(json_filename, "w") as f:
                        json.dump(data, f, indent=2, default=str)
                    logger.info(f"{data_type.capitalize()} data exported to {json_filename}")
        except Exception as e:
            logger.error(f"Failed to export JSON files: {e}")

    async def get_test_results(self, project: str, build_id: int) -> Dict[str, Any]:
        return await self.builds.get_test_results(project, build_id)

    async def get_build_code_coverage(self, project: str, build_id: int) -> Dict[str, Any]:
        try:
            # Preserve behavior: delegate through self.get_code_coverage so monkeypatching works in tests
            return await self.get_code_coverage(project, build_id)
        except Exception as e:
            raise ADORequestError(f"Unexpected error getting build code coverage: {e}") from e

    async def get_builds_with_coverage(self, project: str, repository_id: Optional[str] = None, top: Optional[int] = None, **kwargs: Any) -> List[Dict[str, Any]]:
        return await self.builds.get_builds_with_coverage(project, repository_id, top, **kwargs)

    async def get(self, url: str) -> Dict[str, Any]:
        """Make a GET request to the Azure DevOps API"""
        validate_source_url(url, self.base_url)
        try:
            session = await self._get_aiohttp_session()
            async with session.get(url) as response:
                if response.status == 404:
                    logger.debug(f"Resource not found: {url}")
                    return {"value": []}

                response.raise_for_status()
                return cast(Dict[str, Any], await response.json())

        except aiohttp.ClientResponseError as e:
            logger.error(f"HTTP error {e.status} for {url}: {e.message}")
            if e.status == 404:
                return {"value": []}
            raise
        except aiohttp.ClientError as e:
            logger.error(f"Request failed for {url}: {str(e)}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON response from {url}: {str(e)}")
            return {"value": []}
        except Exception as e:
            logger.error(f"Unexpected error for {url}: {str(e)}")
            raise

    async def get_policy_configurations(self, project: str) -> List[Dict[str, Any]]:
        return await self.policies.get_policy_configurations(project)

    async def get_policy_types(self, project: str) -> List[Dict[str, Any]]:
        return await self.policies.get_policy_types(project)
