"""Nexora SAGE public CLI entrypoint.

The public command is intentionally simple: `python sage.py ...`.
The implementation currently lives in codemaps.py until the internal CLI module
is extracted behind a narrower contract.
"""

from codemaps import main


if __name__ == "__main__":
    main()
