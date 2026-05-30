import hashlib
import os
import sys
import stat

PASSWORD_HASH = "b0f8574c610265c38be0816eaef8abde4dfae20ba11918bb04eebdede5917fd4"

LOCK_DIRS = [
    os.path.dirname(os.path.abspath(__file__)),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "handlers"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"),
]

EXTENSIONS = (".py", ".sh", ".txt", ".cfg", ".ini", ".toml", ".nix")

EXCLUDE_FILES = {"locker.py"}


def collect_files():
    files = []
    for d in LOCK_DIRS:
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if fname in EXCLUDE_FILES:
                continue
            if fname.endswith(EXTENSIONS):
                files.append(os.path.join(d, fname))
    return files


def check_password(pwd: str) -> bool:
    return hashlib.sha256(pwd.encode()).hexdigest() == PASSWORD_HASH


def lock_files():
    files = collect_files()
    for f in files:
        try:
            os.chmod(f, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        except Exception as e:
            print(f"  [!] Could not lock {f}: {e}")
    print(f"\n🔒 LOCKED — {len(files)} files are now READ-ONLY.")
    print("    No one can edit or change the code without the password.")


def unlock_files(pwd: str):
    if not check_password(pwd):
        print("\n❌ WRONG PASSWORD — Access denied.")
        sys.exit(1)
    files = collect_files()
    for f in files:
        try:
            os.chmod(f, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        except Exception as e:
            print(f"  [!] Could not unlock {f}: {e}")
    print(f"\n✅ UNLOCKED — {len(files)} files are now EDITABLE.")


def status():
    files = collect_files()
    locked = []
    unlocked = []
    for f in files:
        mode = os.stat(f).st_mode
        if mode & stat.S_IWUSR:
            unlocked.append(os.path.basename(f))
        else:
            locked.append(os.path.basename(f))
    print(f"\n📂 FILE STATUS REPORT")
    print(f"   🔒 Locked   : {len(locked)} files")
    print(f"   🔓 Unlocked : {len(unlocked)} files")
    if locked:
        print("\n  Locked files:")
        for f in sorted(locked):
            print(f"    - {f}")
    if unlocked:
        print("\n  Unlocked files:")
        for f in sorted(unlocked):
            print(f"    - {f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python locker.py lock              — Lock all files (read-only)")
        print("  python locker.py unlock <password> — Unlock all files")
        print("  python locker.py status            — Check lock status")
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "lock":
        lock_files()

    elif cmd == "unlock":
        if len(sys.argv) < 3:
            print("Usage: python locker.py unlock <password>")
            sys.exit(1)
        unlock_files(sys.argv[2])

    elif cmd == "status":
        status()

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
