---
description: Enforce structured unit test files instead of inline terminal scripts
globs: ["**/*.py"]
---

# Testing Standards & Unit Test Files

1. **Dedicated Test Files Over Inline Scripts**:
   - Never execute long, multi-line inline Python scripts via `python -c "..."` in terminal commands to verify code.
   - Always create dedicated, persistent test files (e.g., `tests/test_<module>.py` or `test_<feature>.py`) using standard testing frameworks (`unittest` or `pytest`).

2. **Test File Organization**:
   - Place tests in an appropriate test directory (e.g., `tests/` or alongside the module if conventional for the project).
   - Use clear test case naming conventions (`test_<function_name>_<scenario>`).

3. **Reproducibility & Inspection**:
   - Ensure tests are cleanly runnable by both the agent and the user via standard runners (e.g., `pytest` or `python -m unittest discover`).
   - Mock external network calls (IMAP, HTTP requests, APIs) so tests run deterministically and fast without side effects.
