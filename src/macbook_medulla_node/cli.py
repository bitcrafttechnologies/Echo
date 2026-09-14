from __future__ import annotations

from collections.abc import Sequence

from macbook_medulla_node.config import load_macbook_runtime
from medulla_node.package_cli import package_main


TEMPLATE = """protocol: medulla/1
node:
  id: my-macbook
  name: My MacBook
  type: computer
provider:
  id: my-macbook
transport:
  type: websocket
metadata:
  implementation: macbook-medulla-node
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
macbook:
  file_roots:
    - ./Documents
  telemetry_interval: 60
  maximum_file_bytes: 262144
  location:
    latitude: 33.4484
    longitude: -112.074
    label: Phoenix
"""


def main(argv: Sequence[str] | None = None) -> int:
    return package_main(argv, program="macbook-medulla-node", description="Read-only local MacBook Medulla Node", template=TEMPLATE, factory=load_macbook_runtime)


if __name__ == "__main__": raise SystemExit(main())
