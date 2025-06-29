# src/twat_text/__init__.py
try:
    from .__version__ import __version__
except ImportError:
    # This happens if the package is not installed and __version__.py is not generated
    # or if accessing from a raw source checkout before any build process.
    # hatch-vcs generates __version__.py during the build.
    # For local development without a build, or if __version__.py is gitignored:
    __version__ = "0.0.0+unknown" # Placeholder

# Expose main functionalities from twat_text.py at the package level
from .twat_text import Config, process_data, main

__all__ = ["__version__", "Config", "process_data", "main"]
