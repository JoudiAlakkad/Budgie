"""The evaluation CLI: `python -m eval.run_eval {check,run,score}` (wiki: backend/evaluation.md).

- `check` validates the ground truth.
- `run` calls the model for every configuration, case and run that has no matching cache
  entry, then scores everything. Each answer is cached right after its call, so a
  stopped run resumes; infrastructure errors aren't cached, so they are retried.
- `score` recomputes everything from the cache, without the model or the network.

Both `run` and `score` write `eval/results/` and rewrite only the generated block of the
report. Output names case ids and outcome codes only, never values.

A write is complete only for the default configurations x every case x every run, with
no missing or stale cache entry (infrastructure errors are never cached, so they count as
missing). Anything less writes results and the report only with `--partial`, marked
partial; only a complete write deletes other configurations' result files. `today` is pinned:
`--today`, else the `today` of the existing `summary.json`, else today in Berlin.

Exit codes: 0 done, 1 cases missing from the cache, 2 invalid input (ground truth, report
markers, arguments, Pillow missing), 3 the model server isn't ready.
"""

import argparse
import datetime as dt
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from app.ai.client import LLMClient
from app.ai.extractor import ExtractionResult
from app.ai.prompts import available_versions
from app.ai.schema import OUTPUT_SPECS
from app.config import Settings
from app.errors import ExtractionError
from app.services.dependencies import build_extractor
from app.services.item_categories import load_seed
from eval.cache import (
    CacheEntry,
    config_id,
    fingerprint,
    is_cacheable,
    load,
    remove,
    store,
    write_atomic,
)
from eval.dataset import Case, InvalidDataset, check_dataset, load_dataset
from eval.images import prepare_image
from eval.metrics import (
    DEFAULT_CONFIGS,
    Config,
    ConfigResult,
    Observation,
    aggregate,
    comparisons,
    label,
    observe,
    score_case,
)
from eval.report import MarkerError, render, replace_block
from eval.results import SUMMARY_FILE, write_results
from eval.scoring import example_items

BERLIN = ZoneInfo("Europe/Berlin")
EXIT_OK, EXIT_MISSING, EXIT_INPUT, EXIT_SERVER = 0, 1, 2, 3


class Extractor(Protocol):
    def extract(self, image: bytes, mime: str) -> ExtractionResult: ...


ExtractorFactory = Callable[[Settings], Extractor]


def today_in_berlin() -> dt.date:
    return dt.datetime.now(BERLIN).date()


# ---------------------------------------------------------------- arguments


def _config(text: str) -> Config:
    model, sep, prompt = text.rpartition("@")
    if not sep or not model or not prompt:
        raise argparse.ArgumentTypeError("use MODEL@PROMPT, e.g. gemma3:4b@v3")
    return model, prompt


