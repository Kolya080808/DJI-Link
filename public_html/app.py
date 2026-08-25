from __future__ import annotations

import os
import platform
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from flask import Flask, jsonify, redirect, render_template, send_from_directory

ROOT = Path(__file__).resolve().parent
REPO = os.getenv('DJI_LINK_REPO', 'Kolya080808/DJI-Link')
GITHUB_API = f'https://api.github.com/repos/{REPO}/releases/latest'
GITHUB_RELEASES = f'https://github.com/{REPO}/releases'

app = Flask(__name__, static_folder='static', template_folder='templates')
app.url_map.strict_slashes = False
application = app  # WSGI entry point used by Timeweb mod_wsgi

FALLBACK_ASSETS = {
    'windows-x64-msi': f'https://github.com/{REPO}/releases/latest/download/dji-link-windows-x64.msi',
    'windows-x86-msi': f'https://github.com/{REPO}/releases/latest/download/dji-link-windows-x86.msi',
    'windows-arm64-msi': f'https://github.com/{REPO}/releases/latest/download/dji-link-windows-arm64.msi',
    'windows-x64-zip': f'https://github.com/{REPO}/releases/latest/download/dji-link-windows-x64.zip',
    'macos-arm64-dmg': f'https://github.com/{REPO}/releases/latest/download/dji-link-macos-arm64.dmg',
    'macos-x86_64-dmg': f'https://github.com/{REPO}/releases/latest/download/dji-link-macos-x86_64.dmg',
    'macos-arm64-tgz': f'https://github.com/{REPO}/releases/latest/download/dji-link-macos-arm64.tar.gz',
    'macos-x86_64-tgz': f'https://github.com/{REPO}/releases/latest/download/dji-link-macos-x86_64.tar.gz',
    'linux-x86_64-deb': f'https://github.com/{REPO}/releases/latest/download/dji-link-linux-x86_64.deb',
    'linux-arm64-deb': f'https://github.com/{REPO}/releases/latest/download/dji-link-linux-arm64.deb',
    'linux-x86_64-rpm': f'https://github.com/{REPO}/releases/latest/download/dji-link-linux-x86_64.rpm',
    'linux-arm64-rpm': f'https://github.com/{REPO}/releases/latest/download/dji-link-linux-arm64.rpm',
    'linux-x86_64-tgz': f'https://github.com/{REPO}/releases/latest/download/dji-link-linux-x86_64.tar.gz',
    'linux-arm64-tgz': f'https://github.com/{REPO}/releases/latest/download/dji-link-linux-arm64.tar.gz',
    'pi-installer': f'https://github.com/{REPO}/releases/latest/download/install-pi.sh',
    'pi-bundle': f'https://github.com/{REPO}/releases/latest/download/dji-link-pi.tar.gz',
}


def asset_key(name: str) -> str | None:
    names = {
        'dji-link-windows-x64.msi': 'windows-x64-msi',
        'dji-link-windows-x86.msi': 'windows-x86-msi',
        'dji-link-windows-arm64.msi': 'windows-arm64-msi',
        'dji-link-windows-x64.zip': 'windows-x64-zip',
        'dji-link-macos-arm64.dmg': 'macos-arm64-dmg',
        'dji-link-macos-x86_64.dmg': 'macos-x86_64-dmg',
        'dji-link-macos-arm64.tar.gz': 'macos-arm64-tgz',
        'dji-link-macos-x86_64.tar.gz': 'macos-x86_64-tgz',
        'dji-link-linux-x86_64.deb': 'linux-x86_64-deb',
        'dji-link-linux-arm64.deb': 'linux-arm64-deb',
        'dji-link-linux-x86_64.rpm': 'linux-x86_64-rpm',
        'dji-link-linux-arm64.rpm': 'linux-arm64-rpm',
        'dji-link-linux-x86_64.tar.gz': 'linux-x86_64-tgz',
        'dji-link-linux-arm64.tar.gz': 'linux-arm64-tgz',
        'install-pi.sh': 'pi-installer',
        'dji-link-pi.tar.gz': 'pi-bundle',
    }
    return names.get(name.lower())


