# Contributing

Run `./scripts/setup.sh` for a release build, tests and demonstration. For checked builds run `./scripts/verify.sh`; set `ASTERBOOK_RUN_FUZZ=1` when Clang's libFuzzer runtime is installed. To run the larger deterministic differential suite:

```sh
python3 reference/differential.py --server "$PWD/build/release/asterbook" --seeds 100 --count 1000
```

Optional formatters can be installed locally with `python3 -m venv .venv` followed by `.venv/bin/python -m pip install -r requirements-dev.txt`. Format C++ with `.venv/bin/clang-format -i` and Python with `.venv/bin/black` on the files changed. Keep generated builds, runtime data and local environments out of commits. Preserve reproducible seeds and document measurement boundaries when adding tests or benchmarks.
