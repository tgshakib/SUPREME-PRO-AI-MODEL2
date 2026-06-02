"""
BUILD SUPREME PRO AI BOT — Password-Protected Download Package
==============================================================
Builds SUPREME_PRO_AI_BOT.zip with AES-256 encryption.
Password: tgoawhidshakib

All .py files, handlers/, assets/, Dockerfile, Procfile,
requirements.txt, runtime.txt, start.sh, .env.example,
README.md — everything needed to run on ANY host.

Usage:
    python build_locked_zip.py

Output:
    SUPREME_PRO_AI_BOT.zip  (AES-256, password-protected)
    mybot_backup.zip        (same file, alias for download server)
"""
import os
import sys
import zipfile
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_ZIP  = os.path.join(ROOT, "SUPREME_PRO_AI_BOT.zip")
OUT_ALT  = os.path.join(ROOT, "mybot_backup.zip")
PASSWORD = "tgoawhidshakib"

# ── Files / dirs to include ──────────────────────────────────────────
INCLUDE_DIRS = [
    "handlers",
    "assets",
]

INCLUDE_EXTS = {
    ".py", ".sh", ".txt", ".cfg", ".ini",
    ".toml", ".md", ".example", ".nix",
}

INCLUDE_NAMED = {
    "Dockerfile", "Procfile", ".env.example",
    "requirements.txt", "runtime.txt", "start.sh",
    "README.md", ".github",
}

EXCLUDE_FILES = {
    "build_locked_zip.py",
    "locker.py",
    "zipFile.zip",
    "SUPREME_PRO_AI_BOT.zip",
    "mybot_backup.zip",
    "trading_bot.db",
    "trading_bot.db-shm",
    "trading_bot.db-wal",
    "__pycache__",
}


def collect_files() -> list[tuple[str, str]]:
    """Return list of (absolute_path, archive_name) tuples."""
    files: list[tuple[str, str]] = []

    # ── Root-level .py and named files ──────────────────────────────
    for fname in sorted(os.listdir(ROOT)):
        if fname in EXCLUDE_FILES or fname.startswith("."):
            continue
        fpath = os.path.join(ROOT, fname)
        if os.path.isfile(fpath):
            ext = os.path.splitext(fname)[1]
            if ext in INCLUDE_EXTS or fname in INCLUDE_NAMED:
                files.append((fpath, fname))

    # ── handlers/ directory ─────────────────────────────────────────
    h_dir = os.path.join(ROOT, "handlers")
    if os.path.isdir(h_dir):
        for fname in sorted(os.listdir(h_dir)):
            if fname.startswith("_") and fname != "__init__.py":
                continue
            fpath = os.path.join(h_dir, fname)
            if os.path.isfile(fpath) and os.path.splitext(fname)[1] in INCLUDE_EXTS:
                files.append((fpath, f"handlers/{fname}"))

    # ── assets/ directory ───────────────────────────────────────────
    a_dir = os.path.join(ROOT, "assets")
    if os.path.isdir(a_dir):
        for fname in sorted(os.listdir(a_dir)):
            fpath = os.path.join(a_dir, fname)
            if os.path.isfile(fpath):
                files.append((fpath, f"assets/{fname}"))

    # ── .github/ workflow ───────────────────────────────────────────
    gh_dir = os.path.join(ROOT, ".github")
    if os.path.isdir(gh_dir):
        for dirpath, _, fnames in os.walk(gh_dir):
            for fname in fnames:
                fpath = os.path.join(dirpath, fname)
                arc   = os.path.relpath(fpath, ROOT)
                files.append((fpath, arc))

    return files


def build_zip():
    files = collect_files()
    print(f"\n📦 Building SUPREME PRO AI BOT package...")
    print(f"   Files to include: {len(files)}")

    # ── Try pyzipper for AES-256 ────────────────────────────────────
    try:
        import pyzipper
        with pyzipper.AESZipFile(
            OUT_ZIP, "w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as zf:
            zf.setpassword(PASSWORD.encode())
            for fpath, arcname in files:
                try:
                    zf.write(fpath, arcname)
                    print(f"   ✅ {arcname}")
                except Exception as e:
                    print(f"   ⚠️  {arcname}: {e}")
        method = "AES-256 (pyzipper)"
    except ImportError:
        # Fallback: standard zipfile (password not supported — store unencrypted)
        print("   ℹ️  pyzipper not available — building unencrypted zip")
        print("   Install: pip install pyzipper  for AES-256 encryption")
        with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath, arcname in files:
                try:
                    zf.write(fpath, arcname)
                    print(f"   ✅ {arcname}")
                except Exception as e:
                    print(f"   ⚠️  {arcname}: {e}")
        method = "DEFLATE (no encryption — install pyzipper for AES-256)"

    # ── Copy to mybot_backup.zip for download server ─────────────────
    shutil.copy2(OUT_ZIP, OUT_ALT)

    size_mb = os.path.getsize(OUT_ZIP) / (1024 * 1024)
    print(f"\n🎉 Done!")
    print(f"   Output : SUPREME_PRO_AI_BOT.zip  ({size_mb:.2f} MB)")
    print(f"   Method : {method}")
    print(f"   Password: {PASSWORD}")
    print(f"   Alias  : mybot_backup.zip (for /download endpoint)")
    print(f"\n📌 Deploy on any host:")
    print(f"   JustRunMy / Render / Railway / Fly.io / Heroku / VPS / GitHub Actions")
    print(f"   Set env vars from .env.example → run: python bot.py")


if __name__ == "__main__":
    build_zip()
