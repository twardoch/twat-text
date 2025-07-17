#!/usr/bin/env python3
"""Cross-platform build script for twat-text project.

This script provides the same functionality as build.sh but works on all platforms.
"""
# this_file: scripts/build.py

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


class Colors:
    """ANSI color codes for terminal output."""
    
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color
    
    @classmethod
    def disable_on_windows(cls) -> None:
        """Disable colors on Windows if not supported."""
        if platform.system() == 'Windows':
            cls.RED = cls.GREEN = cls.YELLOW = cls.BLUE = cls.NC = ''


class Logger:
    """Simple logger with colored output."""
    
    def __init__(self) -> None:
        Colors.disable_on_windows()
    
    def info(self, message: str) -> None:
        """Log info message."""
        print(f"{Colors.BLUE}[INFO]{Colors.NC} {message}")
    
    def success(self, message: str) -> None:
        """Log success message."""
        print(f"{Colors.GREEN}[SUCCESS]{Colors.NC} {message}")
    
    def warning(self, message: str) -> None:
        """Log warning message."""
        print(f"{Colors.YELLOW}[WARNING]{Colors.NC} {message}")
    
    def error(self, message: str) -> None:
        """Log error message."""
        print(f"{Colors.RED}[ERROR]{Colors.NC} {message}")


