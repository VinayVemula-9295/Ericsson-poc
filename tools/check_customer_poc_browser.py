"""Isolated local browser acceptance; no cloud connections or infrastructure writes."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from app_auth import password_hash


def main():
    populated = '--populated' in sys.argv
    output = root / '.cache' / ('provider-cards-browser' if populated else 'customer-poc-browser')
    output.mkdir(parents=True, exist_ok=True)
    password, salt = secrets.token_urlsafe(32), secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix='iipp-browser-') as data:
        env = dict(os.environ, DATA_DIR=data, AWS_EC2_METADATA_DISABLED='true', CONTROL_PLANE_ENABLED='false',
            CONTROL_PLANE_USERS_FILE='', IIPP_ADMIN_USERNAME='admin', IIPP_ADMIN_SALT=salt,
            IIPP_ADMIN_PASSWORD_HASH=password_hash(password, salt), ENABLE_TERRAFORM_WORKFLOW='false', ENABLE_APPROVAL_EXECUTION='false')
        entry = root / 'app.py'
        if populated:
            entry = Path(data) / 'browser_app.py'
            entry.write_text('import sys, runpy\nsys.path.insert(0, ' + repr(str(root)) + ')\nsys.path.insert(0, ' + repr(str(root / 'tools')) + ')\n'
                'import hierarchy_ui\nfrom dashboard_browser_fixture import snapshot\nhierarchy_ui.provider_snapshot = snapshot\n'
                'runpy.run_path(' + repr(str(root / 'app.py')) + ', run_name="__main__")\n', encoding='utf-8')
        proc = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(entry), '--server.port=18529',
            '--server.headless=true', '--browser.gatherUsageStats=false'], cwd=root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen('http://localhost:18529/_stcore/health', timeout=1)
                    break
                except OSError:
                    time.sleep(.2)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('http://localhost:18529')
                page.get_by_role('heading', name='Welcome back').wait_for()
                page.screenshot(path=str(output / 'login.png'))
                page.get_by_label('Username', exact=True).fill('admin')
                page.get_by_label('Password', exact=True).fill(password)
                page.get_by_role('button', name='Sign in', exact=True).click()
                page.get_by_role('heading', name='Connected Clouds', exact=True).wait_for()
                page.get_by_role('heading', name='Recent provisioning', exact=True).wait_for()
                assert page.locator('[data-testid="stException"]').count() == 0
                logos = page.locator('.provider-title').all_text_contents()
                assert [x.strip() for x in logos] == ['Amazon Web Services', 'Microsoft Azure', 'Google Cloud Platform'], repr(logos)
                assert not page.get_by_role('heading', name='Organization cloud expenditure', exact=True).count()
                assert not page.get_by_text('Workload telemetry', exact=True).count()
                for provider, gross in [('Azure', '64.20'), ('GCP', '48.60')]:
                    demo_card = page.locator('.st-key-summary-' + provider)
                    expect(demo_card.get_by_text('Demo data · Illustrative billing', exact=True).first).to_be_visible()
                    expect(demo_card.get_by_test_id('stMetricValue')).to_have_text('USD ' + gross)
                if populated:
                    aws = page.locator('.st-key-summary-AWS')
                    expect(aws.get_by_test_id('stMetricValue')).to_have_text('USD 100.00')
                    expect(aws.locator('.connected-spend')).to_contain_text('USD 10.00')
                    aws.locator('.root-trigger').hover()
                    expect(aws.locator('.root-tooltip')).to_be_visible()
                    expect(aws.locator('.root-tooltip')).to_contain_text('Second Organization')
                    aws.locator('.root-trigger').focus()
                    page.mouse.move(0, 0)
                    expect(aws.locator('.root-tooltip')).to_be_visible()
                    aws.get_by_role('button', name='View accounts ↗', exact=True).click()
                    expect(page.get_by_test_id('stDataFrame').first).to_be_visible()
                    assert not page.get_by_role('button', name='Explore hierarchy', exact=True).count()
                    page.screenshot(path=str(output / 'account-details.png'), full_page=True)
                page.screenshot(path=str(output / 'dashboard-desktop.png'), full_page=True)
                header = page.locator('.st-key-platform-header')
                before = header.bounding_box()
                assert abs(before['y']) < 1, before
                page.get_by_test_id('stMain').evaluate('(element) => element.scrollTo(0, 850)')
                page.wait_for_timeout(500)
                after = header.bounding_box()
                assert abs(before['y'] - after['y']) < 4, f'Header moved: {before} -> {after}'
                page.get_by_role('button', name='View subscriptions ↗', exact=True).click()
                page.get_by_role('heading', name='Azure subscriptions', exact=True).wait_for()
                page.get_by_text('No visible subscriptions.', exact=False).wait_for()
                page.get_by_role('button', name='Configure Cloud', exact=True).click()
                page.get_by_role('dialog').wait_for()
                page.get_by_role('dialog').get_by_role('button', name='Close', exact=True).last.click()
                page.get_by_role('dialog').wait_for(state='hidden')
                page.get_by_test_id('stSidebar').get_by_text('Deployment', exact=True).click()
                page.get_by_role('heading', name='Deployment history', exact=True).wait_for()
                assert not page.get_by_role('heading', name='Deployment workflow', exact=True).count()
                page.get_by_test_id('stSidebar').get_by_text('Provisioning Agent', exact=True).click()
                page.get_by_role('button', name='Evaluate', exact=True).wait_for()
                page.get_by_text('Reuse previous deployment', exact=True).click()
                assert page.locator('[data-testid="stException"]').count() == 0
                page.screenshot(path=str(output / 'reuse-empty.png'), full_page=True)
                page.get_by_test_id('stSidebar').get_by_text('Dashboard', exact=True).click()
                page.get_by_role('heading', name='Connected Clouds', exact=True).wait_for()
                for width, height in [(1280, 800), (768, 1024), (390, 844)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    page.wait_for_timeout(500)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}'
                    assert abs(header.bounding_box()['y']) < 1
                    boxes = [page.locator('.st-key-summary-' + provider).bounding_box() for provider in ('AWS', 'Azure', 'GCP')]
                    if width <= 1000:
                        assert all(abs(box['x'] - boxes[0]['x']) < 1 for box in boxes), f'Cards did not stack at {width}'
                    if populated and width == 390:
                        trigger = page.locator('.st-key-summary-AWS .root-trigger')
                        trigger.click()
                        expect(page.locator('.st-key-summary-AWS .root-tooltip')).to_be_visible()
                    page.screenshot(path=str(output / f'dashboard-{width}.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.get_by_role('button', name='A', exact=True).click()
                assert not page.get_by_role('button', name='Infrastructure & Monitoring', exact=True).count()
                assert not page.get_by_text('Demo profile', exact=False).count()
                page.get_by_role('button', name='Sign out', exact=True).click()
                page.get_by_role('heading', name='Welcome back', exact=True).wait_for()
                assert not errors, errors
                browser.close()
                print('PASS: secure login/logout, provider order, empty hierarchy, configuration, deployment/agent/reuse navigation, fixed header, responsive layouts, no browser exceptions.')
        finally:
            proc.terminate()
            proc.wait(timeout=15)


if __name__ == '__main__':
    main()
