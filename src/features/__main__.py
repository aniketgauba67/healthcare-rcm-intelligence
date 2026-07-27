"""`python -m src.features` — rebuild the committed Model A feature matrix.

A separate entry point rather than `python -m src.features.store`, which warns:
the package `__init__` imports `store` eagerly (the guard needs
`build_training_matrix` on the package), so running the submodule as a script
re-executes a module already in `sys.modules`.
"""

from src.features.store import main

if __name__ == "__main__":
    raise SystemExit(main())
