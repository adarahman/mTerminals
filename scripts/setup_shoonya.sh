#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sdk_dir="${project_dir}/runtime/shoonya_api"

if [[ ! -f "${sdk_dir}/api_helper.py" ]]; then
  git clone --depth 1 https://github.com/Shoonya-Dev/ShoonyaApi-py.git "${sdk_dir}"
fi

(cd "${sdk_dir}" && "${project_dir}/.venv/bin/python" -m pip install -r requirements.txt)
"${project_dir}/.venv/bin/python" -c "import sys; sys.path.insert(0, '${sdk_dir}'); from api_helper import ShoonyaApiPy; print('Shoonya SDK ready')"