def _csv(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eval.run_eval", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="validate the ground truth")
    check.add_argument("--data", type=Path, default=Path("data/eval"))
    for name in ("run", "score"):
        sub = commands.add_parser(name, help=f"{name} the evaluation")
        sub.add_argument("--config", type=_config, action="append", metavar="MODEL@PROMPT")
        sub.add_argument("--models", type=_csv, help="comma-separated; crossed with --prompts")
        sub.add_argument("--prompts", type=_csv, help="comma-separated; crossed with --models")
        sub.add_argument("--cases", type=_csv, help="comma-separated case ids, e.g. c02,c05")
        sub.add_argument("--runs", type=int, default=1)
        sub.add_argument("--data", type=Path, default=Path("data/eval"))
        sub.add_argument("--cache", type=Path, help="default: <data>/cache")
        sub.add_argument("--out", type=Path, default=Path("eval/results"))
        sub.add_argument("--report", type=Path, default=Path("docs/evaluation.md"))
        sub.add_argument("--no-report", action="store_true")
        sub.add_argument("--today", type=dt.date.fromisoformat, help="default: today in Berlin")
        sub.add_argument("--no-resize", action="store_true")
        sub.add_argument(
            "--partial",
            action="store_true",
            help="write results and the report even when case runs are missing",
        )
        if name == "run":
            sub.add_argument("--timeout", type=float, help="LLM_TIMEOUT_S for these calls")
            sub.add_argument("--force", action="store_true", help="ignore the cache")
    return parser


def configs_from(args: argparse.Namespace) -> list[Config]:
    if args.config:
        configs = list(args.config)
    elif args.models or args.prompts:
        models = args.models or sorted({model for model, _ in DEFAULT_CONFIGS})
        prompts = args.prompts or sorted({prompt for _, prompt in DEFAULT_CONFIGS})
        configs = [(model, prompt) for model in models for prompt in prompts]
    else:
        configs = list(DEFAULT_CONFIGS)
    return list(dict.fromkeys(configs))


def config_problem(configs: list[Config]) -> str | None:
    """Why these configurations can't run, or None: an unknown prompt version (no prompt
    folder or no output spec), or two configurations sharing one cache/result id."""
    known = sorted(set(available_versions()) & set(OUTPUT_SPECS))
    for config in configs:
        if config[1] not in known:
            return (
                f"Unknown prompt version {config[1]!r} in {label(config)}; "
                f"available: {', '.join(known)}"
            )
    seen: dict[str, Config] = {}
    for config in configs:
        other = seen.setdefault(config_id(*config), config)
        if other != config:
            return (
                f"{label(other)} and {label(config)} map to the same id "
                f"{config_id(*config)!r}; run them separately"
            )
    return None


def settings_for(config: Config, timeout: float | None = None) -> Settings:
    """Settings built directly, not the process-wide cached `get_settings()`."""
    model, prompt_version = config
    overrides: dict[str, Any] = {"llm_model": model, "prompt_version": prompt_version}
    if timeout is not None:
        overrides["llm_timeout_s"] = timeout
    return Settings(**overrides)


def client_for(settings: Settings, transport: httpx.BaseTransport | None = None) -> LLMClient:
    """As `services.dependencies.get_llm_client` builds it."""
    return LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout=settings.llm_timeout_s,
        transport=transport,
        max_retries=settings.llm_max_retries,
    )


def preflight(
    settings: Settings, models: Sequence[str], transport: httpx.BaseTransport | None = None
) -> str | None:
    """None if the server answers and lists every model, else a message for the user."""
    client = client_for(settings, transport)
    if not client.ping():
        return "The model server is unreachable at LLM_BASE_URL."
    try:
        with httpx.Client(
            headers={"Authorization": f"Bearer {client.api_key}"},
            timeout=min(10.0, settings.llm_timeout_s),
            transport=transport,
        ) as http:
            listed = http.get(f"{client.base_url}/models").json()
        available = {str(entry.get("id")) for entry in listed.get("data", [])}
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return "The model server is unreachable: GET /models gave no model list."
    names = available | {name.removesuffix(":latest") for name in available}
    missing = [model for model in models if model not in names]
    if missing:
        return "Models not available: " + ", ".join(f"run `ollama pull {m}`" for m in missing)
    return None


# ---------------------------------------------------------------- run and score


def _selected(cases: list[Case], ids: list[str] | None) -> list[Case]:
    if not ids:
        return cases
    wanted = set(ids)
    unknown = sorted(wanted - {case.id for case in cases})
    if unknown:
        raise InvalidDataset([f"{case_id}: no such case" for case_id in unknown])
    return [case for case in cases if case.id in wanted]


def _image(case: Case, data: Path, resize: bool) -> tuple[bytes, str]:
    return prepare_image(data / "images" / case.image, resize=resize)


def _fingerprint(settings: Settings, image: bytes, run: int) -> str:
    return fingerprint(
        model=settings.llm_model,
        prompt_version=settings.prompt_version,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        image=image,
        run=run,
    )


def _call(
    extractor: Extractor, image: bytes, mime: str, fp: str
) -> tuple[CacheEntry, str, str | None]:
    """One model call as a cache entry (cached or not), its outcome code and, for an
    unexpected exception, that exception's type name (never its message)."""
    try:
        result = extractor.extract(image, mime)
    except ExtractionError as exc:
        return CacheEntry.from_error(exc.code, exc.latency_s, fp), exc.code, None
    except Exception as exc:
        # A bug or a bad prompt version: `interrupted`, as the pipeline maps it.
        return CacheEntry.from_error("interrupted", None, fp), "interrupted", type(exc).__name__
    return CacheEntry.from_result(result, fp), "extracted", None


