import pytest
import cloud_regions as regions


def table(headers, rows):
    return '<table><tr>' + ''.join(f'<th>{h}</th>' for h in headers) + '</tr>' + ''.join(
        '<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows) + '</table>'


@pytest.mark.parametrize('provider,headers,rows,code', [
    ('AWS', ['Code', 'Name'], [('ap-south-1', 'Mumbai'), ('cn-north-1', 'China'), ('us-gov-west-1', 'GovCloud')], 'ap-south-1'),
    ('Azure', ['Region', 'Programmatic name'], [('Central India', 'centralindia'), ('Announced', 'newregion')], 'centralindia'),
    ('GCP', ['Zones', 'Location'], [('asia-south1-b', 'Mumbai'), ('asia-south1-c', 'Mumbai')], 'asia-south1'),
])
def test_official_table_codes_dedup_and_zones(provider, headers, rows, code):
    result = regions.parse_catalogue(provider, table(headers, rows + rows))
    assert [r['value'] for r in result] == [code]
    if provider == 'GCP':
        assert result[0]['zones'] == ['asia-south1-b', 'asia-south1-c']


def test_missing_or_changed_table_fails_closed():
    with pytest.raises(ValueError):
        regions.parse_catalogue('AWS', '<html>Service unavailable</html>')


def test_successful_reads_cached_and_failed_reads_not_cached(monkeypatch):
    from io import BytesIO
    calls = []
    def fetch(request, timeout):
        calls.append((request.full_url, timeout))
        if len(calls) == 1:
            raise OSError('Temporary failure')
        data = BytesIO(table(['Code', 'Name'], [('ap-south-1', 'Mumbai')]).encode())
        data.status = 200
        return data
    monkeypatch.setattr(regions.urllib.request, 'urlopen', fetch)
    regions.load_catalogue.clear()
    try:
        with pytest.raises(OSError):
            regions.load_catalogue('AWS')
        result = regions.load_catalogue('AWS')
        assert regions.load_catalogue('AWS') == result
        assert len(calls) == 2
        assert calls[0] == (regions.SOURCES['AWS'], 15)
        assert result['retrieved_at'] and result['source'] == regions.SOURCES['AWS']
    finally:
        regions.load_catalogue.clear()
