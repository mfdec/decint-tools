"""``python -m decint_exe_maker`` — GUI by default, CLI when a subcommand is given."""
import multiprocessing
import sys

from .cli import COMMANDS


def entry() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS or "-h" in sys.argv or "--help" in sys.argv:
        from .cli import main
        return main()
    from .gui import main
    return main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(entry())
