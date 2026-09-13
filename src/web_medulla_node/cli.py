from __future__ import annotations

from collections.abc import Sequence

from medulla_node.package_cli import package_main
from web_medulla_node.config import load_web_runtime


TEMPLATE = """protocol: medulla/1
node:
  id: open-web
  name: Open Web Research
  type: service
provider:
  id: open-web
transport:
  type: websocket
metadata:
  implementation: web-medulla-node
  version: 0.1
discovery:
  approval_mode: manual
echo:
  transport: websocket
  endpoint: ws://127.0.0.1:8765/medulla
connection_requirements: {}
capabilities: []
signals: []
resources: []
health:
  supported: true
web:
  allowed_domains:
    - github.com
    - ocw.mit.edu
    - arxiv.org
    - openstax.org
    - doaj.org
    - gutenberg.org
    - wikinews.org
  maximum_bytes: 524288
"""


def main(argv: Sequence[str] | None = None) -> int:
    return package_main(argv, program="web-medulla-node", description="Allowlisted Web Research Medulla Node", template=TEMPLATE, factory=load_web_runtime)


if __name__ == "__main__": raise SystemExit(main())
