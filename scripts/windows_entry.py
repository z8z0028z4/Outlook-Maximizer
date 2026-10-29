"""Small packaged entry point with an isolated developer-only self-test."""

import sys

if len(sys.argv) >= 2 and sys.argv[1] == "--packaged-self-test":
    from packaged_selftest import main

    raise SystemExit(main(sys.argv[2:]))

from app import main

raise SystemExit(main())
