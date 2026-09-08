# Echo

Echo is a lightweight, persistent, event-driven runtime for intelligent
entities. The implementation currently targets the kernel milestone described
in `Echo_Plan.md` and intentionally has no LLM, web, ROS, or semantic-memory
dependency.

## Development

Echo requires Python 3.11 or newer and uses only the standard library at
runtime.

```console
python -m unittest discover -s tests -v
```

