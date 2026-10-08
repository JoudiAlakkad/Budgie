"""Load the demo data into an empty database (decision 0022).

`python -m app.demo_seed [--today YYYY-MM-DD]`, or `make seed-demo`. Exit codes: 0 loaded,
1 the database isn't empty (nothing changed), 2 the storage isn't usable or the seed
stopped partway (an app error; delete the database and run it again). Any other exception
is a bug and stays a traceback.
"""

import argparse
import datetime as dt
import sys

from app.config import Settings, get_settings
from app.db.images import ImageStore
from app.errors import BudgieError, InvalidState, StorageError
from app.services.demo_seed import seed_demo
from app.services.dependencies import database_for, today_in_berlin
from app.services.storage import close_storage, prepare_storage

EXIT_OK = 0
EXIT_NOT_EMPTY = 1
EXIT_STORAGE = 2


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--today",
        type=dt.date.fromisoformat,
        help="the date the demo is relative to (default: today in Europe/Berlin)",
    )
    args = parser.parse_args(argv)
    settings = settings or get_settings()
    today: dt.date = args.today or today_in_berlin()
    try:
        status = prepare_storage(settings)
        if not status.ok:
            print(f"Storage is not usable (failed: {', '.join(status.failures)}).", file=sys.stderr)
            return EXIT_STORAGE
        db = database_for(settings.database_url)
        result = seed_demo(db, ImageStore(settings.upload_dir), today)
    except InvalidState as exc:
        print(exc.detail, file=sys.stderr)
        return EXIT_NOT_EMPTY
    except StorageError as exc:
        # The seed isn't atomic (one transaction per expense, decision 0022).
        print(
            f"Storage error: {exc.detail} The demo data may be partly loaded; "
            "delete the database and run it again.",
            file=sys.stderr,
        )
        return EXIT_STORAGE
    except BudgieError as exc:
        # A rule refused a planned expense, budget or goal (a bug in the plan): the
        # expenses before it are already committed. Programming errors stay tracebacks.
        print(
            f"The demo data could not be loaded ({exc.code}: {exc.detail}) It may be partly "
            "loaded; delete the database and run it again.",
            file=sys.stderr,
        )
        return EXIT_STORAGE
    finally:
        close_storage()
    print(
        f"Loaded {result.expenses} confirmed expenses ({result.first_day} to "
        f"{result.last_day}), {result.budgets} budgets and a savings goal, relative to {today}."
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