class BuildManager:
    """Main build manager class."""
    
    def __init__(self) -> None:
        self.logger = Logger()
        self.script_dir = Path(__file__).parent
        self.project_root = self.script_dir.parent
        self.dist_dir = self.project_root / "dist"
        self.build_dir = self.project_root / "build"
    
    def run_command(self, command: List[str], cwd: Optional[Path] = None) -> bool:
        """Run a shell command and return success status."""
        try:
            result = subprocess.run(
                command,
                cwd=cwd or self.project_root,
                check=True,
                capture_output=True,
                text=True
            )
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed: {' '.join(command)}")
            self.logger.error(f"Error: {e.stderr}")
            return False
    
    def check_requirements(self) -> bool:
        """Check if all requirements are met."""
        self.logger.info("Checking requirements...")
        
        # Check if we're in a git repository
        if not (self.project_root / ".git").exists():
            self.logger.error("Not in a git repository")
            return False
        
        # Check if uv is installed
        try:
            subprocess.run(["uv", "--version"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.logger.error("uv is not installed. Please install it: curl -LsSf https://astral.sh/uv/install.sh | sh")
            return False
        
        self.logger.success("Requirements check passed")
        return True
    
    def clean_build(self) -> bool:
        """Clean build artifacts."""
        self.logger.info("Cleaning build artifacts...")
        
        # Remove build directories
        for directory in [self.dist_dir, self.build_dir]:
            if directory.exists():
                shutil.rmtree(directory)
        
        # Remove Python cache files
        for pattern in ["**/*.pyc", "**/__pycache__", "**/*.egg-info"]:
            for path in self.project_root.rglob(pattern):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
        
        self.logger.success("Build artifacts cleaned")
        return True
    
    def run_linting(self) -> bool:
        """Run linting and formatting checks."""
        self.logger.info("Running linting and formatting...")
        
        # Run Ruff linting
        self.logger.info("Running Ruff lint...")
        if not self.run_command(["uv", "run", "ruff", "check", "src/twat_text", "tests"]):
            self.logger.error("Ruff lint failed")
            return False
        self.logger.success("Ruff lint passed")
        
        # Run Ruff format check
        self.logger.info("Running Ruff format check...")
        if not self.run_command(["uv", "run", "ruff", "format", "--check", "src/twat_text", "tests"]):
            self.logger.error("Ruff format check failed")
            return False
        self.logger.success("Ruff format check passed")
        
        # Run MyPy type checking
        self.logger.info("Running MyPy type checking...")
        if not self.run_command(["uv", "run", "mypy", "src/twat_text", "tests"]):
            self.logger.error("MyPy type checking failed")
            return False
        self.logger.success("MyPy type checking passed")
        
        return True
    
    def run_tests(self) -> bool:
        """Run tests with coverage."""
        self.logger.info("Running tests...")
        
        # Install test dependencies
        self.logger.info("Installing test dependencies...")
        if not self.run_command(["uv", "pip", "install", ".[test]"]):
            self.logger.error("Failed to install test dependencies")
            return False
        
        # Run tests with coverage
        self.logger.info("Running pytest with coverage...")
        test_cmd = [
            "uv", "run", "pytest", "-n", "auto",
            "--cov-report=term-missing",
            "--cov-report=html",
            "--cov-config=pyproject.toml",
            "--cov=src/twat_text",
            "--cov=tests",
            "tests/"
        ]
        
        if not self.run_command(test_cmd):
            self.logger.error("Tests failed")
            return False
        self.logger.success("Tests passed")
        
        # Run benchmark tests if available
        self.logger.info("Running benchmark tests...")
        benchmark_cmd = [
            "uv", "run", "pytest", "-v", "tests/",
            "-m", "benchmark",
            "--benchmark-only",
            "--benchmark-json=benchmark_results.json"
        ]
        
        if self.run_command(benchmark_cmd):
            self.logger.success("Benchmark tests completed")
        else:
            self.logger.warning("Benchmark tests failed or not available")
        
        return True
    
    def build_package(self) -> bool:
        """Build the package."""
        self.logger.info("Building package...")
        
        # Install build dependencies
        self.logger.info("Installing build dependencies...")
        if not self.run_command(["uv", "pip", "install", "build", "hatchling", "hatch-vcs"]):
            self.logger.error("Failed to install build dependencies")
            return False
        
        # Ensure dist directory exists
        self.dist_dir.mkdir(exist_ok=True)
        
        # Build the package
        self.logger.info("Building distributions...")
        if not self.run_command(["uv", "run", "python", "-m", "build", "--outdir", str(self.dist_dir)]):
            self.logger.error("Package build failed")
            return False
        self.logger.success("Package built successfully")
        
        # List built files
        self.logger.info("Built files:")
        for file in self.dist_dir.iterdir():
            print(f"  {file.name}")
        
        # Verify distributions
        self.logger.info("Verifying distributions...")
        wheel_files = list(self.dist_dir.glob("*.whl"))
        sdist_files = list(self.dist_dir.glob("*.tar.gz"))
        
        if wheel_files and sdist_files:
            self.logger.success("Both wheel and source distributions created")
        else:
            self.logger.error("Missing distribution files")
            return False
        
        return True
    
    def check_version(self) -> bool:
        """Check version information."""
        self.logger.info("Checking version information...")
        
        # Get current version from git
        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--dirty", "--always"],
                cwd=self.project_root,
                check=True,
                capture_output=True,
                text=True
            )
            version = result.stdout.strip()
            self.logger.info(f"Current version: {version}")
        except subprocess.CalledProcessError:
            self.logger.warning("No git tags found")
        
        # Check if working directory is clean
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.project_root,
                check=True,
                capture_output=True,
                text=True
            )
            if result.stdout.strip():
                self.logger.warning("Working directory is dirty")
                print(result.stdout)
            else:
                self.logger.success("Working directory is clean")
        except subprocess.CalledProcessError:
            self.logger.error("Failed to check git status")
            return False
        
        return True
    
    def create_release_tag(self, version: str) -> bool:
        """Create a release tag."""
        self.logger.info(f"Creating release tag: {version}")
        
        # Validate version format (basic semver check)
        import re
        if not re.match(r'^v?[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9]+)?$', version):
            self.logger.error(f"Invalid version format: {version}. Expected format: v1.2.3 or 1.2.3")
            return False
        
        # Add v prefix if not present
        if not version.startswith('v'):
            version = f"v{version}"
        
        # Check if tag already exists
        try:
            result = subprocess.run(
                ["git", "tag", "-l"],
                cwd=self.project_root,
                check=True,
                capture_output=True,
                text=True
            )
            existing_tags = result.stdout.strip().split('\n')
            if version in existing_tags:
                self.logger.error(f"Tag {version} already exists")
                return False
        except subprocess.CalledProcessError:
            self.logger.error("Failed to list git tags")
            return False
        
        # Create annotated tag
        try:
            subprocess.run(
                ["git", "tag", "-a", version, "-m", f"Release {version}"],
                cwd=self.project_root,
                check=True
            )
            self.logger.success(f"Created tag: {version}")
        except subprocess.CalledProcessError:
            self.logger.error(f"Failed to create tag: {version}")
            return False
        
        # Ask if user wants to push
        response = input("Push tag to remote? [y/N] ").strip().lower()
        if response in ['y', 'yes']:
            try:
                subprocess.run(
                    ["git", "push", "origin", version],
                    cwd=self.project_root,
                    check=True
                )
                self.logger.success("Tag pushed to remote")
            except subprocess.CalledProcessError:
                self.logger.error("Failed to push tag to remote")
                return False
        else:
            self.logger.info(f"Tag not pushed. You can push later with: git push origin {version}")
        
        return True
    
    def run_full_pipeline(self, skip_clean: bool = False, skip_lint: bool = False,
                         skip_test: bool = False, skip_build: bool = False,
                         release_mode: bool = False) -> bool:
        """Run the full build pipeline."""
        self.logger.info("Running full build pipeline...")
        
        # Clean (unless skipped)
        if not skip_clean:
            if not self.clean_build():
                return False
        
        # Version check
        if not self.check_version():
            return False
        
        # Lint (unless skipped)
        if not skip_lint:
            if not self.run_linting():
                return False
        
        # Test (unless skipped)
        if not skip_test:
            if not self.run_tests():
                return False
        
        # Build (unless skipped)
        if not skip_build:
            if not self.build_package():
                return False
        
        self.logger.success("Full build pipeline completed successfully!")
        
        if release_mode:
            self.logger.info("Release mode: Package is ready for release")
            self.logger.info("To create a release tag, run: python scripts/build.py tag <version>")
        
        return True


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Build script for twat-text project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/build.py                    # Run full pipeline
  python scripts/build.py test               # Run tests only
  python scripts/build.py lint               # Run linting only
  python scripts/build.py build              # Build package only
  python scripts/build.py tag v1.2.3         # Create release tag
  python scripts/build.py --no-test build    # Build without running tests
  python scripts/build.py --release          # Full release pipeline
