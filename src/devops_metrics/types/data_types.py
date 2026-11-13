"""Data type definitions."""
from typing import Dict, Any, List, TypedDict, Literal, Optional, cast, Protocol, runtime_checkable
from datetime import datetime

RawDataKey = Literal["repositories", "repository_data", "builds", "build_data", "project"]


@runtime_checkable
class ProjectInfo(Protocol):
    """Project information."""
    id: str
    name: str
    description: Optional[str]
    url: str
    state: str
    revision: int
    visibility: str
    last_update_time: str


class RawDataRequired(TypedDict):
    """Required fields for raw data structure."""
    repositories: List[Dict[str, Any]]
    repository_data: Dict[str, Any]
    builds: List[Dict[str, Any]]
    build_data: Dict[str, Any]


class RawData(RawDataRequired, total=False):
    """Raw data structure with optional fields."""
    project: Dict[str, Any]  # Using Dict[str, Any] instead of ProjectInfo for validation


class RawRepositoryInfo(TypedDict):
    """Raw repository information structure from ADO API."""
    id: str
    name: str
    url: str
    defaultBranch: str
    size: int
    remoteUrl: str
    sshUrl: str
    webUrl: str
    isDisabled: bool
    isFork: bool
    visibility: str
    lastCommitId: str
    lastUpdateTime: str # Added for activity filtering


class RepositoryData(TypedDict, total=False):
    """Type definition for repository data"""
    commits: List[Dict[str, Any]]
    branches: List[Dict[str, Any]]
    pull_requests: List[Dict[str, Any]]
    policies: List[Dict[str, Any]]
    project: Dict[str, Any]  # Added project field


class CollectedRepositoryData(TypedDict, total=False):
    """Type definition for collected repository data, including nested API responses."""
    commits: List[Dict[str, Any]]
    branches: List[Dict[str, Any]]
    pull_requests: List[Dict[str, Any]]
    policies: Dict[str, Any] # Changed to Dict[str, Any] to match structure in raw_data_collector
    project: Dict[str, Any] # Added project field


class BuildData(TypedDict):
    """Type definition for build data, including coverage and tests."""
    coverage: Dict[str, Any]
    tests: Dict[str, Any]


class ConsolidatedData(TypedDict):
    """Consolidated data structure from all projects, as used by RawDataCollector."""
    projects: List[Dict[str, Any]]
    repositories: List[Dict[str, Any]] # List of RawRepositoryInfo
    repository_data: Dict[str, CollectedRepositoryData] # Map of repo_id to CollectedRepositoryData
    builds: List[Dict[str, Any]]
    build_data: Dict[str, BuildData] # Map of build_id to BuildData


def validate_project_info(project_info: Dict[str, Any]) -> List[str]:
    """Validate project info structure.
    
    Args:
        project_info: Project info to validate
        
    Returns:
        List of validation errors, empty if valid
    """
    errors: List[str] = []
    
    if not isinstance(project_info, dict):
        errors.append("Project info must be a dictionary")
    else:
        # Check required fields
        required_fields = ["id", "name"]
        for field in required_fields:
            if field not in project_info:
                errors.append(f"Missing required field: {field}")
                
        # Check field types
        if "id" in project_info and not isinstance(project_info["id"], str):
            errors.append("Field 'id' must be a string")
            
        if "name" in project_info and not isinstance(project_info["name"], str):
            errors.append("Field 'name' must be a string")
        
    return errors


def create_project_info(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a ProjectInfo instance from raw data.
    
    Args:
        data: Raw project data
        
    Returns:
        ProjectInfo compatible dictionary
        
    Raises:
        ValueError: If required fields are missing or invalid
    """
    project_info_dict = {
        'id': str(data.get('id', '')),
        'name': str(data.get('name', '')),
        'description': data.get('description'),
        'url': str(data.get('url', '')),
        'state': str(data.get('state', '')),
        'revision': int(data.get('revision', 0)),
        'visibility': str(data.get('visibility', '')),
        'last_update_time': str(data.get('lastUpdateTime', datetime.now().isoformat()))
    }
    
    errors = validate_project_info(project_info_dict)
    if errors:
        raise ValueError(f"Invalid project info: {', '.join(errors)}")
        
    return project_info_dict


def validate_consolidated_data(data: ConsolidatedData) -> List[str]:
    """Validate consolidated data structure.
    
    Args:
        data: Consolidated data to validate
        
    Returns:
        List of validation errors, empty if valid
    """
    errors = []
    
    # Validate projects list
    if not isinstance(data.get('projects'), list):
        errors.append("Missing or invalid 'projects' list")
    
    # Validate repositories list
    if not isinstance(data.get('repositories'), list):
        errors.append("Missing or invalid 'repositories' list")
        
    # Validate repository_data dictionary
    if not isinstance(data.get('repository_data'), dict):
        errors.append("Missing or invalid 'repository_data' dictionary")
        
    # Validate builds list
    if not isinstance(data.get('builds'), list):
        errors.append("Missing or invalid 'builds' list")
        
    # Validate build_data dictionary
    if not isinstance(data.get('build_data'), dict):
        errors.append("Missing or invalid 'build_data' dictionary")
    
    return errors


def merge_project_info(project_infos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge multiple project infos into a single consolidated project info.
    
    Args:
        project_infos: List of project infos to merge
        
    Returns:
        Consolidated ProjectInfo compatible dictionary
        
    Raises:
        ValueError: If no valid project infos provided
    """
    if not project_infos:
        raise ValueError("No project infos provided")
    
    if len(project_infos) == 1:
        return project_infos[0]
    
    # For multiple projects, create a consolidated view
    consolidated_dict = {
        'id': 'consolidated',
        'name': f"Multiple Projects ({len(project_infos)})",
        'description': f"Consolidated view of {len(project_infos)} projects",
        'url': '',  # No single URL for multiple projects
        'state': 'active',
        'revision': max(p['revision'] for p in project_infos),
        'visibility': 'private',  # Default to most restrictive
        'last_update_time': max(p['last_update_time'] for p in project_infos)
    }
    
    return consolidated_dict
