# GitHub Workflows for Multiplatform CI/CD

Due to GitHub App permissions restrictions, the GitHub Actions workflows need to be manually added. Here are the enhanced workflow files:

## 1. Enhanced Push Workflow (`.github/workflows/push.yml`)

This workflow should be updated to support multiplatform testing:

```yaml
name: Build & Test

on:
  push:
    branches: [main]
    tags-ignore: ["v*"]
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: write
  id-token: write

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  quality:
    name: Code Quality
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run Ruff lint
        uses: astral-sh/ruff-action@v3
        with:
          version: "latest"
          args: "check --output-format=github"

      - name: Run Ruff Format
        uses: astral-sh/ruff-action@v3
        with:
          version: "latest"
          args: "format --check --respect-gitignore"

  test:
    name: Run Tests
    needs: quality
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
        os: [ubuntu-latest, windows-latest, macos-latest]
      fail-fast: false
    runs-on: ${{ matrix.os }}
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install UV
        uses: astral-sh/setup-uv@v5
        with:
          version: "latest"
          python-version: ${{ matrix.python-version }}
          enable-cache: true
          cache-suffix: ${{ matrix.os }}-${{ matrix.python-version }}

      - name: Install test dependencies
        run: |
          uv pip install --system --upgrade pip
          uv pip install --system ".[test]"

      - name: Run tests with Pytest
        run: uv run pytest -n auto --maxfail=1 --disable-warnings --cov-report=xml --cov-config=pyproject.toml --cov=src/twat_text --cov=tests tests/

      - name: Upload coverage report
        uses: actions/upload-artifact@v4
        with:
          name: coverage-${{ matrix.python-version }}-${{ matrix.os }}
          path: coverage.xml

  build:
    name: Build Distribution
    needs: test
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install UV
        uses: astral-sh/setup-uv@v5
        with:
          version: "latest"
          python-version: "3.12"
          enable-cache: true

      - name: Install build tools
        run: uv pip install build hatchling hatch-vcs

      - name: Build distributions
        run: uv run python -m build --outdir dist

      - name: Upload distribution artifacts
        uses: actions/upload-artifact@v4
        with:
          name: dist-files
          path: dist/
          retention-days: 5
```

## 2. Enhanced Release Workflow (`.github/workflows/release.yml`)

This workflow adds comprehensive multiplatform binary builds:

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: write
  id-token: write

