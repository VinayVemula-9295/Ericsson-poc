"""Read-only access validation; prints status/counts, never secrets or resource payloads."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', required=True)
    parser.add_argument('--username', help='Existing control-plane principal for this trusted local operator')
    parser.add_argument('--inventory', action='store_true', help='Scan inventory across enabled regions')
    args = parser.parse_args()
    import control_plane as cp
    from multicloud_dashboard import query, identity
    from provider_connections import get_connection
    with cp.identity(args.username):
        c = get_connection(args.connection)
        if not c:
            parser.error('Connection unavailable')
        sections = [('hierarchy', None), ('cost', identity(c))]
        if args.inventory:
            sections.append(('resources', identity(c)))
        if (c.get('metadata') or {}).get('billing_scope') == 'organization':
            sections.append(('organization_cost', c['metadata'].get('discovery_boundary')))
        for section, target in sections:
            result = query(args.connection, section, target, refresh=True)
            print(json.dumps(dict(section=section, status=result['status'], records=len(result['data']),
                last_successful_sync=result['last_successful_sync'], errors=result['errors'])))


if __name__ == '__main__':
    main()