Images = dict[str, tuple[bytes, str]]


def prepare_images(cases: list[Case], data: Path, resize: bool) -> Images:
    """Each case's image as sent, prepared once per invocation."""
    return {case.id: _image(case, data, resize) for case in cases}


def run_models(
    args: argparse.Namespace,
    configs: list[Config],
    cases: list[Case],
    factory: ExtractorFactory | None,
    images: Images,
) -> tuple[int, int]:
    """Call the model where the cache has no matching entry; returns the number of calls
    and of infrastructure errors (not cached, so scoring sees them as missing, exactly
    as `score` does)."""
    infra = 0
    calls = 0
    for config in configs:
        settings = settings_for(config, args.timeout)
        if factory is not None:
            extractor = factory(settings)
        else:
            extractor = build_extractor(settings, client_for(settings))
        cid = config_id(*config)
        print(f"{label(config)}: {len(cases)} cases x {args.runs} run(s)")
        for case in cases:
            image, mime = images[case.id]
            for run in range(1, args.runs + 1):
                fp = _fingerprint(settings, image, run)
                if args.force:
                    # Gone before the call: an infrastructure error leaves it missing,
                    # not the old answer.
                    remove(args.cache, cid, case.id, run)
                    cached = None
                else:
                    cached = load(args.cache, cid, case.id, run)
                if cached is not None and cached.fingerprint == fp:
                    print(f"  {case.id} r{run}: cached ({cached.outcome})")
                    continue
                entry, outcome, cause = _call(extractor, image, mime, fp)
                calls += 1
                if is_cacheable(outcome):
                    store(args.cache, cid, case.id, run, entry)
                    print(f"  {case.id} r{run}: {outcome}")
                else:
                    infra += 1
                    why = f" ({cause})" if cause else ""
                    print(f"  {case.id} r{run}: {outcome}{why} (not cached, retried next time)")
    return calls, infra


def score_all(
    args: argparse.Namespace,
    configs: list[Config],
    cases: list[Case],
    images: Images,
) -> tuple[list[ConfigResult], dict[Config, list[Observation]], int]:
    """Score every configuration from the cache; no model, no network."""
    today: dt.date = args.today
    table = {name: (category, "seed") for name, category in load_seed().items()}
    results: list[ConfigResult] = []
    observed: dict[Config, list[Observation]] = {}
    missing_total = 0
    for config in configs:
        settings = settings_for(config)
        model, prompt_version = config
        cid = config_id(*config)
        examples = example_items(prompt_version)
        observations, scores, missing = [], [], 0
        for case in cases:
            for run in range(1, args.runs + 1):
                entry = load(args.cache, cid, case.id, run)
                fp = _fingerprint(settings, images[case.id][0], run)
                if entry is not None and entry.fingerprint == fp:
                    obs = observe(
                        case.id,
                        run,
                        entry,
                        model=model,
                        prompt_version=prompt_version,
                        today=today,
                        table=table,
                    )
                else:
                    missing += 1
                    continue
                observations.append(obs)
                scores.append(score_case(case, obs, examples))
        if not observations:
            print(f"{label(config)}: nothing in the cache, skipped")
            missing_total += missing  # every expected run of this configuration
            continue
        if missing:
            print(f"{label(config)}: {missing} case run(s) not in the cache (or stale)")
        missing_total += missing
        metrics = aggregate(scores, observations, missing=missing)
        results.append(ConfigResult(config, metrics, scores))
        observed[config] = observations
    return results, observed, missing_total


