"""Test configuration using synthetic credentials and mocked service data.

Sets up import path and minimal environment expected by the app at startup.
"""

import os, sys

# Ensure src on PYTHONPATH for test imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

# Provide minimal required env for Settings() used by API startup hook
os.environ.setdefault("ADO_AZURE_DEVOPS_PAT", "test-pat")
os.environ.setdefault("ADO_AZURE_DEVOPS_ORG", "test-org")
# Ensure no DB is attempted during tests unless explicitly configured
os.environ.setdefault("DATABASE_URL", "")

# Monkey-patch RepositoryData alias so raw_data_collector can import it
try:
    import devops_metrics.types.data_types as dt
    dt.RepositoryData = dt.CollectedRepositoryData
except ImportError:
    pass
