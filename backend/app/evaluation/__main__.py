import os
import sys

os.environ.setdefault("TESTING", "1")

from app.evaluation.runner import main  # noqa: E402

sys.exit(main())