def _read_report(path: Path) -> str:
    """The report as it is on disk: `newline=""` keeps CRLF line ends. Raises `MarkerError`
    if it can't be read."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError as exc:
        raise MarkerError(f"the report can't be read ({type(exc).__name__})") from None


def _check_report(args: argparse.Namespace) -> None:
    """Fail early on bad markers, before any model call; raises `MarkerError`."""
    if not args.no_report:
        replace_block(_read_report(args.report), "")


def _update_report(path: Path, block: str) -> bool:
    """Replace the block in the report as it is now (it may have been edited during the
    run); False, and the file untouched, if its markers are broken by then."""
    try:
        doc = _read_report(path)
        new = replace_block(doc, block)
    except MarkerError as exc:
        print(f"Report not touched: {exc}")
        return False
    if new != doc:
        write_atomic(path, new)
    return True


def _finish(
    args: argparse.Namespace,
    results: list[ConfigResult],
    observed: dict[Config, list[Observation]],
    missing: int,
    full_scope: bool,
) -> int:
    """Write results and the report. Complete only for the default configurations, every
    case and no missing entry; anything less needs `--partial` and is marked partial."""
    if not results:
        print("Nothing to score.")
        return EXIT_MISSING
    complete = full_scope and not missing
    if not complete and not args.partial:
        reasons = []
        if missing:
            reasons.append(f"{missing} case run(s) missing from the cache")
        if not full_scope:
            reasons.append("not every default configuration and case was selected")
        print(f"Partial: {'; '.join(reasons)}. Results and report not written (use --partial).")
        return EXIT_MISSING
    summary = write_results(
        args.out,
        results,
        observed,
        comparisons({result.config: result for result in results}),
        today=args.today,
        runs=args.runs,
        partial=not complete,
    )
    print(f"Results written to {args.out}")
    if not args.no_report:
        if not _update_report(args.report, render(summary)):
            return EXIT_INPUT
        print(f"Report block updated in {args.report}")
    return EXIT_MISSING if missing else EXIT_OK


def pinned_today(args: argparse.Namespace) -> tuple[dt.date, str]:
    """`--today`, else the `today` of the existing summary (so a rescore keeps the date
    rules of the run), else today in Berlin; with where it came from."""
    if args.today is not None:
        return args.today, "--today"
    try:
        summary = json.loads((args.out / SUMMARY_FILE).read_text(encoding="utf-8"))
        return dt.date.fromisoformat(summary["today"]), f"{args.out / SUMMARY_FILE}"
    except (OSError, ValueError, KeyError, TypeError):
        return today_in_berlin(), "today in Europe/Berlin"


def main(
    argv: Sequence[str] | None = None, *, extractor_factory: ExtractorFactory | None = None
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        result = check_dataset(args.data)
        for problem in result.problems:
            print(problem)
        for warning in result.warnings:
            print(f"warning: {warning}")
        if result.problems:
            print(f"{len(result.problems)} problem(s) in {len(result.cases)} valid case(s)")
            return EXIT_INPUT
        print(f"{len(result.cases)} cases OK")
        return EXIT_OK

    if args.runs < 1:
        print("--runs must be at least 1")
        return EXIT_INPUT
    args.cache = args.cache or args.data / "cache"
    args.today, source = pinned_today(args)
    print(f"today = {args.today.isoformat()} (from {source})")
    configs = configs_from(args)
    if problem := config_problem(configs):
        print(problem)
        return EXIT_INPUT
    try:
        dataset = load_dataset(args.data)
        cases = _selected(dataset, args.cases)
    except InvalidDataset as exc:
        for problem in exc.problems:
            print(problem)
        return EXIT_INPUT
    full_scope = set(configs) == set(DEFAULT_CONFIGS) and len(cases) == len(dataset)
    try:
        _check_report(args)
    except MarkerError as exc:
        print(f"Report not touched: {exc}")
        return EXIT_INPUT

    if args.command == "run" and extractor_factory is None:
        message = preflight(settings_for(configs[0], args.timeout), [m for m, _ in configs])
        if message:
            print(message)
            return EXIT_SERVER
    images = prepare_images(cases, args.data, not args.no_resize)
    if args.command == "run":
        calls, infra = run_models(args, configs, cases, extractor_factory, images)
        print(f"{calls} model call(s), {infra} infrastructure error(s)")
    results, observed, missing = score_all(args, configs, cases, images)
    return _finish(args, results, observed, missing, full_scope)


if __name__ == "__main__":
    sys.exit(main())
