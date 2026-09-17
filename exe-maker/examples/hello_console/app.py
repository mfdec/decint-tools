"""Example console app. Prints its license status and exits."""
import os
import sys

import helpers


def main() -> int:
    print("hello_console running, python", sys.version.split()[0])
    print("license mode:", os.environ.get("DECINT_LICENSE_MODE", "unlicensed build"))
    print("customer   :", os.environ.get("DECINT_LICENSE_CUSTOMER", "-"))
    print("features   :", os.environ.get("DECINT_LICENSE_FEATURES", "-"))
    print("helper says:", helpers.shout("ok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