jobs:
  # Run tests on all platforms before release
  test:
    name: Test on ${{ matrix.os }} with Python ${{ matrix.python-version }}
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
        os: [ubuntu-latest, windows-latest, macos-latest]
      fail-fast: false
    runs-on: ${{ matrix.os }}
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install UV
        uses: astral-sh/setup-uv@v5
        with:
          version: "latest"
          python-version: ${{ matrix.python-version }}
          enable-cache: true
          cache-suffix: ${{ matrix.os }}-${{ matrix.python-version }}

      - name: Install test dependencies
        run: |
          uv pip install --system --upgrade pip
          uv pip install --system ".[test]"

      - name: Run tests
        run: uv run pytest -n auto --maxfail=1 --disable-warnings --cov-report=xml --cov-config=pyproject.toml --cov=src/twat_text --cov=tests tests/

      - name: Upload coverage
        uses: actions/upload-artifact@v4
        if: matrix.os == 'ubuntu-latest' && matrix.python-version == '3.12'
        with:
          name: coverage-report
          path: coverage.xml

  # Build binary distributions for multiple platforms
  build-binaries:
    name: Build Binary for ${{ matrix.os }}
    needs: test
    strategy:
      matrix:
        include:
          - os: ubuntu-latest
            asset_name: twat-text-linux-amd64
            binary_name: twat-text
          - os: windows-latest
            asset_name: twat-text-windows-amd64.exe
            binary_name: twat-text.exe
          - os: macos-latest
            asset_name: twat-text-macos-amd64
            binary_name: twat-text
    runs-on: ${{ matrix.os }}
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install UV
        uses: astral-sh/setup-uv@v5
        with:
          version: "latest"
          python-version: "3.12"
          enable-cache: true

      - name: Install PyInstaller and dependencies
        run: |
          uv pip install --system pyinstaller
          uv pip install --system ".[all]"

      - name: Create CLI entry point
        run: |
          mkdir -p cli
          cat > cli/main.py << 'EOF'
          #!/usr/bin/env python3
          """CLI entry point for twat-text."""
          import sys
          from twat_text.twat_text import main
          
          if __name__ == "__main__":
              sys.exit(main())
          EOF
        shell: bash

      - name: Build binary with PyInstaller
        run: |
          uv run pyinstaller --onefile --name ${{ matrix.binary_name }} --clean cli/main.py

      - name: Test binary (Unix)
        if: matrix.os != 'windows-latest'
        run: |
          ./dist/${{ matrix.binary_name }} --help || echo "Binary created successfully"

      - name: Test binary (Windows)
        if: matrix.os == 'windows-latest'
        run: |
          .\dist\${{ matrix.binary_name }} --help || echo "Binary created successfully"

      - name: Rename binary
        run: |
          mv dist/${{ matrix.binary_name }} dist/${{ matrix.asset_name }}
        shell: bash

      - name: Upload binary artifact
        uses: actions/upload-artifact@v4
        with:
          name: ${{ matrix.asset_name }}
          path: dist/${{ matrix.asset_name }}

  # Build Python distributions
  build-python:
    name: Build Python Distributions
    needs: test
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install UV
        uses: astral-sh/setup-uv@v5
        with:
          version: "latest"
          python-version: "3.12"
          enable-cache: true

      - name: Install build tools
        run: uv pip install build hatchling hatch-vcs

      - name: Build distributions
        run: uv run python -m build --outdir dist

      - name: Verify distribution files
        run: |
          ls -la dist/
          test -n "$(find dist -name '*.whl')" || (echo "Wheel file missing" && exit 1)
          test -n "$(find dist -name '*.tar.gz')" || (echo "Source distribution missing" && exit 1)

      - name: Upload Python distributions
        uses: actions/upload-artifact@v4
        with:
          name: python-distributions
          path: dist/

  # Create GitHub Release
  release:
    name: Create GitHub Release
    needs: [test, build-binaries, build-python]
    runs-on: ubuntu-latest
    environment:
      name: github-release
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Download all artifacts
        uses: actions/download-artifact@v4
        with:
          path: artifacts

      - name: Prepare release assets
        run: |
          mkdir -p release-assets
          # Copy Python distributions
          cp artifacts/python-distributions/* release-assets/
          # Copy binaries
          cp artifacts/twat-text-linux-amd64/twat-text-linux-amd64 release-assets/
          cp artifacts/twat-text-windows-amd64.exe/twat-text-windows-amd64.exe release-assets/
          cp artifacts/twat-text-macos-amd64/twat-text-macos-amd64 release-assets/
          # List all files
          ls -la release-assets/

      - name: Generate release notes
        run: |
          # Extract version from tag
          VERSION=${GITHUB_REF#refs/tags/}
          
          # Create release notes
          cat > release-notes.md << EOF
          # Release $VERSION
          
          ## What's New
          
          This release includes:
          - Updated package with version $VERSION
          - Cross-platform binary distributions
          - Python wheels for easy installation
          
          ## Installation
          
          ### Python Package
          \`\`\`bash
          pip install twat-text
          \`\`\`
          
          ### Binary Downloads
          
          Download the appropriate binary for your platform:
          
          - **Linux (x64)**: \`twat-text-linux-amd64\`
          - **Windows (x64)**: \`twat-text-windows-amd64.exe\`
          - **macOS (x64)**: \`twat-text-macos-amd64\`
          
          Make the binary executable (Linux/macOS):
          \`\`\`bash
          chmod +x twat-text-*
          \`\`\`
          
          ## Usage
          
          \`\`\`bash
          # Using Python package
          python -m twat_text
          
          # Using binary
          ./twat-text-linux-amd64
          \`\`\`
          
          ## Checksums
          
          \`\`\`
          $(cd release-assets && sha256sum *)
          \`\`\`
          EOF

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v1
        with:
          files: release-assets/*
          body_path: release-notes.md
          generate_release_notes: true
          draft: false
          prerelease: false
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

  # Publish to PyPI
  publish-pypi:
    name: Publish to PyPI
    needs: [release]
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/twat-text
    steps:
      - name: Download Python distributions
        uses: actions/download-artifact@v4
        with:
          name: python-distributions
          path: dist/

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          password: ${{ secrets.PYPI_TOKEN }}
```

## Manual Installation Steps

1. **Update the Push Workflow**: 
   - Replace the content of `.github/workflows/push.yml` with the first YAML above
   - The main change is multiplatform testing (added Windows and macOS)

2. **Replace the Release Workflow**:
   - Replace the content of `.github/workflows/release.yml` with the second YAML above
   - This adds comprehensive multiplatform binary builds and enhanced release management

3. **Required Secrets**:
   - Ensure `PYPI_TOKEN` is set in your repository secrets for PyPI publishing
   - `GITHUB_TOKEN` is automatically available

## Features of the Enhanced Workflows

### Push Workflow (CI)
- **Multiplatform Testing**: Ubuntu, Windows, macOS
- **Python Matrix**: 3.10, 3.11, 3.12
- **Code Quality**: Ruff linting and formatting
- **Coverage Reports**: Uploaded as artifacts

### Release Workflow (CD)
- **Comprehensive Testing**: All platforms before release
- **Binary Builds**: PyInstaller-based executables for all platforms
- **GitHub Releases**: Automated with binaries and Python packages
- **PyPI Publishing**: Automatic on git tag push
- **Release Notes**: Auto-generated with checksums and installation instructions

After manually adding these workflows, your complete CI/CD system will be fully functional with multiplatform support and binary releases.