def latest_release() -> dict:
    try:
        request = Request(
            GITHUB_API,
            headers={
                'Accept': 'application/vnd.github+json',
                'User-Agent': 'DJI-Link-Website',
            },
        )
        with urlopen(request, timeout=6) as response:
            if response.status >= 400:
                raise RuntimeError(f'GitHub API returned HTTP {response.status}')
            data = json.load(response)
        assets = {}
        for item in data.get('assets', []):
            key = asset_key(item.get('name', ''))
            if key:
                assets[key] = {
                    'name': item.get('name'),
                    'url': item.get('browser_download_url'),
                    'size': item.get('size'),
                    'download_count': item.get('download_count', 0),
                }
        return {
            'ok': True,
            'tag': data.get('tag_name'),
            'name': data.get('name') or data.get('tag_name'),
            'body': data.get('body') or '',
            'published_at': data.get('published_at'),
            'prerelease': bool(data.get('prerelease')),
            'html_url': data.get('html_url') or GITHUB_RELEASES,
            'assets': assets,
        }
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError, OSError) as exc:
        return {
            'ok': False,
            'tag': 'Latest release',
            'name': 'Latest release',
            'body': '',
            'published_at': None,
            'prerelease': False,
            'html_url': GITHUB_RELEASES,
            'assets': {},
            'error': str(exc),
        }


def merged_assets() -> dict[str, dict]:
    release = latest_release()
    assets = {
        key: {'name': key, 'url': url, 'size': None, 'download_count': None}
        for key, url in FALLBACK_ASSETS.items()
    }
    assets.update(release.get('assets', {}))
    return assets


@app.context_processor
def inject_globals():
    try:
        static_version = str(max(int(path.stat().st_mtime) for path in (ROOT / 'static').iterdir()))
    except (FileNotFoundError, ValueError):
        static_version = '1'
    return {
        'repo': REPO,
        'github_url': f'https://github.com/{REPO}',
        'wiki_url': f'https://github.com/{REPO}/wiki',
        'releases_url': GITHUB_RELEASES,
        'year': 2026,
        'static_version': static_version,
    }


@app.route('/')
def index():
    return render_template('index.html', page='home')


@app.route('/downloads')
def downloads():
    return render_template('downloads.html', page='downloads')


@app.route('/docs')
def docs():
    return redirect("https://github.com/Kolya080808/DJI-Link/wiki/")


@app.route('/license')
def license_page():
    return render_template('license.html', page='license')


@app.route('/authors')
def authors():
    return render_template('authors.html', page='authors')


@app.route('/contribute')
def contribute():
    return render_template('contribute.html', page='contribute')


@app.route('/index.html')
def legacy_index():
    return redirect('/', code=301)


@app.route('/<page>.html')
def legacy_page(page: str):
    destinations = {
        'downloads': '/downloads',
        'docs': '/docs',
        'license': '/license',
        'authors': '/authors',
        'contribute': '/contribute',
    }
    destination = destinations.get(page)
    if destination is None:
        return ('Not found', 404)
    return redirect(destination, code=301)


@app.route('/api/release')
def api_release():
    data = latest_release()
    data['repo'] = REPO
    return jsonify(data)


@app.route('/api/system')
def api_system():
    return jsonify({
        'os': platform.system(),
        'release': platform.release(),
        'machine': platform.machine(),
        'python': platform.python_version(),
    })


@app.route('/download/<asset>')
def download(asset: str):
    assets = merged_assets()
    item = assets.get(asset)
    if not item or not item.get('url'):
        return ('Unknown download', 404)
    return redirect(item['url'], code=302)


@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory(app.static_folder, path)


@app.route('/healthz')
def healthz():
    return jsonify({'status': 'ok'})


@app.route('/favicon.ico')
def favicon():
    return redirect('/static/logo.svg', code=302)


if __name__ == '__main__':
    port = int(os.getenv('PORT', '5000'))
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_DEBUG') == '1')
