"""Unified CLI for the harmo pipeline.

Usage::

    harmo validate --config <path>
    harmo ingest   --config <path> [--dry-run]
    harmo ingest-session-hed --config <path> [--hed-json <path>] [--dry-run]
    harmo ingest-bundle-semantics --bundle <recording.bundle.json> [--dry-run]
    harmo edit-markers [--model MODEL.osim] [--output OUTPUT.osim] [--geometry DIR]
    harmo analyze  --config <path> [--input FILE] [--label LABEL]
    harmo viz
    harmo init     --type {c3d,mat,csv} <dir>
    harmo r-analyze [<script>] [--check-env] [--setup-posit]
    harmo audit --manifest <run.manifest.json>
    harmo reproduce --expected <contract.json> --actual <contract.json>
    harmo export-atlas --bundle <bundle.json> --manifest <manifest.json> --trust <trust.json> --comparability <assertion.json> --release-decision <decision.json> --output <dir>
    harmo resolve-comparability --left-bundle <bundle.json> --right-bundle <bundle.json> ...
    harmo trust-opensim --validation <validation.json> --model <model.osim> ...
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _setup_logging(verbose=False):
    # type: (bool) -> None
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _cmd_validate(args):
    # type: (argparse.Namespace) -> int
    """Validate a study config.yaml."""
    from harmo.config.loader import load_config
    from harmo.config.hed import load_primary_hed_definition, validate_hed_schema
    from harmo.config.schema import validate_config
    from harmo.config.validator import validate_study

    try:
        config = load_config(args.config)
    except Exception as exc:
        print("ERROR: Failed to load config: {}".format(exc))
        return 1

    errors = validate_config(config)
    errors.extend(validate_study(config))

    hed_dict, hed_def_key, hed_load_errors = load_primary_hed_definition(config)
    errors.extend(hed_load_errors)
    if hed_dict is not None:
        hed_errors = validate_hed_schema(hed_dict, version="8.4.0")
        if hed_errors:
            errors.extend([
                "HED schema validation (definition '{}'): {}".format(
                    hed_def_key or "?",
                    msg,
                )
                for msg in hed_errors
            ])
        else:
            print("OK: HED Schema v8.4.0 validation passed.")

    if not errors:
        print("OK: config validation passed.")
        return 0

    for err in errors:
        marker = "WARNING" if "not found" in err else "ERROR"
        print("{}: {}".format(marker, err))

    n_errors = len([e for e in errors if "jsonschema is not installed" not in e])
    if n_errors > 0:
        print("\n{} issue(s) found.".format(n_errors))
        return 1
    return 0


def _cmd_mixed_batch(args):
    import json
    from harmo.mixed_batch import run
    try:
        result = run(json.loads(Path(args.urls).read_text()),args.output,args.r_library,args.max_bytes,args.max_records,
                     json.loads(Path(args.selection_request).read_text()) if args.selection_request else None)
        print(json.dumps({"status":result["status"],"report":result["run_dir"]},indent=2))
        return 0 if result["status"] == "completed-with-use-holds" else 2
    except (ValueError,OSError,TypeError) as exc:
        print("ERROR: {}".format(exc))
        return 1


def _cmd_timing_package(args):
    import json
    import zipfile
    from harmo import timing_package
    try:
        if args.action=='create':
            if not all((args.workflow,args.source_cache,args.protocol,args.article)):
                raise ValueError('workflow_source_cache_protocol_article_required')
            result=timing_package.create(args.workflow,args.source_cache,args.protocol,args.article,args.output)
        else:
            if not args.package:raise ValueError('package_required')
            result=timing_package.restore(args.package,args.output,args.resume)
        print(json.dumps({k:v for k,v in result.items() if k!='comparisons'},indent=2))
        return 0 if result['status'] in ('packaged','completed') else 2
    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile) as exc:
        print('ERROR: '+str(exc));return 1


def _cmd_timing_run(args):
    import json
    from harmo.planned_timing_workflow import run
    try:
        result=run(args.catalog,args.source_cache,args.protocol,args.article,args.output)
        print(json.dumps(result,indent=2))
        return 0 if result['status']=='completed' else 2
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('ERROR: '+str(exc));return 1


def _cmd_timing_plan(args):
    import json
    from harmo.timing_planner import plan,execute
    try:
        if args.catalog:
            from harmo.catalog_timing_plan import prepare
            if not args.record or not args.channel:raise ValueError('record_and_channel_required')
            result=prepare(args.catalog,args.record,args.channel,args.protocol,args.output,args.assertions)
        else:
            result=plan(json.loads(Path(args.input).read_text()),args.protocol)
            with Path(args.output).open('x') as target:json.dump(result,target,indent=2)
        if args.execute_output and result['status']=='planned':execute(result,args.execute_output)
        print(json.dumps(dict(status=result['status'],steps=len(result['steps']),reasons=result['reasons'])))
        return 0 if result['status']=='planned' else 2
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('ERROR: '+str(exc));return 1


def _cmd_contract_revision(args):
    import json
    from harmo import contract_revision
    try:
        if args.action=='prepare':
            if not args.record or not args.output:raise ValueError('--record and --output required')
            result=contract_revision.prepare(args.catalog,args.record,args.output)
        else:
            if not args.proposal:raise ValueError('--proposal required')
            result=contract_revision.apply(args.catalog,args.proposal)
        print(json.dumps(result,indent=2));return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('ERROR: '+str(exc));return 1


def _cmd_review_session(args):
    import json
    from harmo import review_session
    try:
        if args.action=='prepare':
            if not args.output: raise ValueError('--output required')
            result=review_session.prepare(args.catalog,args.output)
        elif args.action=='apply':
            if not args.packet or not args.decisions: raise ValueError('--packet and --decisions required')
            result=review_session.apply(args.catalog,args.packet,args.decisions)
        else:
            if not args.output or not args.purpose: raise ValueError('--output and --purpose required')
            result=review_session.reassess(args.catalog,args.output,args.purpose,args.max_comparisons)
        print(json.dumps(result,indent=2));return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('ERROR: '+str(exc));return 1


def _cmd_unit_review(args):
    import json
    from harmo.unit_review_store import save_review, load_review, decide
    try:
        if args.action == 'import':
            if not args.queue: raise ValueError('--queue required')
            result = dict(saved=save_review(args.catalog,json.loads(Path(args.queue).read_text())))
        elif args.action == 'show':
            result = load_review(args.catalog)
        else:
            if not all((args.record,args.channel,args.reviewer,args.reason)):
                raise ValueError('--record --channel --reviewer --reason required')
            result = decide(args.catalog,args.record,args.channel,args.action,args.reviewer,args.reason)
        print(json.dumps(result,indent=2)); return 0
    except (ValueError,OSError,TypeError,KeyError) as exc:
        print('ERROR: '+str(exc)); return 1


def _cmd_automatic(args):
    import json
    from harmo.automatic_workflow import run
    try:
        result = run(json.loads(Path(args.urls).read_text()),args.output,args.purpose,args.r_library,
                     args.max_bytes,args.max_records,args.max_comparisons,
                     local_llm_model=args.local_llm_model,local_llm_port=args.local_llm_port,max_unit_channels=args.max_unit_channels,
                     timing_profile=args.timing_profile,source_cache=args.source_cache,timing_protocol=args.timing_protocol,
                     article=args.article,download=args.download,resume=args.resume)
        print(json.dumps(dict(status=result['status'],exported=result['assessment']['exported'],report=result['intake']['run_dir'])))
        if args.timing_profile=='study38-gait120-planned':
            return 0 if result['status']=='completed' else 2
        return 0 if result['status']=='completed-with-holds' else 2
    except (ValueError,OSError,TypeError) as exc:
        print('ERROR: '+str(exc)); return 1


def _cmd_reuse_run(args):
    import json
    from harmo.reuse_workflow import run
    try:
        result = run(json.loads(Path(args.urls).read_text()), args.source_cache,
                     args.protocol, args.article, args.output, args.resume,
                     args.download, args.max_bytes)
        print(json.dumps({"status": result["status"], "timing": result["timing"]}, indent=2))
        return 0 if result["status"] == "completed" else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("ERROR: {}".format(exc))
        return 1


def _cmd_public_export(args):
    import json
    from harmo.public_export import export_catalog
    try:
        result = export_catalog(args.catalog, json.loads(Path(args.request).read_text()), args.output)
        print(json.dumps({"status":result["status"], "exported":result["exported"]}))
        return 0 if result["exported"] else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("ERROR: {}".format(exc))
        return 1


def _cmd_public_batch(args):
    import json
    from harmo.public_batch import run_batch
    try:
        urls = json.loads(Path(args.urls).read_text())
        result = run_batch(urls, args.output, args.r_library, args.max_bytes, args.max_records)
        if getattr(args, "selection_request", None):
            from harmo.public_export import export_catalog
            selection = export_catalog(Path(args.output)/"catalog.sqlite",
                json.loads(Path(args.selection_request).read_text()),
                Path(args.output)/"runs"/result["run_id"]/"selection")
            print(json.dumps({"selection_status":selection["status"], "exported":selection["exported"]}))
            if not selection["exported"]:
                return 2
        print(json.dumps({"run_id": result["run_id"], "output": args.output}))
        return 0 if all(d["status"] == "completed" for d in result["datasets"]) else 2
    except (ValueError, OSError) as exc:
        print("ERROR: {}".format(exc))
        return 1


def _cmd_propose_mapping(args):
    """Generate isolated, reviewable mappings without running ingestion."""
    import json
    from harmo.config.automap import run, discover, save
    try:
        manifest = discover(args.input_root) if args.discover else json.loads(Path(args.manifest).read_text())
        report = run(args.input_root, args.output, manifest["files"], manifest.get("metadata"))
        if args.discover:
            save(Path(args.output) / "discovery.json", manifest)
        print(json.dumps(report["counts"], sort_keys=True))
        return 0 if report["counts"]["accepted"] == len(manifest["files"]) else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("ERROR: {}".format(exc))
        return 1


def _cmd_ingest(args):
    # type: (argparse.Namespace) -> int
    """Run the data ingestion pipeline."""
    from harmo.config.loader import load_config

    config_path = args.config
    config = load_config(config_path)

    if args.dry_run:
        return _dry_run_ingest(config, config_path)

    try:
        from harmo.ingest.pipeline import run_ingest_pipeline
        run_ingest_pipeline(config_path)
    except Exception:
        logger.exception("Ingestion failed")
        return 1

    logger.info("Ingestion completed successfully.")
    return 0


def _cmd_ingest_session_hed(args):
    # type: (argparse.Namespace) -> int
    """Build and persist session_hed from config + HED JSON."""
    try:
        from harmo.ingest.session_hed import persist_session_hed

        df = persist_session_hed(
            config_path=args.config,
            hed_json_path=args.hed_json,
            json_key=args.json_key,
            target_table=args.table,
            dry_run=args.dry_run,
        )
    except Exception:
        logger.exception("session_hed ingestion failed")
        return 1

    if args.dry_run:
        print("DRY RUN: session_hed rows = {}".format(len(df)))
        print(df.head(10).to_string(index=False))
    else:
        print("OK: session_hed persisted to {} (rows={})".format(args.table, len(df)))
    return 0


def _cmd_ingest_bundle_semantics(args):
    """Materialize task and anatomy semantics from R into Iceberg."""
    import json

    from harmo.evidence.io import read_json
    from harmo.evidence.models import HarmoRecordingBundle
    from harmo.ingest.semantic_dimensions import (
        build_semantic_dimension_frames,
        persist_semantic_dimensions,
    )

    try:
        bundle = HarmoRecordingBundle.from_dict(read_json(args.bundle))
        frames = (
            build_semantic_dimension_frames(bundle)
            if args.dry_run
            else persist_semantic_dimensions(bundle)
        )
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1

    summary = {
        "mode": "dry-run" if args.dry_run else "persisted",
        "recording_id": bundle.recording_id,
        "tables": {
            name: {"rows": len(frame), "columns": list(frame.columns)}
            for name, frame in sorted(frames.items())
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_literature_semantic_enrichment(args):
    """Extract and adjudicate source-bound literature semantic claims."""
    from harmo.evidence.semantic_claims import run_semantic_enrichment

    try:
        report = run_semantic_enrichment(
            plan_path=args.plan,
            repository_root=args.repository_root,
            cache_dir=args.cache_dir,
            output_path=args.output,
            offline=args.offline,
            refresh=args.refresh,
        )
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    summary = report.summary
    print(
        "OK: sources={}/{} extracted_claims={}/{} "
        "enrichment_ready={} conflicts={} unresolved={}".format(
            summary["source_acquired"],
            summary["source_count"],
            summary["extracted_claim_count"],
            summary["claim_count"],
            summary["enrichment_ready_count"],
            summary["conflicting_decision_count"],
            summary["unresolved_decision_count"],
        )
    )
    if args.output:
        print("Report: {}".format(args.output))
    return 0


def _cmd_semantic_overlay(args):
    """Create a scope-bound overlay from an adjudicated literature report."""
    import json

    from harmo.evidence.io import read_json, sha256_file, write_json_atomic
    from harmo.evidence.semantic_claims import (
        SemanticEnrichmentReport,
        build_semantic_overlay,
    )

    try:
        selectors = {}
        for raw in args.selector:
            key, separator, value = raw.partition("=")
            if not separator or not key.strip() or not value.strip():
                raise ValueError(
                    "selectors must use a non-empty KEY=VALUE form"
                )
            key = key.strip()
            if key in selectors:
                raise ValueError("duplicate selector key: {}".format(key))
            selectors[key] = value.strip()
        report = SemanticEnrichmentReport.from_dict(read_json(args.report))
        overlay = build_semantic_overlay(
            report,
            report_sha256=sha256_file(args.report),
            study_id=args.study_id,
            target_scope_level=args.target_scope,
            target_scope_selector=selectors,
        )
        write_json_atomic(args.output, overlay)
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(
        json.dumps(
            {
                "output": args.output,
                "overlay_id": overlay.overlay_id,
                "study_id": overlay.study_id,
                "target_scope": overlay.target_scope_level,
                "entry_count": len(overlay.entries),
                "blocked_claim_count": len(overlay.blocked_claims),
                "skipped_claim_count": len(overlay.skipped_claims),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _cmd_semantic_coverage_comparison(args):
    """Compare file-only and paper-assisted semantic property coverage."""
    import json

    from harmo.evidence.io import read_json, sha256_file, write_json_atomic
    from harmo.evidence.semantic_claims import (
        SemanticEnrichmentReport,
        compare_semantic_coverage,
    )

    try:
        report = SemanticEnrichmentReport.from_dict(read_json(args.report))
        comparison = compare_semantic_coverage(
            report,
            report_sha256=sha256_file(args.report),
            study_ids=args.study_id,
        )
        write_json_atomic(args.output, comparison)
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(
        json.dumps(
            {
                "output": args.output,
                "summary": comparison.summary,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _cmd_validate_anatomy_crosswalk(args):
    """Verify an anatomy crosswalk against an immutable ontology asset."""
    import json

    from harmo.evidence.anatomy_crosswalk import verify_anatomy_crosswalk
    from harmo.evidence.io import write_json_atomic
    from harmo.evidence.run import current_repository_state

    try:
        verified = verify_anatomy_crosswalk(
            args.crosswalk,
            args.ontology_file,
            expected_crosswalk_sha256=args.expected_crosswalk_sha256,
        )
        crosswalk = verified.crosswalk
        repository_state = current_repository_state()
        result = {
            "schema_version": "1.0.0",
            "implementation_commit": repository_state.get("commit"),
            "code_dirty": repository_state.get("dirty"),
            "scope": (
                "crosswalk assertions verified against one checksum-fixed "
                "ontology release asset"
            ),
            "crosswalk_id": crosswalk.crosswalk_id,
            "crosswalk_sha256": verified.crosswalk_sha256,
            "ontology": crosswalk.target_release.ontology,
            "ontology_release_tag": crosswalk.target_release.release_tag,
            "ontology_version_iri": crosswalk.target_release.version_iri,
            "ontology_asset_sha256": (
                crosswalk.target_release.asset_sha256
            ),
            "verified_entries": len(crosswalk.entries),
            "verified_exact_matches": sum(
                entry.predicate == "exactMatch"
                for entry in crosswalk.entries
            ),
            "pass": True,
            "claim_boundary": (
                "This verifies ontology release identity, term existence, "
                "preferred labels, exact synonyms, and curated exactMatch "
                "assertions. It does not validate electrode placement, source "
                "signal anatomy, or physiological equivalence."
            ),
        }
        if args.output:
            write_json_atomic(args.output, result)
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_audit_semantic_coverage(args):
    """Audit task/anatomy coverage for the frozen active mappings."""
    import json

    from harmo.evidence.anatomy_crosswalk import verify_anatomy_crosswalk
    from harmo.evidence.io import write_json_atomic
    from harmo.semantic_coverage import (
        audit_active_mapping_semantics,
        build_semantic_audit_frames,
        persist_semantic_audit_frames,
    )

    try:
        verified = verify_anatomy_crosswalk(
            args.crosswalk,
            args.ontology_file,
            expected_crosswalk_sha256=args.expected_crosswalk_sha256,
        )
        audit = audit_active_mapping_semantics(
            args.repository_root,
            args.registry,
            args.mapping_outcomes,
            verified,
        )
        frames = (
            persist_semantic_audit_frames(audit, verified)
            if args.persist
            else build_semantic_audit_frames(audit, verified)
        )
        if args.output:
            write_json_atomic(args.output, audit)
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    result = {
        "mode": "persisted" if args.persist else "dry-run",
        "output": args.output,
        "summary": audit["summary"],
        "tables": {
            name: {"rows": len(frame), "columns": list(frame.columns)}
            for name, frame in sorted(frames.items())
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_materialize_atlas_records(args):
    """Materialize bounded real-source R/M/T/C/D records."""
    import json

    from harmo.atlas_materialization import materialize_ready_emg_mappings

    try:
        result = materialize_ready_emg_mappings(
            repository_root=args.repository_root,
            data_root=args.data_root,
            work_root=args.work_root,
            output_root=args.output,
            corpus_path=args.corpus,
            coverage_path=args.coverage,
            mapping_outcomes_path=args.mapping_outcomes,
            source_inventory_path=args.source_inventory,
            source_releases_path=args.source_releases,
            crosswalk_path=args.crosswalk,
            ontology_path=args.ontology_file,
            max_timepoints=args.max_timepoints,
            download_missing=args.download_missing,
            range_fetch_missing=args.range_fetch_missing,
            persist_iceberg=args.persist,
            semantic_overlay_paths=args.semantic_overlay,
        )
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    summary = {
        "output": args.output,
        "persisted_to_iceberg": args.persist,
        "summary": result["summary"],
        "duckdb_queries": result["duckdb_queries"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_audit_semantic_overlay_materialization(args):
    """Audit provenance-only overlay effects across real R/M/T/C/D."""
    import json

    from harmo.atlas_materialization import (
        audit_semantic_overlay_materialization,
    )
    from harmo.evidence.io import write_json_atomic

    try:
        result = audit_semantic_overlay_materialization(
            baseline_root=args.baseline_root,
            candidate_root=args.candidate_root,
            semantic_overlay_paths=args.semantic_overlay,
            dirty_control_root=args.dirty_control_root,
        )
        write_json_atomic(args.output, result)
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(
        json.dumps(
            {
                "output": args.output,
                "pass": result["pass"],
                "summary": result["summary"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _dry_run_ingest(config, config_path):
    # type: (dict, str) -> int
    """Show what would be ingested without executing."""
    import re

    aliases = config.get("input_dir_aliases", {})
    definitions = config.get("definitions", {})
    file_mappings = config.get("file_mappings", [])

    print("=== DRY RUN: {} ===\n".format(config_path))

    print("Definition tables:")
    for key, defn in definitions.items():
        path = defn.get("path", "")
        exists = os.path.exists(path)
        status = "OK" if exists else "MISSING"
        print("  [{}] {} -> {}".format(status, key, defn.get("table_name", "?")))

    print("\nFile mappings:")
    total_matched = 0
    total_skipped = 0
    total_quarantined = 0

    for mapping in file_mappings:
        name = mapping.get("name", "?")
        if mapping.get("enabled", True) is False:
            print("  [QUARANTINED] {}: {}".format(
                name, mapping.get("readiness_notes", "disabled in config")
            ))
            total_quarantined += 1
            continue
        alias = mapping.get("base_input_dir_alias", "")
        base_dir = aliases.get(alias)

        if not base_dir or not os.path.isdir(str(base_dir)):
            print("  [SKIP] {}: base dir not found ({})".format(name, base_dir))
            total_skipped += 1
            continue

        template = mapping.get("input_path_template", "")
        patterns = mapping.get("path_patterns", {})
        regex_str = template
        for ph, pattern in patterns.items():
            regex_str = regex_str.replace("{%s}" % ph, pattern)

        regex_str = regex_str.replace("/", r"[\\/]")
        try:
            compiled = re.compile(regex_str)
        except re.error:
            print("  [ERROR] {}: invalid regex".format(name))
            continue

        ext = os.path.splitext(template)[-1] or ".txt"
        base = Path(str(base_dir))
        matched = 0
        for p in base.rglob("*{}".format(ext)):
            rel = str(p.relative_to(base)).replace(os.sep, "/")
            if compiled.search(rel):
                matched += 1

        total_matched += matched
        table = mapping.get("iceberg_table", "?")
        print("  [{}] {} -> {} ({} files)".format(
            "OK" if matched > 0 else "WARN",
            name, table, matched
        ))

    print("\nSummary: {} files matched, {} mappings skipped, {} quarantined".format(
        total_matched, total_skipped, total_quarantined
    ))
    return 0


def _cmd_edit_markers(args):
    # type: (argparse.Namespace) -> int
    """Launch the web-based marker editor.

    Uses the opensim-model-viewer web UI ported into
    harmo.model.web_editor.  Falls back to the legacy PyVista GUI if
    ``--legacy`` is specified.
    """
    if getattr(args, "legacy", False):
        from harmo.model.marker_editor import main as gui_main

        argv = []
        if args.model:
            argv += ["--model", args.model]
        if args.output:
            argv += ["--output", args.output]
        if args.geometry:
            argv += ["--geometry", args.geometry]
        if args.verbose:
            argv.append("--verbose")
        return gui_main(argv)

    # --- Web-based marker editor (default) ---
    from harmo.model.web_editor.server import run_server

    model_path = args.model
    output_path = args.output

    # Auto-generate output path if not specified
    if model_path and not output_path:
        p = Path(model_path)
        output_path = str(p.parent / (p.stem + "_post_GUI" + p.suffix))

    return run_server(
        model_path=model_path,
        output_path=output_path,
        geometry_dir=getattr(args, "geometry", None),
        port=getattr(args, "port", 0),
        open_browser=True,
    )


def _cmd_analyze(args):
    # type: (argparse.Namespace) -> int
    """Run the OpenSim analysis pipeline."""
    if args.input:
        ok, reason = _validate_input_file(args.input)
        if not ok:
            logger.error("Input validation error: %s", reason)
            return 2

    if args.check_only:
        logger.info("Check-only mode: pre-checks passed.")
        return 0

    try:
        from harmo.analysis.runner import main as run_analysis
        run_analysis(
            config_path=args.config,
            label=args.label,
            g6_source_manifest=args.g6_source_manifest,
            g6_source_bundle_root=args.g6_source_bundle_root,
            g6_registry=args.g6_registry,
            g6_ledger=args.g6_ledger,
            resume=not args.no_resume,
        )
    except Exception:
        logger.exception("Analysis failed")
        return 1

    logger.info("Analysis completed successfully.")
    return 0


def _cmd_viz(args):
    # type: (argparse.Namespace) -> int
    """Launch the Streamlit visualization app."""
    try:
        import subprocess
        viz_module = Path(__file__).parent / "viz" / "app.py"
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", str(viz_module)],
            check=True,
        )
    except Exception:
        logger.exception("Visualization launch failed")
        return 1
    return 0


def _cmd_init(args):
    # type: (argparse.Namespace) -> int
    """Initialize a new study directory from a template."""
    target_dir = Path(args.directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    templates_dir = Path(__file__).parent / "config" / "templates"
    template_name = "{}_study.yaml".format(args.type)
    template_path = templates_dir / template_name

    if not template_path.exists():
        print("ERROR: Template not found: {}".format(template_path))
        return 1

    dest_config = target_dir / "config.yaml"
    shutil.copy2(str(template_path), str(dest_config))

    defs_dir = target_dir / "definitions"
    defs_dir.mkdir(exist_ok=True)

    print("Study initialized at: {}".format(target_dir))
    print("  config.yaml created from {} template".format(args.type))
    print("  definitions/ directory created")
    print("\nNext steps:")
    print("  1. Edit config.yaml with your study settings")
    print("  2. Add definition tables to definitions/")
    print("  3. Run: harmo validate --config {}".format(dest_config))
    return 0


# ---------------------------------------------------------------------------
# Input validation (from module_b.py)
# ---------------------------------------------------------------------------

def _validate_input_file(input_path):
    # type: (str) -> tuple
    """Basic validation for external software output files."""
    import csv

    path = Path(input_path)
    if not path.exists():
        return False, "File not found: {}".format(path)
    if path.is_dir():
        return False, "Path is a directory: {}".format(path)

    suffix = path.suffix.lower()
    if suffix not in {".csv", ".tsv", ".parquet"}:
        return False, "Unsupported extension: {} (use .csv/.tsv/.parquet)".format(suffix)

    try:
        if suffix == ".parquet":
            with path.open("rb") as f:
                start_magic = f.read(4)
                f.seek(-4, os.SEEK_END)
                end_magic = f.read(4)
            if start_magic != b"PAR1" or end_magic != b"PAR1":
                return False, "Invalid Parquet header/footer"
            if path.stat().st_size <= 12:
                return False, "Parquet file too small (possibly corrupted)"
            return True, "OK"

        sep = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, delimiter=sep)
            header = next(reader, None)
            first_row = next(reader, None)
    except Exception as exc:
        return False, "Failed to read file: {}".format(exc)

    if not header:
        return False, "No header row found"
    if any(not str(col).strip() for col in header):
        return False, "Header contains empty column names"
    if not first_row:
        return False, "No data rows found"

    def _looks_numeric(value):
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False

    if not any(_looks_numeric(cell) for cell in first_row):
        return False, "No numeric columns found"

    return True, "OK"


# ---------------------------------------------------------------------------
# r-analyze subcommand
# ---------------------------------------------------------------------------

def _cmd_r_analyze(args):
    # type: (argparse.Namespace) -> int
    """Run R analysis scripts or check the R environment."""
    from harmo.r_bridge import R_AVAILABLE

    # --check-env: report R availability and packages
    if getattr(args, "check_env", False):
        return _r_check_env()

    # --setup-posit: generate Posit Workbench integration
    if getattr(args, "setup_posit", False):
        return _r_setup_posit(args)

    # No script specified → list available scripts
    if not args.script:
        return _r_list_scripts()

    # Ensure R is available before trying to run anything
    if not R_AVAILABLE:
        from harmo.r_bridge._compat import check_r_available
        try:
            check_r_available("harmo r-analyze")
        except RuntimeError as exc:
            print(str(exc))
            return 1

    # Run the requested script
    from harmo.r_bridge.runner import run_r_script, run_analysis_pipeline

    warehouse = (
        args.warehouse
        or os.environ.get("HARMO_WAREHOUSE_PATH")
    )

    # Special handling for the full pipeline script
    if args.script in ("analysis_quickstart", "analysis_quickstart.R"):
        if not all([args.study_id, args.subject_id, args.session_id, args.run_id]):
            print(
                "ERROR: analysis_quickstart requires "
                "--study-id, --subject-id, --session-id, and --run-id"
            )
            return 1
        try:
            run_analysis_pipeline(
                warehouse_path=warehouse or "",
                study_id=args.study_id,
                subject_id=args.subject_id,
                session_id=args.session_id,
                run_id=args.run_id,
                n_synergies=args.n_synergies,
                emg_output_dir=args.emg_output_dir,
                synergy_output_dir=args.synergy_output_dir,
                timeout=args.timeout,
            )
        except RuntimeError as exc:
            print("ERROR: {}".format(exc))
            return 1
        print("R analysis pipeline completed successfully.")
        return 0

    # Generic script execution
    try:
        run_r_script(args.script, warehouse_path=warehouse, timeout=args.timeout)
    except (FileNotFoundError, RuntimeError) as exc:
        print("ERROR: {}".format(exc))
        return 1

    return 0


def _r_check_env():
    # type: () -> int
    """Check R environment and installed packages."""
    from harmo.r_bridge import R_AVAILABLE
    from harmo.r_bridge._compat import _probe_r_version

    print("=== R Environment Check ===\n")

    if not R_AVAILABLE:
        print("R (Rscript): NOT FOUND")
        print("\nR is not required for the core harmo pipeline.")
        print("Install R to enable advanced statistical analysis and visualization.")
        print("  https://cran.r-project.org/")
        return 1

    version = _probe_r_version() or "unknown"
    from harmo.r_bridge._compat import get_rscript_path
    print("R (Rscript): {}".format(get_rscript_path()))
    print("R version:   {}".format(version))

    from harmo.r_bridge._compat import check_r_packages
    installed, missing = check_r_packages()

    print("\nInstalled packages ({}/{}):".format(
        len(installed), len(installed) + len(missing)
    ))
    for pkg in installed:
        print("  [OK] {}".format(pkg))
    if missing:
        print("\nMissing packages ({}/{}):".format(
            len(missing), len(installed) + len(missing)
        ))
        for pkg in missing:
            print("  [  ] {}".format(pkg))
        print("\nInstall missing packages with:")
        print("  Rscript -e 'source(system.file(\"scripts/install_packages.R\", "
              "package=\"harmo\"))'")
        print("  or run:  harmo r-analyze install_packages")

    from harmo.r_bridge.posit_helpers import detect_posit_workbench
    if detect_posit_workbench():
        print("\nPosit Workbench: DETECTED")
    else:
        print("\nPosit Workbench: not detected (standalone R)")

    return 0 if not missing else 1


def _r_list_scripts():
    # type: () -> int
    """List available R scripts."""
    from harmo.r_bridge.runner import list_scripts

    scripts = list_scripts()
    if not scripts:
        print("No R scripts found.")
        return 1

    print("Available R analysis scripts:\n")
    for s in scripts:
        desc = "  {}".format(s["description"]) if s["description"] else ""
        print("  {:<30s}{}".format(s["name"], desc))

    print("\nUsage:  harmo r-analyze <script_name> [options]")
    print("Check:  harmo r-analyze --check-env")
    return 0


def _r_setup_posit(args):
    # type: (argparse.Namespace) -> int
    """Generate Posit Workbench integration."""
    from harmo.r_bridge.posit_helpers import (
        generate_rprofile_snippet,
        get_warehouse_path,
        create_r_project_link,
    )

    warehouse = args.warehouse or os.environ.get("HARMO_WAREHOUSE_PATH")
    if not warehouse:
        warehouse = get_warehouse_path()

    print("=== Posit Workbench Setup ===\n")

    # Create symlink
    try:
        link = create_r_project_link()
        print("R scripts linked: {}".format(link))
    except OSError as exc:
        print("WARNING: Could not create symlink: {}".format(exc))

    # Print .Rprofile snippet
    snippet = generate_rprofile_snippet(warehouse)
    print("\nAdd the following to your ~/.Rprofile:\n")
    print(snippet)
    print("Warehouse path: {}".format(warehouse))
    return 0


def _cmd_audit(args):
    """Audit an evidence manifest and print its lineage."""
    import json
    from harmo.evidence.audit import audit_manifest
    from harmo.evidence.io import read_json
    from harmo.evidence.models import EvidenceManifest

    try:
        result = audit_manifest(
            EvidenceManifest.from_dict(read_json(args.manifest)), args.artifact_id
        )
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_reproduce(args):
    """Compare expected and reproduced evidence contracts."""
    import json
    from harmo.evidence.audit import compare_contracts
    from harmo.evidence.io import read_json

    try:
        result = compare_contracts(
            read_json(args.expected), read_json(args.actual),
            atol=args.atol, rtol=args.rtol,
        )
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["matches"] else 1


def _cmd_export_atlas(args):
    """Export validated contracts as an EMG ATLAS exchange package."""
    import json
    from harmo.evidence.audit import export_atlas_exchange
    from harmo.evidence.io import read_json
    from harmo.evidence.models import (
        ComparabilityAssertion,
        EvidenceManifest,
        HarmoRecordingBundle,
        ReleaseDecision,
        TrustReport,
    )

    try:
        files = export_atlas_exchange(
            args.output,
            HarmoRecordingBundle.from_dict(read_json(args.bundle)),
            EvidenceManifest.from_dict(read_json(args.manifest)),
            TrustReport.from_dict(read_json(args.trust)),
            ComparabilityAssertion.from_dict(read_json(args.comparability)),
            ReleaseDecision.from_dict(read_json(args.release_decision)),
        )
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(json.dumps(files, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_qualify_atlas(args):
    """Create all evidence contracts and export only an allowed recording."""
    import json
    from harmo.evidence.workflow import qualify_wide_emg

    try:
        result = qualify_wide_emg(
            args.source,
            args.config,
            args.reference_bundle,
            args.reference_trust,
            args.output,
            args.source_asset,
        )
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_resolve_comparability(args):
    """Execute a validated common-phase transformation and re-evaluate."""
    import json
    from harmo.evidence.resolution import resolve_activation_timing

    try:
        result = resolve_activation_timing(
            args.left_bundle,
            args.left_trust,
            args.left_samples,
            args.right_bundle,
            args.right_trust,
            args.right_samples,
            args.config,
            args.output,
        )
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_trust_opensim(args):
    """Convert OpenSim validation metrics into explicit trust evidence."""
    import json
    from harmo.evidence.io import write_json_atomic
    from harmo.evidence.opensim import opensim_evidence_from_validation

    try:
        artifact, report = opensim_evidence_from_validation(
            args.validation,
            args.artifact_id,
            args.recording_id,
            args.model,
            args.output_file,
        )
        artifact_path = write_json_atomic(args.artifact, artifact)
        trust_path = write_json_atomic(args.trust, report)
    except Exception as exc:
        print("ERROR: {}".format(exc))
        return 1
    print(json.dumps({"artifact": artifact_path, "trust": trust_path}, indent=2))
    return 0


def _run_publication_command(command):
    """Run a publication validation command from the installed source tree."""
    import subprocess

    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts/paper/run_validation.py"), *command],
        check=False,
    )
    return int(completed.returncode)


def _cmd_opensim_mechanics_audit(args):
    command = [
        "opensim-mechanics-audit",
        "--protocol",
        args.protocol,
        "--source-manifest",
        args.source_manifest,
        "--data-root",
        args.data_root,
        "--opensim-python",
        args.opensim_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_mechanics_disposition(args):
    command = [
        "opensim-mechanics-disposition",
        "--policy",
        args.policy,
        "--output",
        args.output,
    ]
    return _run_publication_command(command)


def _cmd_opensim_whole_body_balance_audit(args):
    command = [
        "opensim-whole-body-balance-audit",
        "--protocol",
        args.protocol,
        "--source-manifest",
        args.source_manifest,
        "--prior-g6c-result",
        args.prior_g6c_result,
        "--data-root",
        args.data_root,
        "--opensim-python",
        args.opensim_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.download:
        command.append("--download")
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_mechanics_localization_audit(args):
    command = [
        "opensim-mechanics-localization-audit",
        "--protocol",
        args.protocol,
        "--g6c-protocol",
        args.g6c_protocol,
        "--source-manifest",
        args.source_manifest,
        "--prior-g6cs-result",
        args.prior_g6cs_result,
        "--data-root",
        args.data_root,
        "--opensim-python",
        args.opensim_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.download:
        command.append("--download")
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_state_reconstruction_audit(args):
    command = [
        "opensim-state-reconstruction-audit",
        "--protocol",
        args.protocol,
        "--source-manifest",
        args.source_manifest,
        "--s2-result",
        args.s2_result,
        "--s2-work-dir",
        args.s2_work_dir,
        "--data-root",
        args.data_root,
        "--opensim-python",
        args.opensim_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.download:
        command.append("--download")
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_moment_localization_audit(args):
    command = [
        "opensim-moment-localization-audit",
        "--protocol",
        args.protocol,
        "--source-manifest",
        args.source_manifest,
        "--s3-result",
        args.s3_result,
        "--s3-work-dir",
        args.s3_work_dir,
        "--s2-work-dir",
        args.s2_work_dir,
        "--data-root",
        args.data_root,
        "--analysis-python",
        args.analysis_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.download:
        command.append("--download")
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_rra_sensitivity_audit(args):
    command = [
        "opensim-rra-sensitivity-audit",
        "--protocol",
        args.protocol,
        "--source-manifest",
        args.source_manifest,
        "--s2-result",
        args.s2_result,
        "--s3-result",
        args.s3_result,
        "--s4-result",
        args.s4_result,
        "--s2-work-dir",
        args.s2_work_dir,
        "--data-root",
        args.data_root,
        "--opensim-python",
        args.opensim_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.download:
        command.append("--download")
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_structural_adequacy_audit(args):
    command = [
        "opensim-structural-adequacy-audit",
        "--protocol",
        args.protocol,
        "--source-manifest",
        args.source_manifest,
        "--s2-result",
        args.s2_result,
        "--s5-result",
        args.s5_result,
        "--s2-work-dir",
        args.s2_work_dir,
        "--data-root",
        args.data_root,
        "--study1-c3d",
        args.study1_c3d,
        "--study1-subject-info",
        args.study1_subject_info,
        "--analysis-python",
        args.analysis_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.download:
        command.append("--download")
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_structural_counterfactual_audit(args):
    command = [
        "opensim-structural-counterfactual-audit",
        "--protocol",
        args.protocol,
        "--s6-result",
        args.s6_result,
        "--study1-c3d",
        args.study1_c3d,
        "--study1-subject-info",
        args.study1_subject_info,
        "--analysis-python",
        args.analysis_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_structural_identifiability_audit(args):
    command = [
        "opensim-structural-identifiability-audit",
        "--protocol",
        args.protocol,
        "--source-manifest",
        args.source_manifest,
        "--s2-result",
        args.s2_result,
        "--s6-result",
        args.s6_result,
        "--s6b-result",
        args.s6b_result,
        "--data-root",
        args.data_root,
        "--analysis-python",
        args.analysis_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.download:
        command.append("--download")
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_bounded_arm_sensitivity_audit(args):
    command = [
        "opensim-bounded-arm-sensitivity-audit",
        "--protocol",
        args.protocol,
        "--s6-result",
        args.s6_result,
        "--s6b-result",
        args.s6b_result,
        "--s6c-result",
        args.s6c_result,
        "--s6-work-dir",
        args.s6_work_dir,
        "--analysis-python",
        args.analysis_python,
        "--work-dir",
        args.work_dir,
    ]
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


def _cmd_opensim_dynamic_personalization(args):
    command = [
        "opensim-dynamic-personalization",
        "--protocol",
        args.protocol,
        "--inventory",
        args.inventory,
        "--data-root",
        args.data_root,
        "--split",
        args.split,
        "--subject-id",
        args.subject_id,
        "--opensim-python",
        args.opensim_python,
        "--work-dir",
        args.work_dir,
        "--output-model",
        args.output_model,
        "--engine",
        args.engine,
    ]
    if args.plan_only:
        command.append("--plan-only")
    if args.hidden_metrics:
        command += ["--hidden-metrics", args.hidden_metrics]
    if args.output:
        command += ["--output", args.output]
    return _run_publication_command(command)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="harmo",
        description="Harmonized sEMG and biomechanics data pipeline",
    )
    subparsers = parser.add_subparsers(dest="command")

    p_mixed = subparsers.add_parser("mixed-batch", help="Route public PhysioNet/Figshare/Mendeley/Zenodo URLs into one conservative catalog")
    p_mixed.add_argument("--urls",required=True)
    p_mixed.add_argument("--output",required=True)
    p_mixed.add_argument("--r-library",default="")
    p_mixed.add_argument("--max-bytes",type=int,default=1024**3)
    p_mixed.add_argument("--max-records",type=int)
    p_mixed.add_argument("--selection-request")

    p_auto = subparsers.add_parser('harmonize', help='Bounded URL intake and automatic candidate-pair assessment; unresolved evidence remains held')
    p_auto.add_argument('--urls',required=True)
    p_auto.add_argument('--output',required=True)
    p_auto.add_argument('--purpose',action='append',choices=['activation-timing','amplitude-pooling'],required=True)
    p_auto.add_argument('--r-library',default='')
    p_auto.add_argument('--max-bytes',type=int,default=1024**3)
    p_auto.add_argument('--max-records',type=int)
    p_auto.add_argument('--max-comparisons',type=int,default=1000)
    p_auto.add_argument('--local-llm-model',help='Opt-in local Ollama model for source-wise WFDB unit review')
    p_auto.add_argument('--local-llm-port',type=int,default=11439)
    p_auto.add_argument('--max-unit-channels',type=int,default=32)
    p_auto.add_argument('--timing-profile',choices=['study38-gait120','study38-gait120-planned'],help='Explicit registered paper timing profile; planned uses catalog cycle plans and fresh validation')
    p_auto.add_argument('--source-cache')
    p_auto.add_argument('--timing-protocol')
    p_auto.add_argument('--article')
    p_auto.add_argument('--download',action='store_true')
    p_auto.add_argument('--resume',action='store_true')

    from harmo.general_workflow import configure as configure_general_batch, command as general_batch_command
    configure_general_batch(subparsers.add_parser('harmonize-batch',
        help='Resumable public-repository batch: source review, walking plans, diagnostic curves and explicit holds'))

    p_units = subparsers.add_parser('unit-review',help='Persist and inspect source-bound unit candidates; never approve scientific reuse')
    p_units.add_argument('action',choices=['import','show','confirm','hold'])
    p_units.add_argument('--catalog',required=True)
    p_units.add_argument('--queue')
    p_units.add_argument('--record')
    p_units.add_argument('--channel')
    p_units.add_argument('--reviewer')
    p_units.add_argument('--reason')

    p_session=subparsers.add_parser('review-session',help='Prepare evidence, apply explicit unit decisions, and reassess existing catalog')
    p_session.add_argument('action',choices=['prepare','apply','reassess'])
    p_session.add_argument('--catalog',required=True)
    p_session.add_argument('--output')
    p_session.add_argument('--packet')
    p_session.add_argument('--decisions')
    p_session.add_argument('--purpose',action='append',choices=['activation-timing','amplitude-pooling'])
    p_session.add_argument('--max-comparisons',type=int,default=1000)

    p_revision=subparsers.add_parser('contract-revision',help='Source-bound task/anatomy revisions and documentary processing notes; no scientific approval')
    p_revision.add_argument('action',choices=['prepare','apply'])
    p_revision.add_argument('--catalog',required=True)
    p_revision.add_argument('--record')
    p_revision.add_argument('--output')
    p_revision.add_argument('--proposal')

    p_transfer=subparsers.add_parser('timing-package',help='Package registered inputs or rebuild and revalidate them at a new location')
    p_transfer.add_argument('action',choices=['create','restore'])
    for flag in ('workflow','source-cache','protocol','article','package'):
        p_transfer.add_argument('--'+flag)
    p_transfer.add_argument('--output',required=True)
    p_transfer.add_argument('--resume',action='store_true')

    p_timing=subparsers.add_parser('timing-run',help='Registered catalog to cycle plans, fresh physiology, comparison and gated local export')
    for flag in ('catalog','source-cache','protocol','article','output'):
        p_timing.add_argument('--'+flag,required=True)

    p_plan=subparsers.add_parser('timing-plan',help='Bounded registered-cycle plan generation; no scientific approval')
    p_plan_source=p_plan.add_mutually_exclusive_group(required=True)
    p_plan_source.add_argument('--input')
    p_plan_source.add_argument('--catalog')
    p_plan.add_argument('--record')
    p_plan.add_argument('--channel')
    p_plan.add_argument('--assertions',help='Scoped evidence-backed facts; absent facts remain candidates')
    p_plan.add_argument('--protocol',required=True)
    p_plan.add_argument('--output',required=True)
    p_plan.add_argument('--execute-output')

    p_reuse = subparsers.add_parser("reuse-run", help="Run the registered two-study timing workflow with checked stage resume")
    p_reuse.add_argument("--urls", required=True, help="JSON array of the two registered versioned DOI URLs")
    p_reuse.add_argument("--source-cache", required=True)
    p_reuse.add_argument("--protocol", required=True)
    p_reuse.add_argument("--article", help="Optional cached reviewed XML; otherwise acquire the registered Europe PMC article with --download")
    p_reuse.add_argument("--output", required=True)
    p_reuse.add_argument("--resume", action="store_true")
    p_reuse.add_argument("--download", action="store_true")
    p_reuse.add_argument("--max-bytes", type=int, default=2*1024**3)

    p_export = subparsers.add_parser("public-export", help="Reassess purpose-specific catalog pairs and export only eligible signals")
    p_export.add_argument("--catalog", required=True)
    p_export.add_argument("--request", required=True)
    p_export.add_argument("--output", required=True)

    p_public = subparsers.add_parser("public-batch", help="Acquire PhysioNet URLs, convert with PhysioIO and accumulate locally")
    p_public.add_argument("--urls", required=True, help="JSON array of versioned dataset URLs")
    p_public.add_argument("--output", required=True, help="Local catalog directory")
    p_public.add_argument("--r-library", default="", help="Optional isolated R library containing PhysioIO and PhysioCore")
    p_public.add_argument("--max-bytes", type=int, default=1024**3)
    p_public.add_argument("--selection-request", help="Optional JSON purpose request; reassess and export after acquisition")
    p_public.add_argument("--max-records", type=int, default=None, help="Optional per-dataset validation cap; marks output partial")

    p_auto = subparsers.add_parser("propose-mapping", help="Generate and test bounded mapping candidates without ingestion")
    p_auto.add_argument("--input-root", required=True)
    auto_inputs = p_auto.add_mutually_exclusive_group(required=True)
    auto_inputs.add_argument("--manifest", help="JSON with explicit relative files and optional source-linked metadata")
    auto_inputs.add_argument("--discover", action="store_true", help="Inventory a bounded local folder and test candidate files")
    p_auto.add_argument("--output", required=True, help="New directory outside input root")
    p_auto.add_argument("--verbose", action="store_true")

    # -- validate --
    p_val = subparsers.add_parser("validate", help="Validate study config")
    p_val.add_argument("--config", required=True, help="Path to config.yaml")
    p_val.add_argument("--verbose", action="store_true")

    # -- ingest --
    p_ing = subparsers.add_parser("ingest", help="Run data ingestion")
    p_ing.add_argument("--config", required=True, help="Path to config.yaml")
    p_ing.add_argument("--dry-run", action="store_true",
                       help="Show what would be ingested without executing")
    p_ing.add_argument("--verbose", action="store_true")

    # -- ingest-session-hed --
    p_hed = subparsers.add_parser(
        "ingest-session-hed",
        help="Build and persist local.warehouse.session_hed from config + HED JSON",
    )
    p_hed.add_argument("--config", required=True, help="Path to config.yaml")
    p_hed.add_argument("--hed-json", help="Override path to HED JSON file")
    p_hed.add_argument("--json-key", default="HED", help="HED lookup key in JSON (default: HED)")
    p_hed.add_argument("--table", default="local.warehouse.session_hed", help="Target Iceberg table")
    p_hed.add_argument("--dry-run", action="store_true", help="Generate rows only, do not persist")
    p_hed.add_argument("--verbose", action="store_true")

    # -- ingest-bundle-semantics --
    p_semantics = subparsers.add_parser(
        "ingest-bundle-semantics",
        help="Materialize recording task and anatomy into Iceberg dimensions",
    )
    p_semantics.add_argument(
        "--bundle",
        required=True,
        help="Path to recording.bundle.json schema 1.2.0",
    )
    p_semantics.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and report semantic rows without writing Iceberg",
    )
    p_semantics.add_argument("--verbose", action="store_true")

    # -- validate-anatomy-crosswalk --
    p_crosswalk = subparsers.add_parser(
        "validate-anatomy-crosswalk",
        help="Verify a curated anatomy crosswalk against a frozen ontology asset",
    )
    p_crosswalk.add_argument("--crosswalk", required=True)
    p_crosswalk.add_argument("--ontology-file", required=True)
    p_crosswalk.add_argument("--expected-crosswalk-sha256")
    p_crosswalk.add_argument("--output")
    p_crosswalk.add_argument("--verbose", action="store_true")

    # -- audit-semantic-coverage --
    p_coverage = subparsers.add_parser(
        "audit-semantic-coverage",
        help="Audit task/anatomy coverage across the frozen active mappings",
    )
    p_coverage.add_argument("--repository-root", default=".")
    p_coverage.add_argument(
        "--registry", default="paper/validation_registry.yaml"
    )
    p_coverage.add_argument(
        "--mapping-outcomes",
        default="paper/results/validation/mapping_outcomes.json",
    )
    p_coverage.add_argument("--crosswalk", required=True)
    p_coverage.add_argument("--ontology-file", required=True)
    p_coverage.add_argument("--expected-crosswalk-sha256")
    p_coverage.add_argument("--output")
    p_coverage.add_argument(
        "--persist",
        action="store_true",
        help="Append verified ontology/crosswalk/coverage rows to Iceberg",
    )
    p_coverage.add_argument("--verbose", action="store_true")

    # -- literature-semantic-enrichment --
    p_literature = subparsers.add_parser(
        "literature-semantic-enrichment",
        help=(
            "Extract scope-aware metadata claims from publications and "
            "adjudicate them against repository evidence"
        ),
    )
    p_literature.add_argument(
        "--plan",
        default="paper/literature_semantic_enrichment.yaml",
        help="Frozen literature extraction and adjudication plan",
    )
    p_literature.add_argument("--repository-root", default=".")
    p_literature.add_argument(
        "--cache-dir",
        required=True,
        help="External cache for retrieved machine-readable full text",
    )
    p_literature.add_argument("--output")
    p_literature.add_argument(
        "--offline",
        action="store_true",
        help="Use only already cached remote sources",
    )
    p_literature.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch remote sources and verify their frozen digests",
    )
    p_literature.add_argument("--verbose", action="store_true")

    # -- semantic-overlay --
    p_overlay = subparsers.add_parser(
        "semantic-overlay",
        help=(
            "Create a target- and scope-bound overlay from accepted "
            "literature claims without mutating source data"
        ),
    )
    p_overlay.add_argument("--report", required=True)
    p_overlay.add_argument("--study-id", required=True)
    p_overlay.add_argument(
        "--target-scope",
        required=True,
        choices=(
            "dataset",
            "cohort",
            "subject",
            "session",
            "trial",
            "channel-group",
            "channel",
        ),
    )
    p_overlay.add_argument(
        "--selector",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Repeatable selector binding for the overlay target",
    )
    p_overlay.add_argument("--output", required=True)
    p_overlay.add_argument("--verbose", action="store_true")

    # -- semantic-coverage-comparison --
    p_semantic_comparison = subparsers.add_parser(
        "semantic-coverage-comparison",
        help="Compare file-only with paper-assisted semantic property coverage",
    )
    p_semantic_comparison.add_argument("--report", required=True)
    p_semantic_comparison.add_argument(
        "--study-id",
        action="append",
        required=True,
        help="Repeat once per fixed-denominator study",
    )
    p_semantic_comparison.add_argument("--output", required=True)
    p_semantic_comparison.add_argument("--verbose", action="store_true")

    # -- materialize-atlas-records --
    p_atlas_records = subparsers.add_parser(
        "materialize-atlas-records",
        help="Create bounded real-source R/M/T/C/D for six ready EMG mappings",
    )
    p_atlas_records.add_argument("--repository-root", default=".")
    p_atlas_records.add_argument("--data-root", required=True)
    p_atlas_records.add_argument("--work-root", required=True)
    p_atlas_records.add_argument("--output", required=True)
    p_atlas_records.add_argument(
        "--corpus", default="paper/validation_corpus.yaml"
    )
    p_atlas_records.add_argument(
        "--coverage",
        default="paper/results/validation/active_mapping_semantic_coverage.json",
    )
    p_atlas_records.add_argument(
        "--mapping-outcomes",
        default="paper/results/validation/mapping_outcomes.json",
    )
    p_atlas_records.add_argument(
        "--source-inventory",
        default="paper/results/validation/source_inventory.json",
        help="Frozen complete-archive verification ledger",
    )
    p_atlas_records.add_argument(
        "--source-releases",
        default="paper/source_releases.yaml",
        help="Frozen source identity, licence, and redistribution registry",
    )
    p_atlas_records.add_argument("--crosswalk", required=True)
    p_atlas_records.add_argument("--ontology-file", required=True)
    p_atlas_records.add_argument("--max-timepoints", type=int, default=100)
    p_atlas_records.add_argument(
        "--download-missing",
        action="store_true",
        help="Download and fully checksum-verify missing public archives",
    )
    p_atlas_records.add_argument(
        "--range-fetch-missing",
        action="store_true",
        help=(
            "Fetch selected ZIP members and bind them to the frozen "
            "complete-archive verification ledger"
        ),
    )
    p_atlas_records.add_argument(
        "--persist",
        action="store_true",
        help="Append atlas record/channel/task/comparison/release rows to Iceberg",
    )
    p_atlas_records.add_argument(
        "--semantic-overlay",
        action="append",
        default=[],
        help=(
            "Repeatable scope-bound semantic overlay to bind as a "
            "provenance-only EvidenceManifest input"
        ),
    )
    p_atlas_records.add_argument("--verbose", action="store_true")

    # -- audit-semantic-overlay-materialization --
    p_overlay_audit = subparsers.add_parser(
        "audit-semantic-overlay-materialization",
        help=(
            "Verify that semantic overlays add provenance without changing "
            "recording, trust, comparison, or release policy"
        ),
    )
    p_overlay_audit.add_argument("--baseline-root", required=True)
    p_overlay_audit.add_argument("--candidate-root", required=True)
    p_overlay_audit.add_argument(
        "--semantic-overlay",
        action="append",
        required=True,
        help="Repeat once for every study represented in the materialization",
    )
    p_overlay_audit.add_argument(
        "--dirty-control-root",
        help="Optional dirty-tree materialization used as a fail-closed control",
    )
    p_overlay_audit.add_argument("--output", required=True)
    p_overlay_audit.add_argument("--verbose", action="store_true")

    # -- edit-markers --
    p_gui = subparsers.add_parser("edit-markers",
                                  help="Launch marker editor GUI")
    p_gui.add_argument("--model", "-m", metavar="MODEL.osim")
    p_gui.add_argument("--output", "-o", metavar="OUTPUT.osim")
    p_gui.add_argument("--geometry", "-g", metavar="GEOMETRY_DIR")
    p_gui.add_argument("--port", type=int, default=0,
                       help="Port for the web server (0=auto)")
    p_gui.add_argument("--legacy", action="store_true",
                       help="Use legacy PyVista desktop GUI instead of web UI")
    p_gui.add_argument("--verbose", action="store_true")

    # -- analyze --
    p_ana = subparsers.add_parser("analyze", help="Run OpenSim analysis")
    p_ana.add_argument("--config", required=True, help="Path to config.yaml")
    p_ana.add_argument("--input", help="Path to external software output file")
    p_ana.add_argument("--label", default="default")
    p_ana.add_argument("--check-only", action="store_true")
    p_ana.add_argument(
        "--g6-source-manifest",
        help="checksum-bound 16-subject G6 static/model intake manifest",
    )
    p_ana.add_argument(
        "--g6-source-bundle-root",
        help="artifact root for --g6-source-manifest",
    )
    p_ana.add_argument(
        "--g6-registry",
        default="paper/validation_registry.yaml",
        help="frozen G6 validation registry",
    )
    p_ana.add_argument(
        "--g6-ledger",
        default="paper/results/validation/opensim_quality.json",
        help="verified fixed-denominator G6 source ledger",
    )
    p_ana.add_argument(
        "--no-resume",
        action="store_true",
        help="rerun complete G6 validation checkpoints",
    )
    p_ana.add_argument("--verbose", action="store_true")

    # -- viz --
    p_viz = subparsers.add_parser("viz", help="Launch visualization app")
    p_viz.add_argument("--verbose", action="store_true")

    # -- init --
    p_init = subparsers.add_parser("init", help="Initialize new study directory")
    p_init.add_argument("--type", choices=["c3d", "mat", "csv"], default="csv",
                        help="Template type (default: csv)")
    p_init.add_argument("directory", help="Target directory")
    p_init.add_argument("--verbose", action="store_true")

    # -- r-analyze --
    p_r = subparsers.add_parser(
        "r-analyze",
        help="Run R analysis scripts (optional — requires R)",
    )
    p_r.add_argument(
        "script",
        nargs="?",
        help="R script name to run (e.g. analysis_quickstart). "
             "Omit to list available scripts.",
    )
    p_r.add_argument("--warehouse", help="Path to Iceberg warehouse directory")
    p_r.add_argument(
        "--study-id", type=int, help="Study ID for the analysis pipeline"
    )
    p_r.add_argument(
        "--subject-id", type=int, help="Subject ID for the analysis pipeline"
    )
    p_r.add_argument("--session-id", help="Session ID (e.g. ses-V3-T5)")
    p_r.add_argument(
        "--run-id", help="OpenSim run ID (e.g. 20260209_0223_default)"
    )
    p_r.add_argument(
        "--n-synergies", type=int, default=4,
        help="Number of muscle synergies (default: 4)",
    )
    p_r.add_argument("--emg-output-dir", help="Output dir for EMG plots")
    p_r.add_argument("--synergy-output-dir", help="Output dir for synergy plots")
    p_r.add_argument("--timeout", type=int, help="Timeout in seconds")
    p_r.add_argument(
        "--check-env", action="store_true",
        help="Check R environment and installed packages, then exit",
    )
    p_r.add_argument(
        "--setup-posit", action="store_true",
        help="Generate Posit Workbench integration (.Rprofile snippet)",
    )
    p_r.add_argument("--verbose", action="store_true")

    p_audit = subparsers.add_parser(
        "audit", help="Trace artifacts in a trustworthy AI-ready evidence manifest"
    )
    p_audit.add_argument("--manifest", required=True, help="Evidence manifest JSON")
    p_audit.add_argument("--artifact-id", help="Limit the audit to one artifact")
    p_audit.add_argument("--verbose", action="store_true")

    p_rep = subparsers.add_parser(
        "reproduce", help="Compare expected and reproduced evidence contracts"
    )
    p_rep.add_argument("--expected", required=True)
    p_rep.add_argument("--actual", required=True)
    p_rep.add_argument("--atol", type=float, default=1e-9)
    p_rep.add_argument("--rtol", type=float, default=1e-7)
    p_rep.add_argument("--verbose", action="store_true")

    p_atlas = subparsers.add_parser(
        "export-atlas", help="Create a validated EMG ATLAS exchange package"
    )
    p_atlas.add_argument("--bundle", required=True)
    p_atlas.add_argument("--manifest", required=True)
    p_atlas.add_argument("--trust", required=True)
    p_atlas.add_argument("--comparability", required=True)
    p_atlas.add_argument("--release-decision", required=True)
    p_atlas.add_argument("--output", required=True)
    p_atlas.add_argument("--verbose", action="store_true")

    p_qualify = subparsers.add_parser(
        "qualify-atlas",
        help="Build R/M/T/C/D evidence from a wide EMG CSV/TSV and gate export",
    )
    p_qualify.add_argument("--source", required=True, help="Wide EMG CSV or TSV")
    p_qualify.add_argument(
        "--source-asset",
        help="Optional upstream raw asset retained as the recording source",
    )
    p_qualify.add_argument(
        "--config", required=True, help="Evidence qualification JSON"
    )
    p_qualify.add_argument(
        "--reference-bundle",
        required=True,
        help="Previously qualified reference recording bundle",
    )
    p_qualify.add_argument(
        "--reference-trust",
        required=True,
        help="Trust report for the reference recording",
    )
    p_qualify.add_argument("--output", required=True)
    p_qualify.add_argument("--verbose", action="store_true")

    p_resolve = subparsers.add_parser(
        "resolve-comparability",
        help=(
            "Create a validated common gait-cycle representation and "
            "re-evaluate activation-timing comparability"
        ),
    )
    p_resolve.add_argument("--left-bundle", required=True)
    p_resolve.add_argument("--left-trust", required=True)
    p_resolve.add_argument("--left-samples", required=True)
    p_resolve.add_argument("--right-bundle", required=True)
    p_resolve.add_argument("--right-trust", required=True)
    p_resolve.add_argument("--right-samples", required=True)
    p_resolve.add_argument("--config", required=True)
    p_resolve.add_argument("--output", required=True)
    p_resolve.add_argument("--verbose", action="store_true")

    p_osim = subparsers.add_parser(
        "trust-opensim", help="Turn validation.json into threshold-level trust evidence"
    )
    p_osim.add_argument("--validation", required=True)
    p_osim.add_argument("--artifact-id", required=True)
    p_osim.add_argument("--recording-id", required=True)
    p_osim.add_argument("--model", required=True)
    p_osim.add_argument("--output-file", action="append", default=[])
    p_osim.add_argument("--artifact", required=True, help="Output artifact contract JSON")
    p_osim.add_argument("--trust", required=True, help="Output trust report JSON")
    p_osim.add_argument("--verbose", action="store_true")

    p_g6c = subparsers.add_parser(
        "opensim-mechanics-audit",
        help="Run the checksum-bound G6c mechanics audit",
    )
    p_g6c.add_argument("--protocol", default="paper/g6c_mechanics_audit_protocol.yaml")
    p_g6c.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    p_g6c.add_argument("--data-root", required=True)
    p_g6c.add_argument("--opensim-python", required=True)
    p_g6c.add_argument("--work-dir", required=True)
    p_g6c.add_argument("--output")
    p_g6c.add_argument("--verbose", action="store_true")

    p_g6_disposition = subparsers.add_parser(
        "opensim-mechanics-disposition",
        help="Close G6 reporting while retaining its failed scientific endpoint",
    )
    p_g6_disposition.add_argument(
        "--policy",
        default="paper/g6_mechanics_disposition_policy.yaml",
    )
    p_g6_disposition.add_argument(
        "--output",
        default="paper/results/validation/g6_mechanics_disposition.json",
    )
    p_g6_disposition.add_argument("--verbose", action="store_true")

    p_g6cs = subparsers.add_parser(
        "opensim-whole-body-balance-audit",
        help="Run the checksum-bound G6c-S whole-body balance audit",
    )
    p_g6cs.add_argument(
        "--protocol", default="paper/g6c_whole_body_balance_protocol.yaml"
    )
    p_g6cs.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    p_g6cs.add_argument(
        "--prior-g6c-result",
        default="paper/results/validation/g6c_mechanics_audit.json",
    )
    p_g6cs.add_argument("--data-root", required=True)
    p_g6cs.add_argument("--opensim-python", required=True)
    p_g6cs.add_argument("--work-dir", required=True)
    p_g6cs.add_argument("--download", action="store_true")
    p_g6cs.add_argument("--output")
    p_g6cs.add_argument("--verbose", action="store_true")

    p_g6cs2 = subparsers.add_parser(
        "opensim-mechanics-localization-audit",
        help="Run the checksum-bound G6c-S2 mechanics localization",
    )
    p_g6cs2.add_argument(
        "--protocol", default="paper/g6c_s2_mechanics_localization_protocol.yaml"
    )
    p_g6cs2.add_argument(
        "--g6c-protocol", default="paper/g6c_mechanics_audit_protocol.yaml"
    )
    p_g6cs2.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    p_g6cs2.add_argument(
        "--prior-g6cs-result",
        default="paper/results/validation/g6c_whole_body_balance.json",
    )
    p_g6cs2.add_argument("--data-root", required=True)
    p_g6cs2.add_argument("--opensim-python", required=True)
    p_g6cs2.add_argument("--work-dir", required=True)
    p_g6cs2.add_argument("--download", action="store_true")
    p_g6cs2.add_argument("--output")
    p_g6cs2.add_argument("--verbose", action="store_true")

    p_g6cs3 = subparsers.add_parser(
        "opensim-state-reconstruction-audit",
        help="Run the checksum-bound G6c-S3 RRA-native state audit",
    )
    p_g6cs3.add_argument(
        "--protocol", default="paper/g6c_s3_state_reconstruction_protocol.yaml"
    )
    p_g6cs3.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    p_g6cs3.add_argument(
        "--s2-result",
        default="paper/results/validation/g6c_s2_mechanics_localization.json",
    )
    p_g6cs3.add_argument("--s2-work-dir", required=True)
    p_g6cs3.add_argument("--data-root", required=True)
    p_g6cs3.add_argument("--opensim-python", required=True)
    p_g6cs3.add_argument("--work-dir", required=True)
    p_g6cs3.add_argument("--download", action="store_true")
    p_g6cs3.add_argument("--output")
    p_g6cs3.add_argument("--verbose", action="store_true")

    p_g6cs4 = subparsers.add_parser(
        "opensim-moment-localization-audit",
        help="Run the checksum-bound G6c-S4 residual-moment audit",
    )
    p_g6cs4.add_argument(
        "--protocol", default="paper/g6c_s4_moment_localization_protocol.yaml"
    )
    p_g6cs4.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    p_g6cs4.add_argument(
        "--s3-result",
        default="paper/results/validation/g6c_s3_state_reconstruction.json",
    )
    p_g6cs4.add_argument("--s3-work-dir", required=True)
    p_g6cs4.add_argument("--s2-work-dir", required=True)
    p_g6cs4.add_argument("--data-root", required=True)
    p_g6cs4.add_argument("--analysis-python", required=True)
    p_g6cs4.add_argument("--work-dir", required=True)
    p_g6cs4.add_argument("--download", action="store_true")
    p_g6cs4.add_argument("--output")
    p_g6cs4.add_argument("--verbose", action="store_true")

    p_g6cs5 = subparsers.add_parser(
        "opensim-rra-sensitivity-audit",
        help="Run the checksum-bound G6c-S5 RRA sensitivity factorial",
    )
    p_g6cs5.add_argument(
        "--protocol", default="paper/g6c_s5_rra_sensitivity_protocol.yaml"
    )
    p_g6cs5.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    p_g6cs5.add_argument(
        "--s2-result",
        default="paper/results/validation/g6c_s2_mechanics_localization.json",
    )
    p_g6cs5.add_argument(
        "--s3-result",
        default="paper/results/validation/g6c_s3_state_reconstruction.json",
    )
    p_g6cs5.add_argument(
        "--s4-result",
        default="paper/results/validation/g6c_s4_moment_localization.json",
    )
    p_g6cs5.add_argument("--s2-work-dir", required=True)
    p_g6cs5.add_argument("--data-root", required=True)
    p_g6cs5.add_argument("--opensim-python", required=True)
    p_g6cs5.add_argument("--work-dir", required=True)
    p_g6cs5.add_argument("--download", action="store_true")
    p_g6cs5.add_argument("--output")
    p_g6cs5.add_argument("--verbose", action="store_true")

    p_g6cs6 = subparsers.add_parser(
        "opensim-structural-adequacy-audit",
        help="Run the checksum-bound G6c-S6 structural-adequacy audit",
    )
    p_g6cs6.add_argument(
        "--protocol", default="paper/g6c_s6_structural_adequacy_protocol.yaml"
    )
    p_g6cs6.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    p_g6cs6.add_argument(
        "--s2-result",
        default="paper/results/validation/g6c_s2_mechanics_localization.json",
    )
    p_g6cs6.add_argument(
        "--s5-result",
        default="paper/results/validation/g6c_s5_rra_sensitivity.json",
    )
    p_g6cs6.add_argument("--s2-work-dir", required=True)
    p_g6cs6.add_argument("--data-root", required=True)
    p_g6cs6.add_argument(
        "--study1-c3d",
        default="studies/study_1/notebook/Ref01_sss_le_shoes05.c3d",
    )
    p_g6cs6.add_argument(
        "--study1-subject-info",
        default="studies/study_1/definitions/subject_info.csv",
    )
    p_g6cs6.add_argument("--analysis-python", required=True)
    p_g6cs6.add_argument("--work-dir", required=True)
    p_g6cs6.add_argument("--download", action="store_true")
    p_g6cs6.add_argument("--output")
    p_g6cs6.add_argument("--verbose", action="store_true")

    p_g6cs6b = subparsers.add_parser(
        "opensim-structural-counterfactual-audit",
        help="Run the checksum-bound G6c-S6b paired structural counterfactual",
    )
    p_g6cs6b.add_argument(
        "--protocol", default="paper/g6c_s6b_structural_contrast_protocol.yaml"
    )
    p_g6cs6b.add_argument(
        "--s6-result",
        default="paper/results/validation/g6c_s6_structural_adequacy.json",
    )
    p_g6cs6b.add_argument(
        "--study1-c3d",
        default="studies/study_1/notebook/Ref01_sss_le_shoes05.c3d",
    )
    p_g6cs6b.add_argument(
        "--study1-subject-info",
        default="studies/study_1/definitions/subject_info.csv",
    )
    p_g6cs6b.add_argument("--analysis-python", required=True)
    p_g6cs6b.add_argument("--work-dir", required=True)
    p_g6cs6b.add_argument("--output")
    p_g6cs6b.add_argument("--verbose", action="store_true")

    p_g6cs6c = subparsers.add_parser(
        "opensim-structural-identifiability-audit",
        help="Run the checksum-bound G6c-S6c structural identifiability preflight",
    )
    p_g6cs6c.add_argument(
        "--protocol",
        default="paper/g6c_s6c_structural_identifiability_protocol.yaml",
    )
    p_g6cs6c.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    p_g6cs6c.add_argument(
        "--s2-result",
        default="paper/results/validation/g6c_s2_mechanics_localization.json",
    )
    p_g6cs6c.add_argument(
        "--s6-result",
        default="paper/results/validation/g6c_s6_structural_adequacy.json",
    )
    p_g6cs6c.add_argument(
        "--s6b-result",
        default="paper/results/validation/g6c_s6b_structural_contrast.json",
    )
    p_g6cs6c.add_argument("--data-root", required=True)
    p_g6cs6c.add_argument("--analysis-python", required=True)
    p_g6cs6c.add_argument("--work-dir", required=True)
    p_g6cs6c.add_argument("--download", action="store_true")
    p_g6cs6c.add_argument("--output")
    p_g6cs6c.add_argument("--verbose", action="store_true")

    p_g6cs6d = subparsers.add_parser(
        "opensim-bounded-arm-sensitivity-audit",
        help="Run the exploratory G6c-S6d bounded harmonic sensitivity",
    )
    p_g6cs6d.add_argument(
        "--protocol",
        default="paper/g6c_s6d_bounded_arm_sensitivity_protocol.yaml",
    )
    p_g6cs6d.add_argument(
        "--s6-result",
        default="paper/results/validation/g6c_s6_structural_adequacy.json",
    )
    p_g6cs6d.add_argument(
        "--s6b-result",
        default="paper/results/validation/g6c_s6b_structural_contrast.json",
    )
    p_g6cs6d.add_argument(
        "--s6c-result",
        default=(
            "paper/results/validation/"
            "g6c_s6c_structural_identifiability.json"
        ),
    )
    p_g6cs6d.add_argument("--s6-work-dir", required=True)
    p_g6cs6d.add_argument("--analysis-python", required=True)
    p_g6cs6d.add_argument("--work-dir", required=True)
    p_g6cs6d.add_argument("--output")
    p_g6cs6d.add_argument("--verbose", action="store_true")

    p_g6d = subparsers.add_parser(
        "opensim-dynamic-personalization",
        help="Run leakage-resistant G6d/G6e dynamic-only personalization",
    )
    p_g6d.add_argument("--protocol", default="paper/g6_dynamic_personalization_protocol.yaml")
    p_g6d.add_argument("--inventory", required=True)
    p_g6d.add_argument("--data-root", required=True)
    p_g6d.add_argument(
        "--split", choices=("development", "blind", "study31"), required=True
    )
    p_g6d.add_argument("--subject-id", required=True)
    p_g6d.add_argument("--opensim-python", required=True)
    p_g6d.add_argument("--work-dir", required=True)
    p_g6d.add_argument("--output-model", required=True)
    p_g6d.add_argument(
        "--engine", choices=("auto", "addbiomechanics", "harmo-native"), default="auto"
    )
    p_g6d.add_argument("--plan-only", action="store_true")
    p_g6d.add_argument("--hidden-metrics")
    p_g6d.add_argument("--output")
    p_g6d.add_argument("--verbose", action="store_true")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        raise SystemExit(2)

    _setup_logging(getattr(args, "verbose", False))

    dispatch = {
        "harmonize-batch": general_batch_command,
        "harmonize": _cmd_automatic,
        "unit-review": _cmd_unit_review,
        "review-session": _cmd_review_session,
        "contract-revision": _cmd_contract_revision,
        "timing-plan": _cmd_timing_plan,
        "timing-run": _cmd_timing_run,
        "timing-package": _cmd_timing_package,
        "mixed-batch": _cmd_mixed_batch,
        "reuse-run": _cmd_reuse_run,
        "validate": _cmd_validate,
        "ingest": _cmd_ingest,
        "propose-mapping": _cmd_propose_mapping,
        "public-batch": _cmd_public_batch,
        "public-export": _cmd_public_export,
        "ingest-session-hed": _cmd_ingest_session_hed,
        "ingest-bundle-semantics": _cmd_ingest_bundle_semantics,
        "validate-anatomy-crosswalk": _cmd_validate_anatomy_crosswalk,
        "audit-semantic-coverage": _cmd_audit_semantic_coverage,
        "literature-semantic-enrichment": (
            _cmd_literature_semantic_enrichment
        ),
        "semantic-overlay": _cmd_semantic_overlay,
        "semantic-coverage-comparison": (
            _cmd_semantic_coverage_comparison
        ),
        "materialize-atlas-records": _cmd_materialize_atlas_records,
        "audit-semantic-overlay-materialization": (
            _cmd_audit_semantic_overlay_materialization
        ),
        "edit-markers": _cmd_edit_markers,
        "analyze": _cmd_analyze,
        "viz": _cmd_viz,
        "init": _cmd_init,
        "r-analyze": _cmd_r_analyze,
        "audit": _cmd_audit,
        "reproduce": _cmd_reproduce,
        "export-atlas": _cmd_export_atlas,
        "qualify-atlas": _cmd_qualify_atlas,
        "resolve-comparability": _cmd_resolve_comparability,
        "trust-opensim": _cmd_trust_opensim,
        "opensim-mechanics-audit": _cmd_opensim_mechanics_audit,
        "opensim-mechanics-disposition": _cmd_opensim_mechanics_disposition,
        "opensim-whole-body-balance-audit": _cmd_opensim_whole_body_balance_audit,
        "opensim-mechanics-localization-audit": (
            _cmd_opensim_mechanics_localization_audit
        ),
        "opensim-state-reconstruction-audit": (
            _cmd_opensim_state_reconstruction_audit
        ),
        "opensim-moment-localization-audit": (
            _cmd_opensim_moment_localization_audit
        ),
        "opensim-rra-sensitivity-audit": _cmd_opensim_rra_sensitivity_audit,
        "opensim-structural-adequacy-audit": (
            _cmd_opensim_structural_adequacy_audit
        ),
        "opensim-structural-counterfactual-audit": (
            _cmd_opensim_structural_counterfactual_audit
        ),
        "opensim-structural-identifiability-audit": (
            _cmd_opensim_structural_identifiability_audit
        ),
        "opensim-bounded-arm-sensitivity-audit": (
            _cmd_opensim_bounded_arm_sensitivity_audit
        ),
        "opensim-dynamic-personalization": _cmd_opensim_dynamic_personalization,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        raise SystemExit(2)

    raise SystemExit(handler(args))


if __name__ == "__main__":
    main()
