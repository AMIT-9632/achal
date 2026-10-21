# Sample runs (synthetic)

One labelled run per scenario from ACHAL's field simulator, seed 42, site `jharia-demo`.
**All data here is synthetic.** Each `<scenario>.jsonl.gz` holds one telemetry v1 message per
line (see `schemas/telemetry.v1.json`) including the `truth` labels; each
`<scenario>.manifest.json` records the seed, the event parameters drawn from it, onset and
failure times, row counts, and the command that regenerates the file exactly.

| File | Length | Sampling |
| --- | --- | --- |
| `normal.jsonl.gz` | 48 h | 5 min |
| `trough.jsonl.gz` | 21 days | 30 min |
| `pillar_collapse.jsonl.gz` | 48 h | 5 min, plus every blast and the collapse burst at 1 min |
| `faults.jsonl.gz` | 72 h | 5 min |
| `fire.jsonl.gz` | 96 h | 5 min |

Read them with `zcat file | head`, or in Python:

```python
from achal_sim.export import load_jsonl   # services/field-sim/src on the path
rows = list(load_jsonl("data/samples/pillar_collapse.jsonl.gz"))
```

Regenerate with `cd services/field-sim/src && python -m achal_sim.export --samples`. Full-resolution
runs (1 min) go to `data/runs/`, which is not committed. The model is described in
`docs/field-sim-model.md`; plots of these files are in `docs/img/`.
