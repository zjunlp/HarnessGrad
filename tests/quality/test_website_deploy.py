"""Guard the platform Pages entry against publishing the historical redirect."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_platform_deployment_builds_website_and_overlays_pinned_record():
    workflow = (ROOT / '.github/workflows/deploy.yml').read_text()
    assert not (ROOT / '.github/workflows/site-redirect.yml').exists()
    assert 'branches: [main]' in workflow
    assert "- 'docs/contributors.html'" in workflow
    assert "- '.github/workflows/deploy.yml'" in workflow
    assert 'workflow_dispatch:' in workflow
    assert 'site-redirect/' not in workflow
    assert 'ref: website' in workflow and 'path: site' in workflow
    assert 'ref: ${{ github.sha }}' in workflow
    assert 'pnpm install --frozen-lockfile' in workflow
    build = workflow.index('pnpm exec astro build')
    overlay = workflow.index('cp platform-record/docs/contributors.html site/dist/contributors.html')
    check = workflow.index('pnpm --dir site check:links')
    assert build < overlay < check < workflow.index('actions/upload-pages-artifact@v3')
    assert 'pnpm build' not in workflow  # It checks links before the overlay exists.
    assert 'path: site/dist' in workflow
    assert 'group: pages' in workflow and 'cancel-in-progress: false' in workflow
    assert 'name: github-pages' in workflow
    assert 'actions/checkout@v4' in workflow
    assert 'actions/upload-pages-artifact@v3' in workflow
    assert 'actions/deploy-pages@v4' in workflow
