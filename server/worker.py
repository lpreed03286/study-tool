"""The process that does all the math.

The server stays fast and free of SymPy by talking to this over JSON lines. A
hung simplify or dsolve can be killed here without touching saved progress.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import documents  # noqa: E402
from grade import grade  # noqa: E402
from methods import NotApplicable, all_methods, load_repertoire_module  # noqa: E402
from verify import VerificationFailed, build_and_verify  # noqa: E402

MIN_SAMPLES = 2


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(folder: Path) -> dict:
    manifest = folder / "manifest.json"
    return json.loads(manifest.read_text()) if manifest.exists() else {}


def validated_paths(folder: Path) -> list[Path]:
    """Only files that passed add_method, and only while they are unchanged."""
    paths = []
    for entry in read_manifest(folder).values():
        path = folder / entry["file"]
        if path.exists() and file_hash(path) == entry["sha256"]:
            paths.append(path)
    return paths


def methods_for(folder: str):
    return all_methods(validated_paths(Path(folder)))


def op_grade(args: dict) -> dict:
    return grade(args["check"], args["latex"], args.get("rename"), args.get("answer", "")).to_dict()


def op_build(args: dict) -> dict:
    methods = methods_for(args["repertoire"])
    method = methods.get(args["method"])
    if method is None:
        return {"ok": False, "error": f"no method named {args['method']!r} in the repertoire"}
    try:
        return {"ok": True, "plan": build_and_verify(method, args["spec"], args.get("notation") or {})}
    except NotApplicable as reason:
        return {"ok": False, "error": f"{method.name} doesn't apply: {reason}"}
    except VerificationFailed as reason:
        return {"ok": False, "error": f"the answer key failed verification: {reason}"}


def op_list_methods(args: dict) -> dict:
    folder = Path(args["repertoire"])
    good = {p.name for p in validated_paths(folder)}
    stale = [e["file"] for e in read_manifest(folder).values() if e["file"] not in good]
    return {"methods": [m.summary() for m in methods_for(args["repertoire"]).values()],
            "changed_since_validation": stale}


def op_add_method(args: dict) -> dict:
    """Trial-run a new method. It joins the repertoire only if every sample passes."""
    folder = Path(args["repertoire"])
    path = (folder / args["file"]).resolve()
    if path.parent != folder.resolve() or path.suffix != ".py" or not path.exists():
        return {"ok": False, "error": f"expected a .py file directly in {folder}"}
    try:
        method = load_repertoire_module(path)
    except Exception as error:
        return {"ok": False, "error": f"couldn't load it: {type(error).__name__}: {error}"}
    if any(m.id == method.id for m in all_methods().values()):
        return {"ok": False, "error": f"{method.id} is already a built-in method"}
    if len(method.samples) < MIN_SAMPLES:
        return {"ok": False, "error": f"it needs at least {MIN_SAMPLES} sample problems to be tested on"}
    reports = []
    for sample in method.samples:
        try:
            plan = build_and_verify(method, sample, {})
        except (NotApplicable, VerificationFailed) as reason:
            return {"ok": False, "error": f"sample {sample['statement']!r} failed: {reason}", "passed": reports}
        except Exception as error:
            return {"ok": False, "passed": reports,
                    "error": f"sample {sample['statement']!r} crashed: {type(error).__name__}: {error}"}
        undeclared = {s["type"] for s in plan["steps"]} - set(method.step_types)
        if undeclared:
            return {"ok": False, "error": f"step types not listed in step_types: {sorted(undeclared)}"}
        reports.append({"statement": sample["statement"], "verified": plan["verified"]})
    manifest = read_manifest(folder)
    manifest[method.id] = {"file": path.name, "sha256": file_hash(path), "validated_at": time.time(), "report": reports}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return {"ok": True, "method": method.summary(), "report": reports}


def op_doc_build(args: dict) -> dict:
    return documents.build_blocks(args["blocks"], methods_for(args["repertoire"]), args.get("book_methods") or {})


OPS = {"grade": op_grade, "build": op_build, "list_methods": op_list_methods, "add_method": op_add_method,
       "doc_build": op_doc_build}


def main() -> None:
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        try:
            reply = {"id": request["id"], "result": OPS[request["op"]](request["args"])}
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            reply = {"id": request["id"], "error": f"{type(error).__name__}: {error}"}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
