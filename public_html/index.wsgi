import os
import sys

# Timeweb virtual hosting: Apache + mod_wsgi.
# Exact project location provided for this deployment:
# /home/c/cd58696/dji-link-site/public_html
PROJECT_ROOT = '/home/c/cd58696/dji-link-site/public_html'

# Expected virtualenv location. The first path is the one used by the project
# layout; the second is a safe fallback if the venv was created in the account root.
VENV_CANDIDATES = [
    '/home/c/cd58696/dji-link-site/venv',
    '/home/c/cd58696/venv',
]

sys.path.insert(0, PROJECT_ROOT)

for venv_root in VENV_CANDIDATES:
    site_packages = os.path.join(venv_root, 'lib', 'python3.10', 'site-packages')
    if os.path.isdir(site_packages):
        sys.path.insert(0, site_packages)
        os.environ.setdefault('VIRTUAL_ENV', venv_root)
        os.environ['PATH'] = os.path.join(venv_root, 'bin') + ':' + os.environ.get('PATH', '')
        break

from app import application
