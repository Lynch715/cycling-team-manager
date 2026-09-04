from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release" / "模拟自行车队经理_美术资源"
CHECKSUMS = RELEASE / "CHECKSUMS-FULL.sha256"
ARCHIVE = ROOT / "release" / "模拟自行车队经理_完整美术资源_254张.zip"
ARCHIVE_HASH = ROOT / "release" / "模拟自行车队经理_完整美术资源_254张.zip.sha256"


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main():
    files = sorted(path for path in RELEASE.rglob("*") if path.is_file() and path != CHECKSUMS)
    lines = [f"{digest(path)}  {path.relative_to(RELEASE).as_posix()}" for path in files]
    CHECKSUMS.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    if ARCHIVE.exists():
        ARCHIVE.unlink()
    with ZipFile(ARCHIVE, "w", compression=ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(RELEASE.rglob("*")):
            if path.is_file():
                arcname = Path(RELEASE.name) / path.relative_to(RELEASE)
                bundle.write(path, arcname)

    with ZipFile(ARCHIVE) as bundle:
        bad_file = bundle.testzip()
        if bad_file:
            raise SystemExit(f"ZIP verification failed at {bad_file}")

    archive_digest = digest(ARCHIVE)
    ARCHIVE_HASH.write_text(f"{archive_digest}  {ARCHIVE.name}\n", encoding="utf-8")
    print(f"Checksummed files: {len(files)}")
    print(f"Archive: {ARCHIVE}")
    print(f"Archive bytes: {ARCHIVE.stat().st_size}")
    print(f"Archive SHA-256: {archive_digest}")
    print("PASS: zipfile.testzip")


if __name__ == "__main__":
    main()
