"""Create backend/.env (or .env.production) from .env.example with fresh random secrets.

    python scripts/init_env.py               # development: .env
    python scripts/init_env.py --production  # production: .env.production

Never overwrites an existing file. Works with any Python 3.8+, no packages.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--production", action="store_true", help="write .env.production with production settings"
    )
    ap.add_argument("--timezone", default=None, help="default owner time zone, e.g. Asia/Ashgabat")
    args = ap.parse_args()

    target = ROOT / (".env.production" if args.production else ".env")
    if target.exists():
        sys.exit(f"{target.name} already exists - not touching it.")

    storage_secret = secrets.token_urlsafe(24)
    values = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "RUSTFS_SECRET_KEY": storage_secret,
        "PIKIRLER_S3_SECRET_KEY": storage_secret,  # the API uses the RustFS root account
        "PIKIRLER_SECRET_KEY": secrets.token_urlsafe(48),
    }
    if args.production:
        values["PIKIRLER_ENVIRONMENT"] = "production"
        values["PIKIRLER_COOKIE_SECURE"] = "true"
        values["PIKIRLER_CORS_ORIGINS"] = ""
    if args.timezone:
        values["PIKIRLER_DEFAULT_TIMEZONE"] = args.timezone

    out = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        if "=" in line and not line.lstrip().startswith("#") and key in values:
            line = f"{key}={values[key]}"
        out.append(line)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {target} with new random passwords and secret key.")


if __name__ == "__main__":
    main()
