"""Administrator utility: create/update a protected local control-plane identity."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', required=True, help='Protected registry outside the source checkout')
    parser.add_argument('--username', required=True)
    parser.add_argument('--role', required=True, choices=['administrator', 'requester', 'approver', 'viewer'])
    parser.add_argument('--customer', action='append', default=[])
    args = parser.parse_args()
    password = getpass.getpass('New password: ')
    if len(password) < 14:
        parser.error('Use at least 14 characters')
    if password != getpass.getpass('Confirm password: '):
        parser.error('Passwords differ')
    path = Path(args.file)
    if not path.parent.is_dir():
        parser.error('Create a protected parent directory first')
    registry = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    salt = secrets.token_bytes(16)
    registry[args.username] = dict(role=args.role, customers=args.customer, salt=salt.hex(),
                                 password_hash=hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000).hex())
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(registry, stream, indent=2)
    temporary.replace(path)
    os.chmod(path, 0o600)
    print('Identity saved. Password was not logged.')


if __name__ == '__main__':
    main()
