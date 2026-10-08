"""Generate fresh, ignored container credentials without touching local .env."""

import argparse
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / ".local/compose.env")
    args = parser.parse_args()
    path = args.out.resolve()
    if not path.is_relative_to(ROOT / ".local"):
        parser.error("Credentials must stay inside the ignored .local directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for role in ("ADMIN", "APP", "READER"):
            stream.write(f"SHOPLENS_PG_{role}_PASSWORD={secrets.token_urlsafe(32)}\n")
        stream.write("SHOPLENS_MODEL_PROVIDER=offline\nSHOPLENS_DEEPSEEK_API_KEY=\n")
    print(
        f"Created {path.relative_to(ROOT)}; passwords are not printed. Existing files are never replaced."
    )


if __name__ == "__main__":
    main()
