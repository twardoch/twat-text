# twat-text

**twat-text** is a Python utility designed for text processing tasks. It is part of the [twat project collection](https://pypi.org/project/twat/) on PyPI.

*This project is currently under active development. While the foundational structure is in place, the core data processing functionalities are being actively developed and refined.*

## What `twat-text` (aims to) do?

`twat-text` is intended to provide a robust and flexible toolkit for various text manipulation and analysis needs. As a component of the `twat` ecosystem, it aims to integrate seamlessly to extend text-based capabilities. The specific processing features are currently being implemented.

## Who is it for?

*   Python developers looking for a modern, type-safe library for text-related operations.
*   Users and developers within the `twat` ecosystem.
*   Anyone needing scriptable and reusable text utilities.

## Why is it useful?

*   **Modern Python:** Built with contemporary Python practices, including type hints and PEP 621 packaging.
*   **Extensible:** Designed to be easily integrated into other Python projects or workflows.
*   **Ecosystem Integration:** Developed as a plugin for the `twat` collection, ensuring compatibility and collaborative potential.
*   **Placeholder for Future Growth:** While core logic is in development, the structure supports future expansion of text processing features.

## Installation

You can install `twat-text` directly from PyPI:

```bash
pip install twat-text
```

## Usage

### Programmatic Usage

You can use `twat-text` in your Python projects as follows:

```python
from twat_text import twat_text

# Initialize a configuration (example)
config = twat_text.Config(name="default_config", value="example_value", options={"key1": "val1"})

# Example input data (currently, the function expects a list)
sample_data = ["some text data", "another piece of text"]

# Process data
# Note: The process_data function is currently a placeholder.
# Its full functionality and output structure are under development.
try:
    result = twat_text.process_data(sample_data, config=config, debug=True)
    print(f"Processing result: {result}")
except ValueError as e:
    print(f"Error: {e}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")

```
**Important Note:** The `process_data` function is currently in a preliminary stage and its core logic is yet to be implemented. It will return a basic dictionary as a placeholder. Future versions will include comprehensive text processing capabilities.

### Command-Line Interface (CLI) Usage

A command-line interface for `twat-text` is planned for future releases. Currently, the package does not install a direct CLI command. The `src/twat_text/twat_text.py` file contains a `main()` function which demonstrates example usage of the library's functions but is not intended for direct end-user CLI interaction at this stage.

## Technical Details

This section provides a deeper dive into the codebase, development practices, and contribution guidelines for `twat-text`.

### How the Code Works

The primary logic for `twat-text` resides in the `src/twat_text/twat_text.py` module.

*   **Core Components:**
    *   **`Config` Dataclass:** Defined using `@dataclass`, this class (`twat_text.Config`) is used to hold configuration settings for processing operations. It includes fields such as `name: str`, `value: str | int | float`, and an optional `options: dict[str, Any]`.
    *   **`process_data` Function:** This is the main function (`twat_text.process_data`) intended for handling data processing.
        *   **Signature:** `process_data(data: list[Any], config: Config | None = None, *, debug: bool = False) -> dict[str, Any]`
        *   **Purpose:** To process the input `data` according to the provided `config`.
        *   **Current Status:** The function currently contains placeholder logic and returns an empty dictionary. The core implementation is under development.
        *   It can raise a `ValueError` for invalid input data.
    *   **`main` Function:** Serves as an example entry point within `twat_text.py` to demonstrate basic usage of the `Config` class and `process_data` function. It also includes basic error handling.

*   **Logging:** The project uses Python's standard `logging` module. A basic configuration is set up to log information, errors, and debug messages when enabled.

*   **Type Safety:** The codebase extensively uses type hints as per PEP 484 to improve code clarity and enable static analysis.

### Development & Contribution

This project uses [Hatch](https://hatch.pypa.io/) for managing the development workflow, including dependency management, virtual environments, and running scripts.

*   **Setup Development Environment:**
    1.  Install Hatch (if not already installed):
        ```bash
        pip install hatch
        ```
    2.  Activate the project's managed environment:
        ```bash
        hatch shell
        ```

*   **Running Tests:**
    Tests are written using `pytest` and can be run via Hatch:
    *   Run all tests:
        ```bash
        hatch run test
        ```
    *   Run tests with coverage report:
        ```bash
        hatch run test-cov
        ```

*   **Linting and Formatting:**
    The project uses `Ruff` for fast linting and formatting, and `Mypy` for static type checking.
    *   Run linters and formatters:
        ```bash
        hatch run lint
        ```
    *   To run type checking specifically:
        ```bash
        hatch run type-check
        # or
        hatch run lint:typing
        ```
    *   Ruff and Mypy configurations can be found in the `pyproject.toml` file.

*   **Coding Standards:**
    *   Adherence to PEP 8 guidelines is enforced by Ruff.
    *   Comprehensive type hinting is expected for all new contributions.
    *   Follow existing code style and patterns.

*   **Version Control & Versioning:**
    *   The project uses Git for version control.
    *   Package versioning is dynamic and managed by `hatch-vcs`, which derives the version from Git tags. The version string is automatically written to `src/twat_text/__version__.py` during the build process.

*   **CI/CD (Continuous Integration/Continuous Deployment):**
    GitHub Actions are configured for:
    *   **Automated Checks (`.github/workflows/push.yml`):** On every push and pull request to the `main` branch, workflows automatically run linters (Ruff), format checks, and tests (pytest) across multiple Python versions.
    *   **Release Publishing (`.github/workflows/release.yml`):** When a new Git tag matching the pattern `v*` (e.g., `v0.1.0`) is pushed, a GitHub Action workflow builds the package and publishes it to PyPI. It also creates a corresponding GitHub Release.

*   **Key Dependencies:**
    *   **Runtime:** `twat>=1.8.1` (core dependency from the twat ecosystem).
    *   **Development:** `pytest`, `pytest-cov`, `mypy`, `ruff`. For a full list, see `[tool.hatch.envs.default.dependencies]` and `[project.optional-dependencies]` in `pyproject.toml`.

*   **Plugin System Integration:**
    `twat-text` is designed as a plugin for the `twat` framework. It is registered via the `[project.entry-points."twat.plugins"]` section in `pyproject.toml`, making its functionalities discoverable under the name `text` within the `twat` ecosystem.

*   **Supported Python Versions:**
    As specified in `pyproject.toml`, `twat-text` requires Python `>=3.10` and is tested against Python 3.10, 3.11, and 3.12.

*   **Contributing:**
    Contributions are welcome! Please follow these general guidelines:
    1.  If you plan to make significant changes, please open an issue first to discuss your ideas.
    2.  Fork the repository on GitHub.
    3.  Create a new branch for your feature or bug fix.
    4.  Make your changes, ensuring you add or update tests and documentation as appropriate.
    5.  Ensure all tests pass (`hatch run test`) and linters are clean (`hatch run lint`).
    6.  Submit a pull request to the `main` branch of the original repository.

### License

`twat-text` is licensed under the MIT License. See the `LICENSE` file for more details.