"""
    )
    
    parser.add_argument(
        'command',
        nargs='?',
        default='all',
        choices=['test', 'lint', 'build', 'clean', 'version', 'tag', 'all'],
        help='Command to run (default: all)'
    )
    
    parser.add_argument(
        'version',
        nargs='?',
        help='Version for tag command (e.g., v1.2.3)'
    )
    
    parser.add_argument(
        '--no-clean',
        action='store_true',
        help='Skip cleaning build artifacts'
    )
    
    parser.add_argument(
        '--no-lint',
        action='store_true',
        help='Skip linting checks'
    )
    
    parser.add_argument(
        '--no-test',
        action='store_true',
        help='Skip running tests'
    )
    
    parser.add_argument(
        '--no-build',
        action='store_true',
        help='Skip building package'
    )
    
    parser.add_argument(
        '--release',
        action='store_true',
        help='Prepare for release (implies all checks)'
    )
    
    args = parser.parse_args()
    
    # Validate tag command
    if args.command == 'tag' and not args.version:
        parser.error("tag command requires a version argument")
    
    # Create build manager
    build_manager = BuildManager()
    
    # Check requirements
    if not build_manager.check_requirements():
        sys.exit(1)
    
    # Execute command
    success = False
    
    if args.command == 'clean':
        success = build_manager.clean_build()
    elif args.command == 'lint':
        success = build_manager.run_linting()
    elif args.command == 'test':
        success = build_manager.run_tests()
    elif args.command == 'build':
        success = build_manager.build_package()
    elif args.command == 'version':
        success = build_manager.check_version()
    elif args.command == 'tag':
        success = build_manager.create_release_tag(args.version)
    elif args.command == 'all':
        success = build_manager.run_full_pipeline(
            skip_clean=args.no_clean,
            skip_lint=args.no_lint,
            skip_test=args.no_test,
            skip_build=args.no_build,
            release_mode=args.release
        )
    
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()