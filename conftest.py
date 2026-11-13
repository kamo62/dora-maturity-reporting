"""
This file is used to configure pytest and ensure proper module imports.
"""
import os
import sys

# Add the project root and src directories to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

# Try to create a special path structure to help with imports
site_packages_dir = os.path.join(os.path.dirname(__file__), "src_site_packages")
if os.path.exists(site_packages_dir):
    sys.path.insert(0, site_packages_dir) 