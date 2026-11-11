"""Create local admin env configuration; never print credentials."""
import argparse
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_auth import password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--username', default='admin')
    parser.add_argument('--output', default='.env.admin')
    args = parser.parse_args()
    path = Path(args.output)
    password_path = path.with_suffix(path.suffix + '.password')
    if path.exists() or password_path.exists():
        parser.error('Output already exists. Choose a new path; existing credentials are preserved.')
    password, salt = secrets.token_urlsafe(32), secrets.token_hex(16)
    if not args.username or not args.username.isascii() or not all(c.isalnum() or c in '_.-' for c in args.username):
        parser.error('Use an ASCII username with letters, digits, dot, underscore or hyphen.')
    for target, content in [(path, f'IIPP_ADMIN_USERNAME={args.username}\nIIPP_ADMIN_SALT={salt}\nIIPP_ADMIN_PASSWORD_HASH={password_hash(password, salt)}\n'),
                            (password_path, password + '\n')]:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
    print('Admin configuration created. Retrieve the password from the protected .password file locally, then remove that file after saving it in your password manager.')


if __name__ == '__main__':
    main()
