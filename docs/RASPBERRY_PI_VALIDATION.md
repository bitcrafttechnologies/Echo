# Raspberry Pi Medulla Node validation

This runbook is the physical Phase 9I-H acceptance procedure. Automated tests
use the same `GpioAdapter` contract with an injected backend; completing this
runbook on a Pi is required before recording a real-hardware pass.

## Wiring

- Connect an LED through an appropriate resistor between BCM GPIO 17 and
  ground.
- Connect a normally-open push button between BCM GPIO 27 and ground. The
  gpiozero backend supplies the input pull-up.
- Use BCM numbering. Confirm the selected pins are safe for the exact Pi and
  attached hardware before applying power.

## Node installation and launch

Copy `examples/raspberry_pi_medulla_node.yaml` to the Pi, replace the Echo IP,
then install the GPIO extra and run:

```console
python -m pip install 'echo-runtime[gpio,websocket]'
medulla-node validate raspberry_pi_medulla_node.yaml
medulla-node status raspberry_pi_medulla_node.yaml --gpio-output 17 --gpio-input 27
medulla-node run raspberry_pi_medulla_node.yaml --gpio-output 17 --gpio-input 27
```

The node does not import or start Echo, an Entity, or an LLM. GPIO code is
loaded only because the operator supplied explicit GPIO flags.

## Echo acceptance

1. Confirm Echo emits `NodeAvailable`, `NodeCompatible`, and
   `NodeApprovalRequested` and shows the advertised GPIO inventory.
2. Before approval, attempt `gpio.output.set`; it must fail without changing
   GPIO 17 and the node must not be in the capability registry.
3. Approve the node explicitly and authorize only `identity.basic`.
4. Confirm `Workshop Pi connected.` and `ACTIVE`.
5. Dispatch `gpio.output.set` with resource `gpio.pin.17` and
   `{"enabled": true}`. Visually confirm the LED turns on.
6. Press the GPIO 27 button and confirm Echo receives `gpio.input.changed`
   with node, provider, adapter, resource, timestamp, and sequence provenance.
7. Disconnect the Pi, confirm `NodeDisconnected` and `NodeUnavailable`, then
   reconnect and confirm `NodeReconnected` followed by a fresh authorization
   gate.

Record the Pi model, OS, Python version, gpiozero version, wiring, Echo host,
timestamps, operator, and pass/fail evidence. Do not mark physical validation
complete from the injected-backend test alone.
